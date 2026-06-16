"""阶段三：按实验步骤顺序布置各仪器单测。

覆盖：单侧贪心装箱边界、四角反推坐标（左下自下而上 / 右上自上而下）、朝向 theta、
多退少补（删空臂 / 补臂重排）、端到端 11 台流程、环境障碍与越界校验。
"""

from __future__ import annotations

import math

import pytest

from unilabos.layout_optimizer import rail_layout as rl
from unilabos.layout_optimizer.models import Device, Lab, Obstacle, Opening

# 固定机械臂：导轨长 L=1.0，工作半径 0.5，bbox 0.2×1.0（臂宽 0.2，native_long='y'）
ARM = {"L": 1.0, "working_radius": 0.5, "bbox": [0.2, 1.0]}
# 固定堆栈：stack_h=max=0.4，保持几何确定
STACK = {"bbox": [0.4, 0.4]}


def _inst(dev_id: str, w: float, depth: float, direction=(0.0, -1.0)) -> Device:
    """构造仪器：默认朝向 -Y → W_rail=w(沿导轨)、L_out=depth(伸出)。"""
    return Device(id=dev_id, name=dev_id, bbox=(w, depth), openings=[Opening(direction=direction)])


def _arms(lab, n, mode="near_wall", l_max=0.4):
    return rl.place_arms_and_stacks(
        lab, n, arm_model=ARM, mode=mode, stack_model=STACK, l_max=l_max,
    )["arms"]


# ---------- 单侧贪心装箱边界 ----------


def test_pack_single_side_boundary():
    arms = _arms(Lab(width=3.0, depth=6.0), 1, l_max=0.4)
    # w=0.3, c=0.3: i=2 -> 0.9 ≤ 1.0；i=3 -> 1.5 > 1.0，左侧只装 2 台
    devs = [_inst(f"i{k}", 0.3, 0.4) for k in range(3)]
    placements = rl.assign_and_place_instruments(arms, devs)
    left = [p for p in placements if p.side == "left"]
    right = [p for p in placements if p.side == "right"]
    assert [p.device_id for p in left] == ["i0", "i1"]
    assert [p.device_id for p in right] == ["i2"]


# ---------- 四角反推坐标 ----------


def test_left_side_coords_from_left_bottom():
    arms = _arms(Lab(width=3.0, depth=6.0), 1, l_max=0.4)
    arm = arms[0]
    # 臂左下角 LB=(0.9,0.5)，左边缘 x=0.9
    devs = [_inst("i0", 0.3, 0.4), _inst("i1", 0.3, 0.4), _inst("i2", 0.3, 0.4)]
    placements = rl.assign_and_place_instruments(arms, devs)
    i0, i1 = placements[0], placements[1]
    # 左侧：中心 x = 左边缘 0.9 - b 0.2 - l_out/2 0.2 = 0.5
    assert i0.center[0] == pytest.approx(0.5)
    assert i1.center[0] == pytest.approx(0.5)
    # 右边缘（朝臂）对齐 arm_left - b = 0.7
    assert i0.center[0] + 0.4 / 2 == pytest.approx(0.9 - 0.2)
    # 自下而上：i0 在下，i1 在上
    assert i0.center[1] == pytest.approx(0.65)
    assert i1.center[1] == pytest.approx(1.25)
    assert i1.center[1] > i0.center[1]


def test_right_side_coords_from_right_top():
    arms = _arms(Lab(width=3.0, depth=6.0), 1, l_max=0.4)
    devs = [_inst("i0", 0.3, 0.4), _inst("i1", 0.3, 0.4), _inst("i2", 0.3, 0.4)]
    placements = rl.assign_and_place_instruments(arms, devs)
    i2 = placements[2]
    # 右侧：臂右上角 RT=(1.1,1.5)，中心 x = 右边缘 1.1 + b 0.2 + l_out/2 0.2 = 1.5
    assert i2.center[0] == pytest.approx(1.5)
    # 左边缘（朝臂）对齐 arm_right + b = 1.3
    assert i2.center[0] - 0.4 / 2 == pytest.approx(1.1 + 0.2)
    # 自上而下：第一台贴顶 RT
    assert i2.center[1] == pytest.approx(1.35)


# ---------- 朝向 theta ----------


def test_orientation_faces_rail():
    arms = _arms(Lab(width=3.0, depth=6.0), 1, l_max=0.4)
    devs = [_inst("i0", 0.3, 0.4), _inst("i1", 0.3, 0.4), _inst("i2", 0.3, 0.4)]
    placements = rl.assign_and_place_instruments(arms, devs)
    left0, right0 = placements[0], placements[2]
    # 左侧开口朝 +x（指向导轨）：(0,-1) 旋转 +90° → (1,0)
    assert left0.theta == pytest.approx(math.pi / 2)
    # 右侧开口朝 -x：(0,-1) 旋转 -90° → (-1,0)
    assert right0.theta == pytest.approx(-math.pi / 2)

    def _rot(direction, theta):
        c, s = math.cos(theta), math.sin(theta)
        return (direction[0] * c - direction[1] * s, direction[0] * s + direction[1] * c)

    lx, ly = _rot((0.0, -1.0), left0.theta)
    assert (lx, ly) == pytest.approx((1.0, 0.0))
    rx, ry = _rot((0.0, -1.0), right0.theta)
    assert (rx, ry) == pytest.approx((-1.0, 0.0))


# ---------- 多退少补：删空臂 ----------


def test_trim_empty_trailing_arm():
    # 4 台 w=0.3（c=0）：每侧装 1 台凑不满？实则每臂左右各 2 台 → 1 臂即够，裁掉多余臂
    lab = Lab(width=3.0, depth=8.0)
    devs = [_inst(f"i{k}", 0.3, 0.3) for k in range(4)]
    result = rl.layout_rail(
        devs, [d.id for d in devs], lab, arm_model=ARM, stack_model=STACK,
    )
    # 粗估 n_arm=2（sum_w 用 (k-1)c 高估），实际 1 臂装满 4 台 → 退到 1 臂
    assert result.report.n_arm == 2
    assert len(result.arms) == 1
    assert len(result.stacks) == 0
    assert len(result.placements) == 4
    assert result.leftover == []


# ---------- 多退少补：补臂重排 ----------


def test_add_arm_when_leftover():
    # c=0 + w=0.51：每侧仅装 1 台 → 每臂 2 台。6 台粗估 n=2 但实际需 3 臂
    lab = Lab(width=3.0, depth=8.0)
    params = rl.RailParams.from_overrides({"c": 0.0})
    devs = [_inst(f"i{k}", 0.51, 0.3) for k in range(6)]
    result = rl.layout_rail(
        devs, [d.id for d in devs], lab, arm_model=ARM, params=params, stack_model=STACK,
    )
    assert result.report.n_arm == 2  # 粗估低于实际
    assert len(result.arms) == 3  # 补臂到 3
    assert len(result.placements) == 6
    assert result.leftover == []


# ---------- 端到端：11 台线性流程 → 2 臂 ----------


def test_end_to_end_11_instruments():
    lab = Lab(width=3.0, depth=6.0)
    params = rl.RailParams.from_overrides({"c": 0.2})
    ids = [f"i{k}" for k in range(11)]
    devs = [_inst(i, 0.15, 0.2) for i in ids]
    result = rl.layout_rail(
        devs, ids, lab, arm_model=ARM, params=params, stack_model=STACK,
    )
    assert len(result.placements) == 11
    assert len(result.arms) == 2
    assert result.leftover == []
    # 流程顺序连续：placements 顺序与输入顺序一致
    assert [p.device_id for p in result.placements] == ids
    # 无越界 / 障碍冲突
    assert result.conflicts == []


# ---------- 环境障碍碰撞 ----------


def test_validate_obstacle_collision():
    arms = _arms(Lab(width=3.0, depth=6.0), 1, l_max=0.4)
    devs = [_inst("i0", 0.3, 0.4)]
    placements = rl.assign_and_place_instruments(arms, devs)
    # i0 中心约 (0.5, 0.65)，放一个覆盖它的障碍
    obstacle = Obstacle(x=0.3, y=0.45, width=0.4, depth=0.4)
    conflicts = rl.validate_placements(placements, Lab(width=3.0, depth=6.0), [obstacle])
    assert any(c.kind == "obstacle_collision" for c in conflicts)


def test_validate_no_collision_when_clear():
    arms = _arms(Lab(width=3.0, depth=6.0), 1, l_max=0.4)
    devs = [_inst("i0", 0.3, 0.4)]
    placements = rl.assign_and_place_instruments(arms, devs)
    conflicts = rl.validate_placements(placements, Lab(width=3.0, depth=6.0), [])
    assert conflicts == []


# ---------- 越界守卫 ----------


def test_validate_out_of_bounds():
    p = rl.InstrumentPlacement(device_id="i0", center=(0.1, 0.1), theta=0.0, bbox=(0.4, 0.4))
    conflicts = rl.validate_placements([p], Lab(width=3.0, depth=3.0))
    assert any(c.kind == "out_of_bounds" for c in conflicts)


# ---------- 空输入 ----------


def test_empty_inputs():
    assert rl.assign_and_place_instruments([], []) == []
    assert rl.assign_and_place_instruments(_arms(Lab(3.0, 6.0), 1), []) == []
    assert rl.validate_placements([], Lab(width=3.0, depth=6.0)) == []


# ---------- 不可行直接返回原因 ----------


def test_layout_infeasible_returns_conflicts():
    # 仪器伸出过长 → 短边不等式 2 不成立 → 不可行
    devs = [_inst("i0", 0.3, 5.0)]
    result = rl.layout_rail(devs, ["i0"], Lab(width=3.0, depth=8.0), arm_model=ARM, stack_model=STACK)
    assert result.report.feasible is False
    assert result.placements == []
    assert any(c.kind == "infeasible" for c in result.conflicts)
    assert result.leftover == ["i0"]
