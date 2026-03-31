"""约束体系测试。"""

import math

import pytest

from ..constraints import (
    evaluate_constraints,
    evaluate_default_hard_constraints,
)
from ..mock_checkers import MockCollisionChecker, MockReachabilityChecker
from ..models import Constraint, Device, Lab, Placement


def _make_devices():
    return [
        Device(id="a", name="Device A", bbox=(0.5, 0.5)),
        Device(id="b", name="Device B", bbox=(0.5, 0.5)),
    ]


def _make_lab():
    return Lab(width=5.0, depth=4.0)


class TestDefaultHardConstraints:
    def test_no_collision_passes(self):
        """无碰撞的布局应返回 0。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 1.0, 0.0),
            Placement("b", 3.0, 3.0, 0.0),
        ]
        checker = MockCollisionChecker()
        cost = evaluate_default_hard_constraints(devices, placements, _make_lab(), checker)
        assert cost == 0.0

    def test_collision_returns_graduated_penalty(self):
        """碰撞布局应返回正的graduated penalty（非inf）。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 1.0, 0.0),
            Placement("b", 1.2, 1.0, 0.0),
        ]
        checker = MockCollisionChecker()
        cost = evaluate_default_hard_constraints(devices, placements, _make_lab(), checker)
        assert cost > 0
        assert not math.isinf(cost)

    def test_collision_returns_inf_binary_mode(self):
        """Binary mode: 碰撞布局应返回 inf。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 1.0, 0.0),
            Placement("b", 1.2, 1.0, 0.0),
        ]
        checker = MockCollisionChecker()
        cost = evaluate_default_hard_constraints(
            devices, placements, _make_lab(), checker, graduated=False,
        )
        assert math.isinf(cost)

    def test_out_of_bounds_returns_graduated_penalty(self):
        """越界布局应返回正的graduated penalty（非inf）。"""
        devices = _make_devices()
        placements = [
            Placement("a", 0.1, 0.1, 0.0),  # 左下角越界
            Placement("b", 3.0, 3.0, 0.0),
        ]
        checker = MockCollisionChecker()
        cost = evaluate_default_hard_constraints(devices, placements, _make_lab(), checker)
        assert cost > 0
        assert not math.isinf(cost)

    def test_out_of_bounds_returns_inf_binary_mode(self):
        """Binary mode: 越界布局应返回 inf。"""
        devices = _make_devices()
        placements = [
            Placement("a", 0.1, 0.1, 0.0),
            Placement("b", 3.0, 3.0, 0.0),
        ]
        checker = MockCollisionChecker()
        cost = evaluate_default_hard_constraints(
            devices, placements, _make_lab(), checker, graduated=False,
        )
        assert math.isinf(cost)

    def test_worse_collision_higher_cost(self):
        """Deeper penetration should produce higher cost."""
        devices = _make_devices()
        checker = MockCollisionChecker()
        lab = _make_lab()
        # Small overlap
        cost_small = evaluate_default_hard_constraints(
            devices, [Placement("a", 1.0, 1.0, 0.0), Placement("b", 1.4, 1.0, 0.0)],
            lab, checker,
        )
        # Large overlap
        cost_large = evaluate_default_hard_constraints(
            devices, [Placement("a", 1.0, 1.0, 0.0), Placement("b", 1.1, 1.0, 0.0)],
            lab, checker,
        )
        assert cost_large > cost_small > 0


class TestUserConstraints:
    def test_distance_less_than_satisfied(self):
        """距离约束满足时 cost=0。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 1.0, 0.0),
            Placement("b", 1.5, 1.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="distance_less_than",
                       params={"device_a": "a", "device_b": "b", "distance": 1.0})
        ]
        checker = MockCollisionChecker()
        reachability = MockReachabilityChecker()
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker, reachability
        )
        assert cost == 0.0

    def test_distance_less_than_violated_hard(self):
        """硬距离约束违反：graduated模式返回有限惩罚，binary模式返回inf。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 1.0, 0.0),
            Placement("b", 4.0, 3.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="distance_less_than",
                       params={"device_a": "a", "device_b": "b", "distance": 1.0})
        ]
        checker = MockCollisionChecker()
        # graduated=True (default): 有限惩罚
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker
        )
        assert cost > 0
        assert not math.isinf(cost)
        # graduated=False: binary inf
        cost_binary = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker,
            graduated=False,
        )
        assert math.isinf(cost_binary)

    def test_minimize_distance_cost(self):
        """minimize_distance 约束应返回正比于距离的 cost。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 1.0, 0.0),
            Placement("b", 3.0, 1.0, 0.0),
        ]
        constraints = [
            Constraint(type="soft", rule_name="minimize_distance",
                       params={"device_a": "a", "device_b": "b"}, weight=2.0)
        ]
        checker = MockCollisionChecker()
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker
        )
        # edge-to-edge distance = 2.0 - 0.25 - 0.25 = 1.5, weight = 2.0 → cost = 3.0
        assert abs(cost - 3.0) < 0.01

    def test_reachability_constraint(self):
        """可达性约束：目标在臂展内应通过（不返回 inf）。

        Opening-faces-arm penalty may add a small soft cost when the
        target's opening doesn't face the arm, but it must not cause
        hard failure (inf).
        """
        devices = [
            Device(id="arm", name="Arm", bbox=(0.2, 0.2), device_type="articulation"),
            Device(id="target", name="Target", bbox=(0.5, 0.5)),
        ]
        placements = [
            Placement("arm", 1.0, 1.0, 0.0),
            Placement("target", 1.5, 1.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="reachability",
                       params={"arm_id": "arm", "target_device_id": "target"})
        ]
        checker = MockCollisionChecker()
        reachability = MockReachabilityChecker(arm_reach={"arm": 1.0})
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker, reachability
        )
        assert not math.isinf(cost)  # reachable → no hard failure

    def test_reachability_constraint_violated(self):
        """可达性约束：目标超出臂展 — graduated返回有限惩罚，binary返回inf。"""
        devices = [
            Device(id="arm", name="Arm", bbox=(0.2, 0.2), device_type="articulation"),
            Device(id="target", name="Target", bbox=(0.5, 0.5)),
        ]
        placements = [
            Placement("arm", 1.0, 1.0, 0.0),
            Placement("target", 4.0, 3.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="reachability",
                       params={"arm_id": "arm", "target_device_id": "target"})
        ]
        checker = MockCollisionChecker()
        reachability = MockReachabilityChecker(arm_reach={"arm": 1.0})
        # graduated=True (default): 有限惩罚
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker, reachability
        )
        assert cost > 0
        assert not math.isinf(cost)
        # graduated=False: binary inf
        cost_binary = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker, reachability,
            graduated=False,
        )
        assert math.isinf(cost_binary)


def test_distance_less_than_uses_edge_to_edge():
    """distance_less_than should measure edge-to-edge, not center-to-center.

    Two devices: centers 3m apart, each 2m wide → edge gap = 1m.
    Constraint: distance_less_than 1.5m (edge-to-edge).
    Old center-to-center: 3m > 1.5m → violation.
    New edge-to-edge: 1m < 1.5m → satisfied.
    """
    devices = [
        Device(id="a", name="A", bbox=(2.0, 1.0)),
        Device(id="b", name="B", bbox=(2.0, 1.0)),
    ]
    placements = [
        Placement(device_id="a", x=1.0, y=1.0, theta=0.0),
        Placement(device_id="b", x=4.0, y=1.0, theta=0.0),
    ]
    lab = Lab(width=10, depth=10)
    constraint = Constraint(
        type="soft", rule_name="distance_less_than",
        params={"device_a": "a", "device_b": "b", "distance": 1.5},
        weight=1.0,
    )
    checker = MockCollisionChecker()
    cost = evaluate_constraints(devices, placements, lab, [constraint], checker)
    assert cost == pytest.approx(0.0)


def test_prefer_aligned_zero_at_cardinal():
    """prefer_aligned cost = 0 when all devices at 0/90/180/270°."""
    devices = [Device(id="a", name="A", bbox=(1.0, 1.0))]
    lab = Lab(width=10, depth=10)
    checker = MockCollisionChecker()
    for angle in [0, math.pi / 2, math.pi, 3 * math.pi / 2]:
        placements = [Placement(device_id="a", x=5, y=5, theta=angle)]
        constraint = Constraint(type="soft", rule_name="prefer_aligned", weight=1.0)
        cost = evaluate_constraints(devices, placements, lab, [constraint], checker)
        assert cost == pytest.approx(0.0, abs=1e-9)


def test_prefer_aligned_max_at_45():
    """prefer_aligned cost is maximum when device at 45°."""
    devices = [Device(id="a", name="A", bbox=(1.0, 1.0))]
    placements = [Placement(device_id="a", x=5, y=5, theta=math.pi / 4)]
    lab = Lab(width=10, depth=10)
    constraint = Constraint(type="soft", rule_name="prefer_aligned", weight=1.0)
    checker = MockCollisionChecker()
    cost = evaluate_constraints(devices, placements, lab, [constraint], checker)
    # (1 - cos(4 * pi/4)) / 2 = (1 - cos(pi)) / 2 = (1 - (-1)) / 2 = 1.0
    assert cost == pytest.approx(1.0)


def test_prefer_aligned_sums_over_devices():
    """Cost sums across all devices."""
    devices = [
        Device(id="a", name="A", bbox=(1.0, 1.0)),
        Device(id="b", name="B", bbox=(1.0, 1.0)),
    ]
    placements = [
        Placement(device_id="a", x=2, y=2, theta=math.pi / 4),  # cost = 1.0
        Placement(device_id="b", x=7, y=7, theta=math.pi / 4),  # cost = 1.0
    ]
    lab = Lab(width=10, depth=10)
    constraint = Constraint(type="soft", rule_name="prefer_aligned", weight=2.0)
    checker = MockCollisionChecker()
    cost = evaluate_constraints(devices, placements, lab, [constraint], checker)
    # 2 devices × 1.0 × weight 2.0 = 4.0
    assert cost == pytest.approx(4.0)


class TestGraduatedHardConstraints:
    """graduated 模式下硬约束返回比例惩罚而非 inf。"""

    def test_hard_reachability_graduated_finite(self):
        """graduated=True: 硬可达性返回有限惩罚。"""
        devices = [
            Device(id="arm", name="Arm", bbox=(0.2, 0.2), device_type="articulation"),
            Device(id="t", name="Target", bbox=(0.5, 0.5)),
        ]
        placements = [
            Placement("arm", 1.0, 1.0, 0.0),
            Placement("t", 4.0, 3.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="reachability",
                       params={"arm_id": "arm", "target_device_id": "t"}, weight=1.0)
        ]
        checker = MockCollisionChecker()
        reach = MockReachabilityChecker(arm_reach={"arm": 1.0})
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker, reach,
            graduated=True,
        )
        assert cost > 0
        assert not math.isinf(cost)

    def test_hard_reachability_binary_inf(self):
        """graduated=False: 硬可达性返回 inf。"""
        devices = [
            Device(id="arm", name="Arm", bbox=(0.2, 0.2), device_type="articulation"),
            Device(id="t", name="Target", bbox=(0.5, 0.5)),
        ]
        placements = [
            Placement("arm", 1.0, 1.0, 0.0),
            Placement("t", 4.0, 3.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="reachability",
                       params={"arm_id": "arm", "target_device_id": "t"}, weight=1.0)
        ]
        checker = MockCollisionChecker()
        reach = MockReachabilityChecker(arm_reach={"arm": 1.0})
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker, reach,
            graduated=False,
        )
        assert math.isinf(cost)

    def test_hard_min_spacing_graduated_sums_all_pairs(self):
        """graduated模式：min_spacing 对所有违规对求和（不只第一对）。"""
        devices = [
            Device(id="a", name="A", bbox=(0.5, 0.5)),
            Device(id="b", name="B", bbox=(0.5, 0.5)),
            Device(id="c", name="C", bbox=(0.5, 0.5)),
        ]
        # 三个设备间距都小于 min_gap=1.0
        placements = [
            Placement("a", 1.0, 2.0, 0.0),
            Placement("b", 1.3, 2.0, 0.0),  # OBB 边缘距 a 约 0.3
            Placement("c", 1.6, 2.0, 0.0),  # OBB 边缘距 b 约 0.3, 距 a 约 0.6
        ]
        constraints = [
            Constraint(type="hard", rule_name="min_spacing",
                       params={"min_gap": 1.0}, weight=1.0)
        ]
        checker = MockCollisionChecker()
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker,
            graduated=True,
        )
        # 应大于 0 且有限（累加多对违规）
        assert cost > 0
        assert not math.isinf(cost)

    def test_hard_min_spacing_binary_inf(self):
        """graduated=False: min_spacing 违规返回 inf。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 2.0, 0.0),
            Placement("b", 1.3, 2.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="min_spacing",
                       params={"min_gap": 1.0}, weight=1.0)
        ]
        checker = MockCollisionChecker()
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker,
            graduated=False,
        )
        assert math.isinf(cost)

    def test_hard_distance_less_than_graduated(self):
        """graduated模式：distance_less_than 硬约束返回比例惩罚。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 2.0, 0.0),
            Placement("b", 4.0, 2.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="distance_less_than",
                       params={"device_a": "a", "device_b": "b", "distance": 0.5},
                       weight=2.0)
        ]
        checker = MockCollisionChecker()
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker,
            graduated=True,
        )
        # HARD_MULTIPLIER(5) × weight(2) × overshoot > 0
        assert cost > 0
        assert not math.isinf(cost)

    def test_graduated_default_is_true(self):
        """不传 graduated 参数时默认使用 graduated 模式。"""
        devices = _make_devices()
        placements = [
            Placement("a", 1.0, 2.0, 0.0),
            Placement("b", 4.0, 2.0, 0.0),
        ]
        constraints = [
            Constraint(type="hard", rule_name="distance_less_than",
                       params={"device_a": "a", "device_b": "b", "distance": 0.5},
                       weight=1.0)
        ]
        checker = MockCollisionChecker()
        # 不指定 graduated — 默认应为 True → 有限惩罚
        cost = evaluate_constraints(
            devices, placements, _make_lab(), constraints, checker,
        )
        assert not math.isinf(cost)
