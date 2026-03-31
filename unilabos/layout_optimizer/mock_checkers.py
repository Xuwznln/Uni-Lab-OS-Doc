"""Mock 检测器：无 ROS 依赖的简化碰撞与可达性检测。

碰撞检测基于 OBB SAT（O(n²) 两两比较）。
可达性检测基于最大臂展半径的欧几里得距离判断。

集成阶段由 ros_checkers.py 中的 MoveItCollisionChecker / IKFastReachabilityChecker 替代。
"""

from __future__ import annotations

import math

from .obb import obb_corners, obb_overlap


class MockCollisionChecker:
    """基于 OBB SAT 的碰撞检测。

    输入格式与 CollisionChecker Protocol 一致：
        placements: [{"id": str, "bbox": (w, d), "pos": (x, y, θ)}, ...]
    """

    def check(self, placements: list[dict]) -> list[tuple[str, str]]:
        """返回所有碰撞的设备对。"""
        collisions: list[tuple[str, str]] = []
        n = len(placements)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = placements[i], placements[j]
                corners_a = obb_corners(
                    a["pos"][0], a["pos"][1],
                    a["bbox"][0], a["bbox"][1],
                    a["pos"][2] if len(a["pos"]) > 2 else 0.0,
                )
                corners_b = obb_corners(
                    b["pos"][0], b["pos"][1],
                    b["bbox"][0], b["bbox"][1],
                    b["pos"][2] if len(b["pos"]) > 2 else 0.0,
                )
                if obb_overlap(corners_a, corners_b):
                    collisions.append((a["id"], b["id"]))
        return collisions

    def check_bounds(
        self, placements: list[dict], lab_width: float, lab_depth: float
    ) -> list[str]:
        """返回超出实验室边界的设备 ID 列表。"""
        out_of_bounds: list[str] = []
        for p in placements:
            hw, hd = self._rotated_half_extents(p)
            x, y = p["pos"][:2]
            if x - hw < 0 or x + hw > lab_width or y - hd < 0 or y + hd > lab_depth:
                out_of_bounds.append(p["id"])
        return out_of_bounds

    @staticmethod
    def _rotated_half_extents(p: dict) -> tuple[float, float]:
        """计算旋转后 AABB 的半宽和半深。"""
        w, d = p["bbox"]
        theta = p["pos"][2] if len(p["pos"]) > 2 else 0.0
        cos_t = abs(math.cos(theta))
        sin_t = abs(math.sin(theta))
        half_w = (w * cos_t + d * sin_t) / 2
        half_d = (w * sin_t + d * cos_t) / 2
        return half_w, half_d


class MockReachabilityChecker:
    """基于最大臂展半径的简化可达性判断。

    内置常见 Elite CS 系列机械臂的臂展参数。
    自定义臂展可通过构造参数传入。
    """

    # 默认臂展参数（单位：米）
    DEFAULT_ARM_REACH: dict[str, float] = {
        "elite_cs63": 0.624,
        "elite_cs66": 0.914,
        "elite_cs612": 1.304,
        "elite_cs620": 1.800,
        "arm_slider": 1.07,   # 线性导轨臂：body 2.14m × 0.35m，reach ≈ half length
    }

    # 未知型号回退臂展：realistic default for lab-scale arms
    DEFAULT_FALLBACK_REACH: float = 0.4

    def __init__(self, arm_reach: dict[str, float] | None = None):
        self.arm_reach = {**self.DEFAULT_ARM_REACH, **(arm_reach or {})}

    def is_reachable(self, arm_id: str, arm_pose: dict, target: dict) -> bool:
        """判断目标点是否在机械臂最大臂展半径内。

        Uses OBB edge-to-edge distance when available (passed as _obb_dist),
        otherwise falls back to center-to-center Euclidean distance.

        Args:
            arm_id: 机械臂型号 ID（用于查臂展）
            arm_pose: {"x": float, "y": float, "theta": float}
            target: {"x": float, "y": float, "z": float, "_obb_dist": float (optional)}

        Returns:
            True 如果目标在臂展半径内
        """
        max_reach = self.arm_reach.get(arm_id, self.DEFAULT_FALLBACK_REACH)
        if "_obb_dist" in target:
            return target["_obb_dist"] <= max_reach
        dx = target["x"] - arm_pose["x"]
        dy = target["y"] - arm_pose["y"]
        dist_sq = dx**2 + dy**2
        return dist_sq <= max_reach**2
