"""MockCollisionChecker 和 MockReachabilityChecker 测试。"""

import math

from ..mock_checkers import MockCollisionChecker, MockReachabilityChecker


class TestMockCollisionChecker:
    def setup_method(self):
        self.checker = MockCollisionChecker()

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

    def test_collision_touching_edges(self):
        """两设备恰好边缘接触，不算碰撞（< 而非 <=）。"""
        placements = [
            {"id": "a", "bbox": (1.0, 1.0), "pos": (0.5, 0.5, 0.0)},
            {"id": "b", "bbox": (1.0, 1.0), "pos": (1.5, 0.5, 0.0)},
        ]
        collisions = self.checker.check(placements)
        assert collisions == []

    def test_collision_with_rotation(self):
        """旋转后的设备 OBB 可能导致碰撞。"""
        placements = [
            {"id": "a", "bbox": (1.0, 0.2), "pos": (1.0, 1.0, math.pi / 4)},
            {"id": "b", "bbox": (0.5, 0.5), "pos": (1.4, 1.0, 0.0)},  # closer: OBB overlap
        ]
        collisions = self.checker.check(placements)
        assert ("a", "b") in collisions

    def test_no_collision_with_rotation(self):
        """旋转后仍不碰撞。"""
        placements = [
            {"id": "a", "bbox": (1.0, 0.2), "pos": (1.0, 1.0, math.pi / 4)},
            {"id": "b", "bbox": (0.5, 0.5), "pos": (2.0, 1.0, 0.0)},
        ]
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

    def test_three_devices_multiple_collisions(self):
        """三个设备，两两碰撞。"""
        placements = [
            {"id": "a", "bbox": (1.0, 1.0), "pos": (1.0, 1.0, 0.0)},
            {"id": "b", "bbox": (1.0, 1.0), "pos": (1.3, 1.0, 0.0)},
            {"id": "c", "bbox": (1.0, 1.0), "pos": (1.6, 1.0, 0.0)},
        ]
        collisions = self.checker.check(placements)
        assert ("a", "b") in collisions
        assert ("b", "c") in collisions


def test_obb_collision_rotated_no_false_positive():
    """A rotated narrow device should NOT collide with a nearby device
    that the old AABB method would have flagged as colliding.

    Old AABB expands footprint; OBB is precise.
    """
    checker = MockCollisionChecker()
    # Narrow device (2.0 x 0.5) rotated 45°:
    # AABB would be ~1.77 x 1.77, OBB is the actual narrow rectangle
    placements = [
        {"id": "narrow", "bbox": (2.0, 0.5), "pos": (3.0, 3.0, math.pi / 4)},
        {"id": "nearby", "bbox": (0.5, 0.5), "pos": (4.5, 3.0, 0.0)},
    ]
    collisions = checker.check(placements)
    # With OBB: no collision (the narrow rotated box doesn't reach)
    assert ("narrow", "nearby") not in collisions and ("nearby", "narrow") not in collisions


class TestMockReachabilityChecker:
    def setup_method(self):
        self.checker = MockReachabilityChecker()

    def test_reachable_within_radius(self):
        """目标在臂展半径内。"""
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 0.5, "y": 0.5, "z": 0.0}
        assert self.checker.is_reachable("elite_cs66", arm_pose, target)

    def test_not_reachable_outside_radius(self):
        """目标超出臂展半径。"""
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 2.0, "y": 2.0, "z": 0.0}
        assert not self.checker.is_reachable("elite_cs66", arm_pose, target)

    def test_reachable_at_boundary(self):
        """目标恰好在臂展边界上（应可达）。"""
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 0.914, "y": 0.0, "z": 0.0}
        assert self.checker.is_reachable("elite_cs66", arm_pose, target)

    def test_unknown_arm_uses_default(self):
        """未知型号使用 1.0m 回退臂展（realistic lab-scale default）。"""
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        # Within 1.0m fallback reach
        target_near = {"x": 0.8, "y": 0.0, "z": 0.0}
        assert self.checker.is_reachable("unknown_arm", arm_pose, target_near)
        # Beyond 1.0m fallback reach
        target_far = {"x": 1.5, "y": 0.0, "z": 0.0}
        assert not self.checker.is_reachable("unknown_arm", arm_pose, target_far)

    def test_custom_arm_reach(self):
        """自定义臂展参数。"""
        checker = MockReachabilityChecker(arm_reach={"custom_arm": 1.5})
        arm_pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        target = {"x": 1.4, "y": 0.0, "z": 0.0}
        assert checker.is_reachable("custom_arm", arm_pose, target)
