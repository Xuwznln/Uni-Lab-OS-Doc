"""Tests for broad_phase.py — 2 轴 sweep-and-prune 宽相碰撞检测。"""

from __future__ import annotations

import math
import random

import pytest

from ..broad_phase import broad_phase_device_pairs, sweep_and_prune_pairs
from ..models import Device, Placement


# ---------------------------------------------------------------------------
# 测试用辅助函数
# ---------------------------------------------------------------------------

def _make_device(device_id: str, w: float = 0.6, d: float = 0.4) -> Device:
    """创建简单测试设备。"""
    return Device(id=device_id, name=device_id, bbox=(w, d))


def _make_placement(
    device_id: str, x: float, y: float, theta: float = 0.0
) -> Placement:
    """创建简单测试放置。"""
    return Placement(device_id=device_id, x=x, y=y, theta=theta)


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------


class TestNoOverlap:
    """两台设备距离足够远，宽相不返回候选对。"""

    def test_no_overlap_returns_empty(self):
        """水平方向间距远大于 AABB 尺寸 → 0 候选对。"""
        devices = [_make_device("A", 1.0, 1.0), _make_device("B", 1.0, 1.0)]
        placements = [
            _make_placement("A", 0.0, 0.0),
            _make_placement("B", 10.0, 0.0),
        ]
        pairs = sweep_and_prune_pairs(devices, placements)
        assert pairs == []


class TestOverlapping:
    """两台设备 AABB 明显重叠。"""

    def test_overlapping_devices_returned(self):
        """两台 1×1 设备中心距 0.5m → 1 候选对。"""
        devices = [_make_device("A", 1.0, 1.0), _make_device("B", 1.0, 1.0)]
        placements = [
            _make_placement("A", 0.0, 0.0),
            _make_placement("B", 0.5, 0.0),
        ]
        pairs = sweep_and_prune_pairs(devices, placements)
        assert len(pairs) == 1
        assert pairs[0] == (0, 1)


class TestXOverlapYNoOverlap:
    """x 轴投影交叠但 y 轴不交叠，应被 y 轴检查剪枝。"""

    def test_x_overlap_y_no_overlap(self):
        """水平接近但垂直方向偏移足够大 → 0 候选对。"""
        devices = [_make_device("A", 2.0, 1.0), _make_device("B", 2.0, 1.0)]
        placements = [
            _make_placement("A", 0.0, 0.0),
            _make_placement("B", 0.5, 5.0),  # x 轴交叠但 y 轴相距很远
        ]
        pairs = sweep_and_prune_pairs(devices, placements)
        assert pairs == []


class TestTouchingDevices:
    """AABB 恰好接触（边缘距离 = 0）应作为候选对返回。"""

    def test_touching_devices_included(self):
        """两个 1×1 设备中心距恰好为 1.0（半宽 0.5 + 0.5 = 1.0），
        AABB 边界接触 → 应包含在候选对中（<= 判定）。"""
        devices = [_make_device("A", 1.0, 1.0), _make_device("B", 1.0, 1.0)]
        placements = [
            _make_placement("A", 0.0, 0.0),
            _make_placement("B", 1.0, 0.0),  # xmax_A = 0.5, xmin_B = 0.5 → 接触
        ]
        pairs = sweep_and_prune_pairs(devices, placements)
        # 接触算作潜在碰撞，安全起见需保留
        assert len(pairs) == 1
        assert pairs[0] == (0, 1)


class TestMultipleDevices:
    """4 台设备验证精确的候选对列表。"""

    def test_multiple_devices_correct_pairs(self):
        """排列 4 台设备，只有特定配对 AABB 交叠。

        布局（1×1 设备）：
          A(0,0)  B(0.8,0)  — A-B 交叠（中心距 0.8 < 1.0）
          C(0,5)            — 远离 A、B
          D(0.9,5)          — C-D 交叠（中心距 0.9 < 1.0）

        期望候选对: (A,B) 和 (C,D)。
        """
        devices = [
            _make_device("A", 1.0, 1.0),
            _make_device("B", 1.0, 1.0),
            _make_device("C", 1.0, 1.0),
            _make_device("D", 1.0, 1.0),
        ]
        placements = [
            _make_placement("A", 0.0, 0.0),
            _make_placement("B", 0.8, 0.0),
            _make_placement("C", 0.0, 5.0),
            _make_placement("D", 0.9, 5.0),
        ]
        pairs = sweep_and_prune_pairs(devices, placements)
        pair_set = set(pairs)
        assert (0, 1) in pair_set  # A-B
        assert (2, 3) in pair_set  # C-D
        assert len(pair_set) == 2


class TestRotatedDeviceAabb:
    """旋转设备导致 AABB 变大，命中候选对。"""

    def test_rotated_device_aabb(self):
        """一台窄长设备 (2.0×0.2)：
        - 未旋转时 AABB 半宽 = 1.0，两设备中心距 2.5 → 不交叠
        - 旋转 90° 后 AABB 半宽 = 0.1，半深 = 1.0 → 仍不交叠
        - 旋转 45° 后 AABB 半宽 ≈ (2*cos45 + 0.2*sin45)/2 ≈ 0.778
          但另一台放在 x=1.6，半宽 = 1.0
          所以 xmax_A = 0 + 0.778 = 0.778 < 1.6 - 1.0 = 0.6 → 不够

        更好的方案：用中心距 1.5，未旋转时不交叠，旋转后交叠。
        未旋转: half_w_A = 0.3 (bbox 0.6x2.0), half_w_B = 0.3
          A: xmax = 0 + 0.3 = 0.3, B: xmin = 1.5 - 0.3 = 1.2 → 不交叠
        旋转 90°: A 的 bbox (0.6, 2.0) → half_w = (0.6*0 + 2.0*1)/2 = 1.0
          A: xmax = 0 + 1.0 = 1.0, B: xmin = 1.5 - 0.3 = 1.2 → 仍不交叠

        用 bbox (0.4, 2.0)，间距 1.2：
        未旋转: half_w = 0.2, xmax_A = 0.2, xmin_B = 1.2 - 0.2 = 1.0 → 不交叠
        旋转 45°: half_w = (0.4*cos45 + 2.0*sin45)/2 = (0.283+1.414)/2 = 0.849
          xmax_A = 0.849, xmin_B = 1.2 - 0.2 = 1.0 → 不交叠

        间距 0.8：
        未旋转: xmax_A = 0.2, xmin_B = 0.8 - 0.2 = 0.6 → 不交叠 ✓
        旋转 45°: xmax_A = 0.849, xmin_B = 0.6 → 交叠 ✓ (0.849 > 0.6)
        y 轴: half_d_A_rot = (0.4*sin45 + 2.0*cos45)/2 = 0.849, half_d_B = 1.0
          ymax_A = 0.849, ymin_B = -1.0 → 交叠 ✓
        """
        dev_narrow = _make_device("narrow", 0.4, 2.0)
        dev_normal = _make_device("normal", 0.4, 2.0)
        # 未旋转：不交叠
        placements_no_rot = [
            _make_placement("narrow", 0.0, 0.0, theta=0.0),
            _make_placement("normal", 0.8, 0.0, theta=0.0),
        ]
        assert sweep_and_prune_pairs([dev_narrow, dev_normal], placements_no_rot) == []

        # narrow 旋转 45° → AABB 变大 → 交叠
        placements_rot = [
            _make_placement("narrow", 0.0, 0.0, theta=math.pi / 4),
            _make_placement("normal", 0.8, 0.0, theta=0.0),
        ]
        pairs = sweep_and_prune_pairs([dev_narrow, dev_normal], placements_rot)
        assert len(pairs) == 1


class TestOriginalIndices:
    """验证返回的索引对应 placements 原始顺序而非排序后顺序。"""

    def test_sorted_output_preserves_original_indices(self):
        """故意让 placements 按 x 坐标逆序排列，
        验证返回的索引仍是原始顺序。"""
        devices = [
            _make_device("A", 1.0, 1.0),
            _make_device("B", 1.0, 1.0),
            _make_device("C", 1.0, 1.0),
        ]
        # 逆序排列：C 在最左，A 在最右
        placements = [
            _make_placement("A", 5.0, 0.0),  # idx 0, 最右
            _make_placement("B", 4.5, 0.0),  # idx 1, 中间（与 A 交叠）
            _make_placement("C", 0.0, 0.0),  # idx 2, 最左（独立）
        ]
        pairs = sweep_and_prune_pairs(devices, placements)
        # A(idx=0) 和 B(idx=1) AABB 交叠，索引应为 (0, 1)
        assert len(pairs) == 1
        assert pairs[0] == (0, 1)

        # 同时验证 broad_phase_device_pairs 返回正确 device_id
        id_pairs = broad_phase_device_pairs(devices, placements)
        assert id_pairs == [("A", "B")]


class TestPairCountReduction:
    """大规模随机测试：宽相候选对数应远小于 N*(N-1)/2。"""

    def test_pair_count_reduction(self):
        """N=15 台设备随机放置在 10×10 实验室 → 候选对数显著少于全量。"""
        random.seed(42)
        n = 15
        devices = [_make_device(f"D{i}", 0.5, 0.5) for i in range(n)]
        placements = [
            _make_placement(f"D{i}", random.uniform(0, 10), random.uniform(0, 10))
            for i in range(n)
        ]
        pairs = sweep_and_prune_pairs(devices, placements)
        full_pairs = n * (n - 1) // 2  # = 105
        # 在 10×10 区域放 15 台 0.5×0.5 设备，交叠率应很低
        assert len(pairs) < full_pairs
        # 额外断言：候选对数不超过全量的一半（保守判定）
        assert len(pairs) < full_pairs * 0.5


class TestEdgeCases:
    """边界情况。"""

    def test_empty_input(self):
        """空列表 → 空结果。"""
        assert sweep_and_prune_pairs([], []) == []

    def test_single_device(self):
        """单台设备 → 无候选对。"""
        devices = [_make_device("A")]
        placements = [_make_placement("A", 0.0, 0.0)]
        assert sweep_and_prune_pairs(devices, placements) == []

    def test_identical_positions(self):
        """两台设备完全重叠 → 1 候选对。"""
        devices = [_make_device("A", 1.0, 1.0), _make_device("B", 1.0, 1.0)]
        placements = [
            _make_placement("A", 0.0, 0.0),
            _make_placement("B", 0.0, 0.0),
        ]
        pairs = sweep_and_prune_pairs(devices, placements)
        assert len(pairs) == 1
