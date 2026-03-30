"""MoveItCollisionChecker 和 IKFastReachabilityChecker 测试。

使用 unittest.mock 模拟 MoveIt2 实例，验证适配器逻辑，
无需 ROS2 / MoveIt2 运行环境。
"""

import math
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ..ros_checkers import (
    IKFastReachabilityChecker,
    MoveItCollisionChecker,
    _transform_to_arm_frame,
    _yaw_to_quat,
    _yaw_to_rotation_matrix,
    create_checkers,
)


# ---------- 辅助函数测试 ----------


class TestYawToQuat:
    def test_zero_rotation(self):
        """零旋转 → 单位四元数。"""
        q = _yaw_to_quat(0.0)
        assert q == pytest.approx((0.0, 0.0, 0.0, 1.0))

    def test_90_degrees(self):
        """90° → (0, 0, sin(π/4), cos(π/4))。"""
        q = _yaw_to_quat(math.pi / 2)
        expected = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))
        assert q == pytest.approx(expected)

    def test_180_degrees(self):
        """180° → (0, 0, 1, 0)。"""
        q = _yaw_to_quat(math.pi)
        assert q == pytest.approx((0.0, 0.0, 1.0, 0.0), abs=1e-10)


class TestTransformToArmFrame:
    def test_identity_transform(self):
        """臂在原点无旋转，目标在 (1, 0, 0.5)。"""
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 1.0, "y": 0.0, "z": 0.5}
        local = _transform_to_arm_frame(arm_pose, target)
        assert local == pytest.approx((1.0, 0.0, 0.5))

    def test_translation_only(self):
        """臂在 (2, 3) 无旋转，目标在 (3, 4, 0)。"""
        arm_pose = {"x": 2.0, "y": 3.0, "theta": 0.0}
        target = {"x": 3.0, "y": 4.0, "z": 0.0}
        local = _transform_to_arm_frame(arm_pose, target)
        assert local == pytest.approx((1.0, 1.0, 0.0))

    def test_rotation_90(self):
        """臂旋转 90°，目标在臂前方。"""
        arm_pose = {"x": 0.0, "y": 0.0, "theta": math.pi / 2}
        target = {"x": 0.0, "y": 1.0, "z": 0.0}
        local = _transform_to_arm_frame(arm_pose, target)
        # 世界 Y+ 在臂坐标系中变成 X+
        assert local[0] == pytest.approx(1.0, abs=1e-10)
        assert local[1] == pytest.approx(0.0, abs=1e-10)


class TestYawToRotationMatrix:
    def test_identity(self):
        """零旋转 → 单位矩阵。"""
        R = _yaw_to_rotation_matrix(0.0)
        np.testing.assert_allclose(R, np.eye(3), atol=1e-10)

    def test_90_degrees(self):
        """90° 旋转矩阵。"""
        R = _yaw_to_rotation_matrix(math.pi / 2)
        expected = np.array([
            [0.0, -1.0, 0.0],
            [1.0,  0.0, 0.0],
            [0.0,  0.0, 1.0],
        ])
        np.testing.assert_allclose(R, expected, atol=1e-10)


# ---------- MoveItCollisionChecker 测试 ----------


class TestMoveItCollisionChecker:
    def setup_method(self):
        self.moveit2 = MagicMock()
        # 禁用 FCL，使用 OBB 回退（测试环境无需 python-fcl）
        self.checker = MoveItCollisionChecker(
            self.moveit2, sync_to_scene=True,
        )
        self.checker._fcl_available = False

    def test_no_collision_far_apart(self):
        """两个设备距离足够远，不碰撞。"""
        placements = [
            {"id": "a", "bbox": (0.5, 0.5), "pos": (1.0, 1.0, 0.0)},
            {"id": "b", "bbox": (0.5, 0.5), "pos": (3.0, 3.0, 0.0)},
        ]
        assert self.checker.check(placements) == []

    def test_collision_overlapping(self):
        """两个设备重叠，应检测到碰撞。"""
        placements = [
            {"id": "a", "bbox": (1.0, 1.0), "pos": (1.0, 1.0, 0.0)},
            {"id": "b", "bbox": (1.0, 1.0), "pos": (1.5, 1.0, 0.0)},
        ]
        collisions = self.checker.check(placements)
        assert ("a", "b") in collisions

    def test_collision_with_rotation(self):
        """旋转后的碰撞检测。"""
        placements = [
            {"id": "a", "bbox": (1.0, 0.2), "pos": (1.0, 1.0, math.pi / 4)},
            {"id": "b", "bbox": (0.5, 0.5), "pos": (1.4, 1.0, 0.0)},
        ]
        collisions = self.checker.check(placements)
        assert ("a", "b") in collisions

    def test_syncs_collision_objects(self):
        """验证 check() 调用 add_collision_box 同步到 MoveIt2。"""
        placements = [
            {"id": "dev_a", "bbox": (0.6, 0.8), "pos": (1.0, 2.0, 0.5)},
        ]
        self.checker.check(placements)

        self.moveit2.add_collision_box.assert_called_once()
        call_kwargs = self.moveit2.add_collision_box.call_args
        # 验证使用 {device_id}_ 前缀
        assert call_kwargs.kwargs["id"] == "dev_a_"
        # 验证 size = (w, d, h)
        assert call_kwargs.kwargs["size"] == (0.6, 0.8, 0.4)

    def test_device_id_prefix(self):
        """碰撞对象名称使用 {device_id}_ 前缀。"""
        placements = [
            {"id": "robot_arm", "bbox": (0.3, 0.3), "pos": (1.0, 1.0, 0.0)},
            {"id": "centrifuge", "bbox": (0.5, 0.5), "pos": (3.0, 3.0, 0.0)},
        ]
        self.checker.check(placements)

        calls = self.moveit2.add_collision_box.call_args_list
        ids = [c.kwargs["id"] for c in calls]
        assert "robot_arm_" in ids
        assert "centrifuge_" in ids

    def test_sync_failure_does_not_crash(self):
        """add_collision_box 异常不影响碰撞检测结果。"""
        self.moveit2.add_collision_box.side_effect = RuntimeError("service unavailable")
        placements = [
            {"id": "a", "bbox": (0.5, 0.5), "pos": (1.0, 1.0, 0.0)},
            {"id": "b", "bbox": (0.5, 0.5), "pos": (3.0, 3.0, 0.0)},
        ]
        # 不应抛异常
        collisions = self.checker.check(placements)
        assert collisions == []

    def test_check_bounds_within(self):
        """设备在边界内。"""
        placements = [
            {"id": "a", "bbox": (0.5, 0.5), "pos": (1.0, 1.0, 0.0)},
        ]
        assert self.checker.check_bounds(placements, 5.0, 5.0) == []

    def test_check_bounds_outside(self):
        """设备超出边界。"""
        placements = [
            {"id": "a", "bbox": (1.0, 1.0), "pos": (0.2, 0.2, 0.0)},
        ]
        oob = self.checker.check_bounds(placements, 5.0, 5.0)
        assert "a" in oob

    def test_no_sync_mode(self):
        """sync_to_scene=False 时不调用 add_collision_box。"""
        checker = MoveItCollisionChecker(
            self.moveit2, sync_to_scene=False,
        )
        checker._fcl_available = False
        placements = [
            {"id": "a", "bbox": (0.5, 0.5), "pos": (1.0, 1.0, 0.0)},
        ]
        checker.check(placements)
        self.moveit2.add_collision_box.assert_not_called()

    def test_touching_edges_no_collision(self):
        """恰好边缘接触，不算碰撞。"""
        placements = [
            {"id": "a", "bbox": (1.0, 1.0), "pos": (0.5, 0.5, 0.0)},
            {"id": "b", "bbox": (1.0, 1.0), "pos": (1.5, 0.5, 0.0)},
        ]
        collisions = self.checker.check(placements)
        assert collisions == []

    def test_three_devices_multiple_collisions(self):
        """三个设备，相邻碰撞。"""
        placements = [
            {"id": "a", "bbox": (1.0, 1.0), "pos": (1.0, 1.0, 0.0)},
            {"id": "b", "bbox": (1.0, 1.0), "pos": (1.3, 1.0, 0.0)},
            {"id": "c", "bbox": (1.0, 1.0), "pos": (1.6, 1.0, 0.0)},
        ]
        collisions = self.checker.check(placements)
        assert ("a", "b") in collisions
        assert ("b", "c") in collisions


# ---------- IKFastReachabilityChecker 测试 ----------


class TestIKFastReachabilityCheckerVoxel:
    """体素图模式测试。"""

    def _create_voxel_dir(self, tmp_path: Path, arm_id: str = "elite_cs66") -> Path:
        """创建包含体素图的临时目录。"""
        # 创建一个简单的体素网格：中心区域可达
        grid = np.zeros((100, 100, 50), dtype=bool)
        # 标记中心 60x60x30 区域为可达
        grid[20:80, 20:80, 10:40] = True

        origin = np.array([-0.5, -0.5, 0.0])
        resolution = 0.01

        npz_path = tmp_path / f"{arm_id}.npz"
        np.savez(str(npz_path), grid=grid, origin=origin, resolution=resolution)
        return tmp_path

    def test_reachable_in_voxel(self, tmp_path):
        """目标在体素图可达区域内。"""
        voxel_dir = self._create_voxel_dir(tmp_path)
        checker = IKFastReachabilityChecker(voxel_dir=voxel_dir)

        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        # 中心区域：local = (0.0, 0.0, 0.2) → ix=50, iy=50, iz=20 → 可达
        target = {"x": 0.0, "y": 0.0, "z": 0.2}
        assert checker.is_reachable("elite_cs66", arm_pose, target)

    def test_not_reachable_outside_voxel(self, tmp_path):
        """目标在体素图不可达区域。"""
        voxel_dir = self._create_voxel_dir(tmp_path)
        checker = IKFastReachabilityChecker(voxel_dir=voxel_dir)

        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        # 边缘区域：local = (-0.45, -0.45, 0.0) → ix=5, iy=5, iz=0 → 不可达
        target = {"x": -0.45, "y": -0.45, "z": 0.0}
        assert not checker.is_reachable("elite_cs66", arm_pose, target)

    def test_out_of_bounds_not_reachable(self, tmp_path):
        """目标超出体素图范围。"""
        voxel_dir = self._create_voxel_dir(tmp_path)
        checker = IKFastReachabilityChecker(voxel_dir=voxel_dir)

        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 5.0, "y": 5.0, "z": 0.0}
        assert not checker.is_reachable("elite_cs66", arm_pose, target)

    def test_arm_rotation_transforms_target(self, tmp_path):
        """臂旋转后目标变换到臂坐标系。"""
        voxel_dir = self._create_voxel_dir(tmp_path)
        checker = IKFastReachabilityChecker(voxel_dir=voxel_dir)

        # 臂旋转 90°，目标在世界 Y+ 方向 → 臂坐标系 X+ 方向
        arm_pose = {"x": 0.0, "y": 0.0, "theta": math.pi / 2}
        # 世界 (0, 0.1, 0.2) → 臂坐标系 (0.1, 0, 0.2) → 在可达范围
        target = {"x": 0.0, "y": 0.1, "z": 0.2}
        assert checker.is_reachable("elite_cs66", arm_pose, target)

    def test_unknown_arm_no_voxel_no_moveit(self, tmp_path):
        """未知臂型且无 MoveIt2，乐观返回 True。"""
        voxel_dir = self._create_voxel_dir(tmp_path)
        checker = IKFastReachabilityChecker(voxel_dir=voxel_dir)

        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 0.5, "y": 0.0, "z": 0.0}
        assert checker.is_reachable("unknown_arm", arm_pose, target)

    def test_missing_voxel_dir(self):
        """体素目录不存在不报错。"""
        checker = IKFastReachabilityChecker(voxel_dir="/nonexistent/path")
        assert len(checker._voxel_maps) == 0


class TestIKFastReachabilityCheckerLiveIK:
    """实时 IK 模式测试。"""

    def test_reachable_via_ik(self):
        """compute_ik 返回 JointState → 可达。"""
        moveit2 = MagicMock()
        moveit2.compute_ik.return_value = MagicMock()  # 非 None → 成功

        checker = IKFastReachabilityChecker(moveit2)
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 0.5, "y": 0.0, "z": 0.3}
        assert checker.is_reachable("elite_cs66", arm_pose, target)

    def test_not_reachable_via_ik(self):
        """compute_ik 返回 None → 不可达。"""
        moveit2 = MagicMock()
        moveit2.compute_ik.return_value = None

        checker = IKFastReachabilityChecker(moveit2)
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 5.0, "y": 5.0, "z": 0.0}
        assert not checker.is_reachable("elite_cs66", arm_pose, target)

    def test_ik_exception_returns_false(self):
        """compute_ik 抛异常 → 不可达。"""
        moveit2 = MagicMock()
        moveit2.compute_ik.side_effect = RuntimeError("service timeout")

        checker = IKFastReachabilityChecker(moveit2)
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 0.5, "y": 0.0, "z": 0.0}
        assert not checker.is_reachable("elite_cs66", arm_pose, target)

    def test_ik_called_with_correct_position(self):
        """验证 compute_ik 接收正确的臂坐标系位置。"""
        moveit2 = MagicMock()
        moveit2.compute_ik.return_value = MagicMock()

        checker = IKFastReachabilityChecker(moveit2)
        arm_pose = {"x": 1.0, "y": 2.0, "theta": 0.0}
        target = {"x": 1.5, "y": 2.3, "z": 0.4}
        checker.is_reachable("elite_cs66", arm_pose, target)

        call_kwargs = moveit2.compute_ik.call_args.kwargs
        assert call_kwargs["position"] == pytest.approx((0.5, 0.3, 0.4))

    def test_voxel_takes_priority_over_live_ik(self, tmp_path):
        """有体素图时优先使用体素查询，不调用 compute_ik。"""
        # 创建体素图
        grid = np.ones((10, 10, 10), dtype=bool)
        origin = np.array([-0.05, -0.05, 0.0])
        np.savez(
            str(tmp_path / "test_arm.npz"),
            grid=grid, origin=origin, resolution=0.01,
        )

        moveit2 = MagicMock()
        checker = IKFastReachabilityChecker(moveit2, voxel_dir=tmp_path)

        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 0.0, "y": 0.0, "z": 0.05}
        checker.is_reachable("test_arm", arm_pose, target)

        moveit2.compute_ik.assert_not_called()


# ---------- create_checkers 工厂函数测试 ----------


class TestCreateCheckers:
    def test_mock_mode(self):
        """mock 模式返回 Mock 检测器。"""
        from ..mock_checkers import (
            MockCollisionChecker,
            MockReachabilityChecker,
        )

        collision, reachability = create_checkers(mode="mock")
        assert isinstance(collision, MockCollisionChecker)
        assert isinstance(reachability, MockReachabilityChecker)

    def test_moveit_mode(self):
        """moveit 模式返回 MoveIt2 检测器。"""
        moveit2 = MagicMock()
        collision, reachability = create_checkers(moveit2, mode="moveit")
        assert isinstance(collision, MoveItCollisionChecker)
        assert isinstance(reachability, IKFastReachabilityChecker)

    def test_moveit_mode_requires_instance(self):
        """moveit 模式无实例时抛异常。"""
        with pytest.raises(ValueError, match="MoveIt2 instance required"):
            create_checkers(mode="moveit")

    def test_default_mode_is_mock(self):
        """默认使用 mock 模式。"""
        from ..mock_checkers import MockCollisionChecker

        collision, _ = create_checkers()
        assert isinstance(collision, MockCollisionChecker)

    def test_env_var_override(self, monkeypatch):
        """LAYOUT_CHECKER_MODE 环境变量覆盖默认值。"""
        moveit2 = MagicMock()
        monkeypatch.setenv("LAYOUT_CHECKER_MODE", "moveit")
        collision, _ = create_checkers(moveit2)
        assert isinstance(collision, MoveItCollisionChecker)


# ---------- Protocol 兼容性测试 ----------


class TestProtocolConformance:
    """验证适配器满足 Protocol 接口签名。"""

    def test_collision_checker_has_check(self):
        """MoveItCollisionChecker 实现 check(placements) 方法。"""
        moveit2 = MagicMock()
        checker = MoveItCollisionChecker(moveit2, sync_to_scene=False)
        checker._fcl_available = False
        placements = [
            {"id": "a", "bbox": (0.5, 0.5), "pos": (1.0, 1.0, 0.0)},
        ]
        result = checker.check(placements)
        assert isinstance(result, list)

    def test_reachability_checker_has_is_reachable(self):
        """IKFastReachabilityChecker 实现 is_reachable(arm_id, arm_pose, target) 方法。"""
        checker = IKFastReachabilityChecker()
        result = checker.is_reachable(
            "arm_id",
            {"x": 0.0, "y": 0.0, "theta": 0.0},
            {"x": 0.5, "y": 0.0, "z": 0.0},
        )
        assert isinstance(result, bool)

    def test_collision_checker_has_check_bounds(self):
        """MoveItCollisionChecker 实现 check_bounds 方法。"""
        moveit2 = MagicMock()
        checker = MoveItCollisionChecker(moveit2, sync_to_scene=False)
        placements = [
            {"id": "a", "bbox": (0.5, 0.5), "pos": (1.0, 1.0, 0.0)},
        ]
        result = checker.check_bounds(placements, 5.0, 5.0)
        assert isinstance(result, list)
