"""阶段一：可行性检查 check_feasibility 单测。

覆盖：面积、台数公式边界、长边不等式/n_max、短边不等式 1/2、可达性、报告完整性。
通过 arm_model 固定机械臂几何（L/工作半径/bbox），用仪器 bbox + 朝向控制 W/L_out。
"""

from __future__ import annotations

import pytest

from unilabos.layout_optimizer import rail_layout as rl
from unilabos.layout_optimizer.models import Device, Lab, Opening

# 固定机械臂：导轨长 L=1.0，工作半径 0.5，bbox 0.2×1.0（臂宽 0.2，占地 0.2㎡）
ARM = {"L": 1.0, "working_radius": 0.5, "bbox": [0.2, 1.0]}


def _inst(dev_id: str, w: float, depth: float, direction=(0.0, -1.0)) -> Device:
    """构造仪器：默认朝向 -Y → W_rail=w(沿X)、L_out=depth(沿Y)。"""
    return Device(id=dev_id, name=dev_id, bbox=(w, depth), openings=[Opening(direction=direction)])


# 固定堆栈几何（stack_h=max=0.4），避免依赖 footprints.json 内容，保持测试确定性
STACK = {"bbox": [0.4, 0.4]}


def _check(devices, order, lab, arm=ARM, params=None, stack=STACK):
    return rl.check_feasibility(
        devices, order, lab, arm_model=arm, params=params, stack_model=stack,
    )


# ---------- 面积 ----------


def test_area_feasible():
    devs = [_inst("i1", 0.4, 0.4), _inst("i2", 0.4, 0.4)]
    rep = _check(devs, ["i1", "i2"], Lab(width=5.0, depth=5.0))
    assert rep.feasible is True
    assert not any("总面积" in r for r in rep.reasons)


def test_area_exceeded():
    devs = [_inst("i1", 0.4, 0.4), _inst("i2", 0.4, 0.4)]
    rep = _check(devs, ["i1", "i2"], Lab(width=0.6, depth=0.6))
    assert rep.feasible is False
    assert any("总面积" in r for r in rep.reasons)


# ---------- 台数公式边界 ----------


@pytest.mark.parametrize(
    "w, expected_n",
    [
        (2.0, 1),   # sum_w 恰好 = 2nL（n=1）
        (2.2, 2),   # 刚超过 2(n-1)L=2 → n=2
        (4.2, 3),   # 刚超过 2*2*L=4 → n=3
    ],
)
def test_n_arm_boundary(w, expected_n):
    devs = [_inst("i1", w, 0.2)]
    rep = _check(devs, ["i1"], Lab(width=10.0, depth=10.0))
    assert rep.n_arm == expected_n
    assert rep.n_stack == expected_n - 1


# ---------- 长边不等式 / n_max ----------


def test_long_side_fits():
    # n_arm=2（w=2.5 → sum_w=2.5 → ceil(1.25)=2），长边 4.0 → n_max=2
    devs = [_inst("i1", 2.5, 0.3)]
    rep = _check(devs, ["i1"], Lab(width=3.0, depth=4.0))
    assert rep.n_arm == 2
    assert rep.n_max == 2
    assert rep.feasible is True


def test_long_side_too_short():
    # 同样 n_arm=2，但长边 3.0 → n_max=1 < 2 → 不可行
    devs = [_inst("i1", 2.5, 0.3)]
    rep = _check(devs, ["i1"], Lab(width=3.0, depth=3.0))
    assert rep.n_arm == 2
    assert rep.n_max == 1
    assert rep.feasible is False
    assert any("长边" in r for r in rep.reasons)


# ---------- 短边不等式 1 / 2 ----------


def test_short_ineq2_fail_infeasible():
    # L_out=5.0 → 2d+b+L_max+臂宽 = 6.0 > 短边 3.0
    devs = [_inst("i1", 0.4, 5.0)]
    rep = _check(devs, ["i1"], Lab(width=3.0, depth=8.0))
    assert rep.feasible is False
    assert any("短边方向放不下" in r for r in rep.reasons)


def test_short_ineq1_fail_near_wall_hint():
    # ineq2 成立但 ineq1 不成立 → 仍可行，但 mode_hint=near_wall
    devs = [_inst("i1", 0.4, 0.8), _inst("i2", 0.4, 0.8)]
    rep = _check(devs, ["i1", "i2"], Lab(width=2.0, depth=6.0))
    assert rep.feasible is True
    assert rep.mode_hint == "near_wall"


def test_short_ineq1_ok_centered_hint():
    devs = [_inst("i1", 0.4, 0.3), _inst("i2", 0.4, 0.3)]
    rep = _check(devs, ["i1", "i2"], Lab(width=3.0, depth=6.0))
    assert rep.feasible is True
    assert rep.mode_hint == "centered"


# ---------- 可达性 ----------


def test_reach_b_exceeds_radius():
    arm = {"L": 1.0, "working_radius": 0.3, "bbox": [0.2, 1.0]}
    params = rl.RailParams.from_overrides({"b": 0.35})
    devs = [_inst("i1", 0.4, 0.3)]
    rep = _check(devs, ["i1"], Lab(width=5.0, depth=5.0), arm=arm, params=params)
    assert rep.feasible is False
    assert any(r.startswith("距离参数 b=") for r in rep.reasons)


def test_reach_e_exceeds_radius():
    arm = {"L": 1.0, "working_radius": 0.3, "bbox": [0.2, 1.0]}
    params = rl.RailParams.from_overrides({"e": 0.4})
    devs = [_inst("i1", 0.4, 0.3)]
    rep = _check(devs, ["i1"], Lab(width=5.0, depth=5.0), arm=arm, params=params)
    assert rep.feasible is False
    assert any(r.startswith("距离参数 e=") for r in rep.reasons)


# ---------- 报告完整性 ----------


def test_report_reasons_suggestions_paired():
    devs = [_inst("i1", 0.4, 5.0)]
    rep = _check(devs, ["i1"], Lab(width=3.0, depth=8.0))
    assert rep.feasible is False
    assert len(rep.reasons) > 0
    assert len(rep.reasons) == len(rep.suggestions)
    assert all(r and s for r, s in zip(rep.reasons, rep.suggestions))


# ---------- 朝向解析 ----------


def test_opening_along_x_swaps_dims():
    # 朝向沿 X：L_out=width(w)，W_rail=depth → 大 width 走 L_out 撑短边
    devs = [_inst("i1", 5.0, 0.4, direction=(1.0, 0.0))]
    rep = _check(devs, ["i1"], Lab(width=3.0, depth=8.0))
    # L_out=5.0 → 短边不等式2 失败
    assert rep.feasible is False
    assert any("短边方向放不下" in r for r in rep.reasons)


# ---------- 异常 ----------


def test_missing_instrument_raises():
    with pytest.raises(ValueError):
        _check([_inst("i1", 0.4, 0.3)], ["missing"], Lab(width=5.0, depth=5.0))


def test_no_arm_raises():
    devs = [_inst("i1", 0.4, 0.3)]
    with pytest.raises(ValueError):
        rl.check_feasibility(devs, ["i1"], Lab(width=5.0, depth=5.0), arm_model=None)
