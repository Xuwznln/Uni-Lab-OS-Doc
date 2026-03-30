"""Protocol 接口定义，隔离 ROS 依赖。

开发阶段使用 mock_checkers.py 中的 Mock 实现，
集成阶段替换为 ros_checkers.py 中的 MoveIt2 / IKFast 实现。
"""

from __future__ import annotations

from typing import Protocol


class CollisionChecker(Protocol):
    """碰撞检测接口。"""

    def check(self, placements: list[dict]) -> list[tuple[str, str]]:
        """返回碰撞设备对列表。

        Args:
            placements: [{"id": str, "bbox": (w, d), "pos": (x, y, θ)}, ...]

        Returns:
            [("device_a", "device_b"), ...] 存在碰撞的设备对
        """
        ...


class ReachabilityChecker(Protocol):
    """可达性检测接口。"""

    def is_reachable(self, arm_id: str, arm_pose: dict, target: dict) -> bool:
        """判断机械臂在给定位姿下能否到达目标点。

        Args:
            arm_id: 机械臂设备 ID
            arm_pose: {"x": float, "y": float, "theta": float}
            target: {"x": float, "y": float, "z": float}

        Returns:
            True 如果可达
        """
        ...
