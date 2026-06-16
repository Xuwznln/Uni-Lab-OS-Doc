"""阶段0（M0 公共前置）骨架测试。

只校验公共基础设施：默认参数、参数合并/覆盖、可达性约定、数据结构、
以及三阶段函数骨架尚未实现时抛 NotImplementedError。
具体算法行为留待 M1~M3 的对应测试。
"""

from __future__ import annotations

import math

import pytest

from unilabos.layout_optimizer import rail_layout as rl
from unilabos.layout_optimizer.models import Lab


def test_default_params_values():
    assert rl.DEFAULT_PARAMS == {"a": 0.5, "b": 0.2, "c": 0.3, "d": 0.3, "e": 0.2}
    assert rl.DEFAULT_WORKING_RADIUS == 0.3


def test_params_default_fallback():
    p = rl.RailParams.from_overrides(None)
    assert (p.a, p.b, p.c, p.d, p.e) == (0.5, 0.2, 0.3, 0.3, 0.2)
    assert p.working_radius == 0.3


def test_params_override_merges_and_ignores_none():
    p = rl.RailParams.from_overrides({"a": 0.6, "b": None, "working_radius": 0.4})
    assert p.a == 0.6  # 覆盖生效
    assert p.b == 0.2  # None 被忽略，回落默认
    assert p.working_radius == 0.4


def test_reachability_convention_ok():
    # 默认 b/e < 工作半径 → 无违反
    assert rl.RailParams.from_overrides(None).reachability_violations() == []


def test_reachability_violation_detected():
    p = rl.RailParams.from_overrides({"b": 0.35, "e": 0.5, "working_radius": 0.3})
    reasons = p.reachability_violations()
    assert len(reasons) == 2
    assert any("b=" in r for r in reasons)
    assert any("e=" in r for r in reasons)


def test_conflicts_to_dicts():
    conflicts = [rl.RailConflict(kind="area", message="msg", suggestion="sug")]
    dicts = rl.conflicts_to_dicts(conflicts)
    assert dicts == [{"kind": "area", "message": "msg", "suggestion": "sug"}]


def test_dataclasses_constructible():
    arm = rl.ArmPlacement(
        id="arm1", center=(1.0, 2.0), theta=0.0,
        corners=[(0, 0), (1, 0), (1, 1), (0, 1)], bbox=(0.2, 1.0),
    )
    assert arm.corners[0] == (0, 0)
    stack = rl.StackPlacement(id="s1", center=(1.0, 1.5), bbox=(0.3, 0.3))
    assert stack.bbox == (0.3, 0.3)
    inst = rl.InstrumentPlacement(device_id="d", center=(0.0, 0.0), theta=math.pi / 2)
    assert inst.side == "left"
    report = rl.FeasibilityReport(feasible=True, n_arm=2, n_stack=1)
    assert report.mode_hint == "near_wall"


@pytest.mark.parametrize(
    "call",
    [
        # 阶段一/二已实现（M1/M2），此处仅校验阶段三仍为骨架。
        lambda lab: rl.assign_and_place_instruments([], []),
        lambda lab: rl.validate_placements([], lab),
    ],
)
def test_stage_functions_not_implemented(call):
    lab = Lab(width=4.0, depth=6.0)
    with pytest.raises(NotImplementedError):
        call(lab)
