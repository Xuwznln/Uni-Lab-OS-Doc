"""Edge<->Sim 通信全链路集成测试（无需 Isaac Sim、无需 ROS）。

用零依赖的 MockSimServer 充当 Sim 侧，验证 IsaacSimGateway 的：
- 启动握手：hello -> world.create -> (world.create.ack) -> session.start
- 链路 A：asset.upsert 在 world ready 后送达
- 链路 B：joint_state.stream 送达
- 链路 C：attach.request -> attach.ack(ok)
- 链路 D：collision.event 回调
- 链路 E：joint_command.set -> 处理器执行 -> joint_command.ack(accepted)
"""

from __future__ import annotations

import sys
import os
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from unilabos.sim.isaac_gateway import IsaacSimGateway, get_active_gateway, set_active_gateway
from unilabos.sim.mock_sim_server import MockSimServer


class TestIsaacGatewayLink(unittest.TestCase):
    def setUp(self) -> None:
        self.server = MockSimServer(host="127.0.0.1", port=0)
        self.server.start()
        self.gateway = IsaacSimGateway(
            endpoint=self.server.endpoint,
            auto_bootstrap=True,
            reconnect_backoff_ms=200,
            heartbeat_interval_ms=5000,
        )

    def tearDown(self) -> None:
        try:
            self.gateway.stop()
        finally:
            self.server.stop()
        set_active_gateway(None)

    def test_full_link(self) -> None:
        self.gateway.start()

        # 启动握手
        self.assertTrue(self.server.wait_for("hello", timeout=5.0), "hello 未送达")
        self.assertTrue(self.server.wait_for("world.create", timeout=5.0), "world.create 未送达")
        self.assertTrue(self.server.wait_for("session.start", timeout=5.0),
                        "session.start 未在 world.create.ack 后送达")

        # world ready 门控
        self.assertTrue(self.gateway.wait_world_ready(timeout=5.0), "world 未就绪")

        # 单例可被业务侧获取
        self.assertIs(get_active_gateway(), self.gateway)

        # 链路 A：asset.upsert
        self.gateway.upsert_asset({
            "asset_id": "ur5_left",
            "asset_kind": "device",
            "format": "urdf",
            "source_uri": "file:///tmp/ur5.urdf",
            "prim_path": "/World/Devices/ur5_left",
            "pose": {
                "frame_id": "world",
                "position_m": {"x": 0.0, "y": 0.0, "z": 0.0},
                "orientation_xyzw": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            "metadata": {"id": "ur5_left"},
            "replace_if_exists": True,
        })
        self.assertTrue(self.server.wait_for("asset.upsert", timeout=5.0), "asset.upsert 未送达")
        self.assertEqual(self.server.payload_of("asset.upsert").get("asset_id"), "ur5_left")

        # 链路 B：joint_state.stream
        self.gateway.publish_joint_state(
            device_id="ur5_left",
            base_frame="world",
            joint_names=["shoulder_pan_joint", "shoulder_lift_joint"],
            joint_positions_rad=[0.1, -1.2],
        )
        self.assertTrue(self.server.wait_for("joint_state.stream", timeout=5.0),
                        "joint_state.stream 未送达")

        # 链路 C：attach.request -> attach.ack
        ack = self.gateway.request_attach(
            attachment_id="att_1",
            child_asset_id="material_tube_001",
            parent_asset_id="ur5_left",
            parent_link="tool0",
            relative_pose={
                "position_m": {"x": 0.0, "y": 0.0, "z": 0.02},
                "orientation_xyzw": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            wait_ack=True,
            timeout_s=5.0,
        )
        self.assertIsNotNone(ack, "attach.ack 超时")
        self.assertEqual(ack.get("status"), "ok")
        self.assertEqual(ack.get("attachment_id"), "att_1")

        # 链路 D：collision.event 回调
        collision_seen = threading.Event()
        captured: dict = {}

        def _on_collision(payload: dict) -> None:
            captured["payload"] = payload
            collision_seen.set()

        self.gateway.add_collision_handler(_on_collision)
        self.server.push_collision([
            {"a_asset_id": "ur5_left/tool0", "b_asset_id": "material_tube_001"},
        ])
        self.assertTrue(collision_seen.wait(timeout=5.0), "collision.event 回调未触发")
        self.assertIn("pairs", captured["payload"])

        # 链路 E：joint_command.set -> 处理器 -> joint_command.ack(accepted)
        command_seen = threading.Event()

        def _on_joint_command(payload: dict) -> dict:
            command_seen.set()
            return {"status": "accepted",
                    "executed_positions_rad": payload.get("target_positions_rad", [])}

        self.gateway.add_joint_command_handler(_on_joint_command)
        self.server.push_joint_command(
            device_id="ur5_left",
            joint_names=["shoulder_pan_joint", "shoulder_lift_joint"],
            target_positions_rad=[0.12, -1.30],
        )
        self.assertTrue(command_seen.wait(timeout=5.0), "joint_command.set 处理器未触发")
        self.assertTrue(self.server.wait_for("joint_command.ack", timeout=5.0),
                        "joint_command.ack 未回流到 Sim")
        self.assertEqual(self.server.payload_of("joint_command.ack").get("status"), "accepted")

    def test_joint_command_rejected_without_handler(self) -> None:
        """未注册处理器时，反向关节命令应被拒绝并回 ack(rejected)。"""
        self.gateway.start()
        self.assertTrue(self.gateway.wait_world_ready(timeout=5.0))

        self.server.push_joint_command(
            device_id="ur5_left",
            joint_names=["j1"],
            target_positions_rad=[0.0],
        )
        self.assertTrue(self.server.wait_for("joint_command.ack", timeout=5.0))
        self.assertEqual(self.server.payload_of("joint_command.ack").get("status"), "rejected")

    def test_upsert_scene_urdf(self) -> None:
        """整场景 URDF：写出 scene.urdf 文件，并以 file:// URI 发出 device 类 asset.upsert。"""
        import os
        import tempfile

        self.gateway.start()
        self.assertTrue(self.gateway.wait_world_ready(timeout=5.0))

        # 含 file:// mesh 的小 URDF，验证反斜杠规整不报错
        urdf = (
            '<?xml version="1.0"?>\n'
            '<robot name="full_dev">\n'
            '  <link name="world"/>\n'
            '  <link name="dev1_base">\n'
            '    <visual><geometry>'
            '<mesh filename="file://C:\\meshes\\dev1\\base.STL"/>'
            '</geometry></visual>\n'
            '  </link>\n'
            '</robot>\n'
        )
        uri = self.gateway.upsert_scene_urdf(urdf)
        self.assertIsNotNone(uri)
        self.assertTrue(uri.startswith("file:///"), f"unexpected uri: {uri}")

        scene_path = os.path.join(tempfile.gettempdir(), "unilab_sim_scene.urdf")
        self.assertTrue(os.path.exists(scene_path), "scene.urdf 未写出")
        written = open(scene_path, encoding="utf-8").read()
        # 反斜杠应被规整为正斜杠
        self.assertIn('filename="file://C:/meshes/dev1/base.STL"', written)
        self.assertNotIn("\\meshes\\", written)

        self.assertTrue(self.server.wait_for("asset.upsert", timeout=5.0), "asset.upsert 未送达")
        payload = self.server.payload_of("asset.upsert")
        self.assertEqual(payload.get("asset_kind"), "device")
        self.assertEqual(payload.get("format"), "urdf")
        self.assertEqual(payload.get("asset_id"), "full_dev")
        self.assertTrue(str(payload.get("source_uri", "")).startswith("file:///"))


if __name__ == "__main__":
    unittest.main()
