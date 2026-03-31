"""2 轴 sweep-and-prune 宽相碰撞检测。

对每个设备计算旋转后的 AABB，先沿 x 轴排序并剪枝，
再用 y 轴交叠过滤。返回候选碰撞对（索引对列表），
供后续 OBB SAT 精确检测使用。
"""

from __future__ import annotations

from .models import Device, Placement


def sweep_and_prune_pairs(
    devices: list[Device],
    placements: list[Placement],
) -> list[tuple[int, int]]:
    """2 轴 sweep-and-prune，返回 AABB 交叠的索引对。

    Args:
        devices: 设备列表，与 placements 一一对应。
        placements: 布局位姿列表。

    Returns:
        候选碰撞对列表，每个元素为 (i, j)，
        i < j，索引对应 placements 原始顺序。
    """
    n = len(devices)
    if n < 2:
        return []

    # --- 计算每个设备旋转后的 AABB ---
    aabbs: list[tuple[float, float, float, float]] = []
    for dev, pl in zip(devices, placements):
        hw, hd = pl.rotated_bbox(dev)
        aabbs.append((pl.x - hw, pl.x + hw, pl.y - hd, pl.y + hd))

    # --- 按 xmin 排序，保留原始索引映射 ---
    sorted_indices = sorted(range(n), key=lambda k: aabbs[k][0])

    # --- 扫描 x 轴，y 轴过滤 ---
    candidates: list[tuple[int, int]] = []
    for si in range(len(sorted_indices)):
        i = sorted_indices[si]
        x_min_i, x_max_i, y_min_i, y_max_i = aabbs[i]
        for sj in range(si + 1, len(sorted_indices)):
            j = sorted_indices[sj]
            x_min_j, _x_max_j, y_min_j, y_max_j = aabbs[j]
            # 由于按 xmin 排序，x_min_j >= x_min_i
            if x_min_j > x_max_i:
                break  # 后续设备 xmin 更大，不可能与 i 在 x 轴交叠
            # x 轴交叠确认，检查 y 轴
            if y_min_i <= y_max_j and y_min_j <= y_max_i:
                # 保证输出 (min_idx, max_idx) 方便去重和测试
                pair = (min(i, j), max(i, j))
                candidates.append(pair)

    return candidates


def broad_phase_device_pairs(
    devices: list[Device],
    placements: list[Placement],
) -> list[tuple[str, str]]:
    """返回候选碰撞对的 device_id 字符串元组列表。"""
    index_pairs = sweep_and_prune_pairs(devices, placements)
    return [(placements[i].device_id, placements[j].device_id) for i, j in index_pairs]
