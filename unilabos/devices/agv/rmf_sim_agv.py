"""`agv.RMFSim`：OS 内部 mock 小车节点（#22 §0.1 / #18 §10.3）。

作为 OS graph 的**独立 root 节点**（不是 `rmf.coordinator` 的 children）：`__init__` 内启动 mock
`/agv/*` HTTP 服务(:`edge_port`) + 模拟运动；RMF 节点（`rmf.coordinator`）的 `fleet_manager`
经 `/agv/*` 驱动它。真车换 `agv.SEER_RMF`（同 `/agv/*` 契约，链路不变）。

注册：plain class + `registry/devices/robot_rmf.yaml`；sim 模式不被 stub 靠 `device_pair.yaml`
给 `agv.RMFSim` 注册自配对（`real == virtual`）。框架不调用 async `initialize()`，故 mock 服务
在 `__init__` 启动（同 `rmf.coordinator`）。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("agv.RMFSim")

from unilabos.registry.decorators import topic_config

# 自标记（供 Device Square / 本地配对识别）。sim_engine 在此语义为 fleet_engine。
SIMULATION_META: Dict[str, Any] = {
    "driver_runtime_kind": "virtual",
    "virtual_driver_kind": "engine_adapter",
    "sim_engine": "rmf",
}


class RMFSimAgv:
    def __init__(
        self,
        robot_name: str = "unilab_agv1",
        fleet_name: str = "unilab_agv",
        footprint_radius: float = 0.35,
        charger_waypoint: str = "",
        initial_waypoint: str = "",
        device_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        self.device_id = device_id or robot_name
        self.robot_name = robot_name
        self.fleet_name = fleet_name
        self.footprint_radius = footprint_radius
        self.charger_waypoint = charger_waypoint
        self.initial_waypoint = initial_waypoint
        # graph 节点 config 的额外键经 **kwargs 传入，合并进 self.config（同 rmf.coordinator）
        self.config = {**(config or {}), **kwargs}
        self.data: Dict[str, Any] = {}

        # OS 内部 mock 小车：__init__ 内起 /agv/* HTTP 服务 + 模拟运动（框架不调用 async initialize()）
        self._hw = None  # MockAgvHardware
        self._server = None  # MockAgvServer
        self._floor = None  # FloorFrame（边缘帧→楼层帧；None=未试，False=不可用）
        self._start_mock_server()

        # 位姿经关节管道上报（#23.1 E1）：发 /joint_states（关节名 {robot}_x/_y/_yaw），
        # HostNode 自动按 device_id 前缀分组 → 映射 node_uuid → 限频/死区 → push_joint_state 上云。
        self._jp = None  # SimpleJointPublisher
        self._jp_executor = None
        self._jp_thread = None
        self._start_pose_publisher()

    def _start_mock_server(self) -> None:
        """启动 mock 小车硬件 + `/agv/*` HTTP 服务（#22 §0.1）。`config.enable_mock=false` 可关。"""
        if str(self.config.get("enable_mock", True)).lower() in ("0", "false", "no"):
            return
        runtime_clock = None
        try:
            from unilabos.sim.context import get_runtime_context

            runtime_clock = get_runtime_context().clock
        except Exception:  # noqa: BLE001
            runtime_clock = None
        try:
            from unilabos.sim.fleet.rmf.edge.agv_http_server import MockAgvServer
            from unilabos.sim.fleet.rmf.edge.mock_agv import MockAgvHardware
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[agv] mock 服务不可用（导入失败）: {e}")
            return

        x = float(self.config.get("start_x", 0.0))
        y = float(self.config.get("start_y", 0.0))
        yaw = float(self.config.get("start_yaw", 0.0))
        speed = float(self.config.get("linear_speed", 0.5))
        host = str(self.config.get("edge_host") or "127.0.0.1")
        port = int(self.config.get("edge_port") or 8090)

        self._hw = MockAgvHardware(self.robot_name, x=x, y=y, yaw=yaw, linear_speed=speed)
        try:
            # 注入 runtime clock：sim 模式下 pause/rate 会直接作用到 mock 小车推进。
            self._server = MockAgvServer([self._hw], log=lambda m: logger.info(m), clock=runtime_clock)
            self._server.start(host, port)
            logger.info(
                f"[agv] mock AGV 节点上线：/agv/* http://{host}:{port} "
                f"robot={self.robot_name}@({x},{y}) fleet={self.fleet_name}（OS 内部小车，#22 §0.1）"
            )
        except OSError as e:
            logger.warning(f"[agv] mock /agv/* 监听 {host}:{port} 失败（端口被占？）: {e}")
            self._server = None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[agv] mock 服务启动失败: {e}")
            self._server = None

    def _start_pose_publisher(self) -> None:
        """把 mock 实时位姿发到 `/joint_states`（关节名 `{robot}_x/_y/_yaw`），经 HostNode 上行
        `push_joint_state`（#23.1 E1，与机械臂同款关节管道）。

        HostNode 负责 `device_id→node_uuid` 映射、~20Hz 限频与 1e-4 死区，故本端只管发关节、不碰
        node_uuid。`config.publish_joint_state=false` 可关；`config.joint_state_rate` 调频（默认 20Hz）。
        """
        if str(self.config.get("publish_joint_state", True)).lower() in ("0", "false", "no"):
            return
        if self._hw is None:  # 无 mock 硬件无位姿可发
            return
        try:
            import rclpy
            from rclpy.executors import MultiThreadedExecutor

            from unilabos.devices.ros_dev.simple_joint_publisher_node import SimpleJointPublisher
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[agv] 位姿关节发布不可用（导入失败，非 ROS 后端？）: {e}")
            return

        rate = float(self.config.get("joint_state_rate", 20.0))
        try:
            if not rclpy.ok():
                rclpy.init()
            self._jp = SimpleJointPublisher(
                device_id=self.robot_name,  # 关节名前缀 = robotId（须与前端 useRmfStore id 一致）
                joint_names=["x", "y", "yaw"],  # → {robot}_x / _y / _yaw（米 / 米 / 弧度）
                rate=int(max(rate, 1.0)),
                node_name=f"{self.robot_name}_pose_pub",
            )
            # 把 mock 实时位姿镜像进 JointState（不插值，硬件已积分运动）；publisher 自带定时器发布
            self._jp.create_timer(1.0 / max(rate, 1.0), self._mirror_pose)
            self._jp_executor = MultiThreadedExecutor()
            self._jp_executor.add_node(self._jp)
            self._jp_thread = threading.Thread(
                target=self._jp_executor.spin, name=f"{self.robot_name}-pose-pub", daemon=True
            )
            self._jp_thread.start()
            logger.info(
                f"[agv] 位姿关节发布上线：/joint_states {self.robot_name}_x/_y/_yaw @ {int(rate)}Hz "
                f"→ HostNode push_joint_state（#23.1 E1）"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[agv] 位姿关节发布启动失败: {e}")
            self._jp = None

    def _mirror_pose(self) -> None:
        """20Hz 把 mock 实时位姿写进 JointState（由 SimpleJointPublisher 的定时器发布到 /joint_states）。"""
        if self._jp is None:
            return
        try:
            x, y, yaw = self.pose
            self._jp.set_immediate({"x": x, "y": y, "yaw": yaw})
        except Exception:  # noqa: BLE001
            pass

    async def initialize(self) -> bool:
        self.data.update({"pose": self.pose, "status": self.status})
        return True

    async def cleanup(self) -> bool:
        if self._jp_executor is not None:
            try:
                if self._jp is not None:
                    self._jp_executor.remove_node(self._jp)
                self._jp_executor.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._jp_executor = None
            self._jp = None
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        return True

    def _get_floor(self):
        """惰性创建边缘帧→楼层帧转换器（#24.1 §0）。需 config.generated_map_dir；不可用则降级用边缘帧。"""
        if self._floor is None:
            gmd = self.config.get("generated_map_dir")
            if not gmd:
                self._floor = False
            else:
                try:
                    from unilabos.sim.fleet.rmf.frame import FloorFrame

                    self._floor = FloorFrame(str(gmd))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[agv] FloorFrame 不可用，pose 用边缘帧: {e}")
                    self._floor = False
        return self._floor or None

    # ---------------------------------------------------------------- status
    @property
    @topic_config(period=0.1)
    def pose(self) -> List[float]:
        """[x_m, y_m, yaw_rad]：mock 硬件实时位姿（RMF 边缘帧）→ **转楼层帧** 后返回（#24.1 §2）。

        经 device_status 高频(~10Hz)写 `node.data.pose`，前端每帧读它驱动设备 3D（与设备同帧）。
        """
        if self._hw is None:
            return [0.0, 0.0, 0.0]
        st = self._hw.state()
        ex, ey, yaw = float(st["x"]), float(st["y"]), float(st["yaw"])
        ff = self._get_floor()
        if ff is not None and ff.ready:
            fx, fy = ff.edge_to_floor(ex, ey)
            return [fx, fy, yaw]
        return [ex, ey, yaw]

    @property
    def status(self) -> str:
        """idle / moving 等（标量，可走 device_status）。"""
        return self._hw.status if self._hw is not None else "idle"

    @property
    def battery(self) -> float:
        return float(self._hw.battery) if self._hw is not None else 1.0

    @property
    def task_id(self) -> str:
        return ""

    # ---------------------------------------------------- 兼容旧接口（现由 mock 自模拟，保留空实现）
    def update_from_rmf(
        self,
        x_m: float,
        y_m: float,
        yaw_rad: float,
        status: str = "idle",
        battery: Optional[float] = None,
        task_id: str = "",
    ) -> None:
        """历史接口（旧设计由 coordinator event_collector 注入位姿）。

        新设计中 mock 小车经 `/agv/*` 收 `fleet_manager` 指令、自行模拟运动并上报，
        无需外部注入；保留为 no-op 以兼容旧调用。
        """
        return None
