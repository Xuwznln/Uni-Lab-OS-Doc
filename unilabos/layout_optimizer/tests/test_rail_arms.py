"""阶段二：布局机械臂与堆栈 place_arms_and_stacks 单测。

覆盖：靠墙模式第一台坐标、居中模式中轴线、多臂偏移与堆栈夹层、四角坐标自洽、
长边总长越界、房间长边沿 X 时的朝向、默认 thermo_stacker / 用户指定堆栈解析。
"""

from __future__ import annotations

import math

import pytest

from unilabos.layout_optimizer import rail_layout as rl
from unilabos.layout_optimizer.models import Device, Lab, Opening

# 机械臂：L=1.0、臂宽 0.2（bbox[1]=1.0 为长边，native_long='y'）
ARM = {"L": 1.0, "working_radius": 0.5, "bbox": [0.2, 1.0]}
# 固定堆栈：stack_h=max=0.4，保持测试确定性
STACK = {"bbox": [0.4, 0.4]}


def _approx_xy(actual, expected):
    assert actual[0] == pytest.approx(expected[0])
    assert actual[1] == pytest.approx(expected[1])


def _place(lab, n_arm, mode="near_wall", l_max=0.0, stack=STACK, arm=ARM, params=None, devices=None):
    return rl.place_arms_and_stacks(
        lab, n_arm, arm_model=arm, params=params, mode=mode,
        stack_model=stack, l_max=l_max, devices=devices,
    )


# ---------- 靠墙模式 ----------


def test_near_wall_first_arm():
    res = _place(Lab(width=3.0, depth=6.0), 1, mode="near_wall", l_max=0.5)
    arm0 = res["arms"][0]
    # 长边沿 Y：center=(short, long)；短边坐标 = d+b+L_max+臂宽/2 = 1.1
    _approx_xy(arm0.center, (1.1, 1.0))
    # 臂较长一侧到墙距离 = d+b+L_max = 1.0
    assert arm0.center[0] - ARM["bbox"][0] / 2 == pytest.approx(0.3 + 0.2 + 0.5)
    assert arm0.theta == pytest.approx(0.0)
    assert len(res["stacks"]) == 0


# ---------- 居中模式 ----------


def test_centered_first_arm():
    res = _place(Lab(width=3.0, depth=6.0), 1, mode="centered")
    arm0 = res["arms"][0]
    # bbox 中轴线与房间短边中轴线重合
    _approx_xy(arm0.center, (1.5, 1.0))
    # 短侧到墙仍为 a：臂沿长边底边 = a
    long_bottom = min(c[1] for c in arm0.corners)
    assert long_bottom == pytest.approx(0.5)


# ---------- 多臂偏移 + 堆栈夹层 ----------


def test_multi_arm_offset_and_stacks():
    res = _place(Lab(width=3.0, depth=6.0), 3, mode="near_wall", l_max=0.3)
    arms, stacks = res["arms"], res["stacks"]
    assert len(arms) == 3
    assert len(stacks) == 2
    step = 1.0 + 2 * 0.2 + 0.4  # L + 2e + stack_h = 1.8
    # 相邻臂中心间距（沿长边 Y）
    assert arms[1].center[1] - arms[0].center[1] == pytest.approx(step)
    assert arms[2].center[1] - arms[1].center[1] == pytest.approx(step)
    # 堆栈夹在相邻臂中点，且短边坐标与臂中轴线重合
    assert stacks[0].center[1] == pytest.approx((arms[0].center[1] + arms[1].center[1]) / 2)
    assert stacks[0].center[0] == pytest.approx(arms[0].center[0])
    assert stacks[0].bbox == (0.4, 0.4)


# ---------- 四角坐标自洽 ----------


def test_corners_consistent_with_center_bbox():
    res = _place(Lab(width=3.0, depth=6.0), 2, mode="near_wall", l_max=0.4)
    for arm in res["arms"]:
        xs = [c[0] for c in arm.corners]
        ys = [c[1] for c in arm.corners]
        cx = sum(xs) / 4
        cy = sum(ys) / 4
        _approx_xy(arm.center, (cx, cy))
        # 宽（短边 X）= 臂宽 0.2，长（长边 Y）= L = 1.0
        assert max(xs) - min(xs) == pytest.approx(0.2)
        assert max(ys) - min(ys) == pytest.approx(1.0)
    # 角顺序：左下/右下/右上/左上
    lb, rb, rt, lt = res["arms"][0].corners
    assert lb[0] < rb[0] and rt[1] > rb[1] and lt[0] < rt[0]


# ---------- 长边总长越界（与 n_max 一致） ----------


def test_total_length_within_long_side():
    lab = Lab(width=3.0, depth=6.0)
    res = _place(lab, 3, mode="near_wall", l_max=0.3)
    arms = res["arms"]
    top = max(c[1] for c in arms[-1].corners)
    # 末臂顶边 + a ≤ 房间长边
    assert top + 0.5 <= max(lab.width, lab.depth) + 1e-9
    # 等于解析总长 2a + nL + (n-1)(2e+stack_h)
    total = 2 * 0.5 + 3 * 1.0 + 2 * (2 * 0.2 + 0.4)
    assert top + 0.5 == pytest.approx(total)


# ---------- 房间长边沿 X 时的朝向 ----------


def test_long_side_along_x_rotates_arm():
    res = _place(Lab(width=6.0, depth=3.0), 1, mode="near_wall", l_max=0.5)
    arm0 = res["arms"][0]
    # long_axis='x'：center=(long, short)
    _approx_xy(arm0.center, (1.0, 1.1))
    # native_long='y' 与 long_axis='x' 不一致 → 旋转 90°
    assert arm0.theta == pytest.approx(math.pi / 2)
    # 沿长边 X 的尺寸 = L = 1.0，沿短边 Y = 臂宽 0.2
    xs = [c[0] for c in arm0.corners]
    ys = [c[1] for c in arm0.corners]
    assert max(xs) - min(xs) == pytest.approx(1.0)
    assert max(ys) - min(ys) == pytest.approx(0.2)


# ---------- n_arm < 1 ----------


def test_zero_arms_returns_empty():
    res = _place(Lab(width=3.0, depth=6.0), 0)
    assert res == {"arms": [], "stacks": []}


# ---------- 堆栈解析 ----------


def test_default_stack_is_thermo_stacker():
    fp = rl._load_footprints_safe()
    if rl.DEFAULT_STACK_MODEL not in fp:
        pytest.skip("footprints.json 不可用或无 thermo_stacker")
    expected = tuple(float(x) for x in fp[rl.DEFAULT_STACK_MODEL]["bbox"])
    res = _place(Lab(width=3.0, depth=8.0), 2, mode="near_wall", l_max=0.3,
                 stack=rl.DEFAULT_STACK_MODEL)
    assert res["stacks"][0].bbox == expected


def test_user_specified_stack_dict():
    res = _place(Lab(width=3.0, depth=8.0), 2, stack={"bbox": [0.5, 0.7]}, l_max=0.3)
    assert res["stacks"][0].bbox == (0.5, 0.7)
    # stack_h=max=0.7 → step=L+2e+0.7=2.1
    assert res["arms"][1].center[1] - res["arms"][0].center[1] == pytest.approx(1.0 + 0.4 + 0.7)


def test_resolve_stack_order_devices_keyword():
    # 未知 stack_model id + devices 含堆栈关键词 → 命中 device
    devs = [Device(id="my_buffer_x", name="buffer", bbox=(0.3, 0.9), openings=[Opening(direction=(0, -1))])]
    bbox, openings = rl._resolve_stack("not_in_footprints", devs)
    assert bbox == (0.3, 0.9)
    assert openings == [(0, -1)]
