"""设备接驳点坐标（v1：placements.center；后续可扩展 dock_for 网格搜索）。"""

from __future__ import annotations

from typing import Dict, Tuple


def resolve_device_xy(placement: Dict) -> Tuple[float, float]:
    """返回 AGV 停靠坐标（米，lab 局部系）。

    v1 使用 placements.center；与 layout-optimizer animate_trajectory 的 dock_for 对齐
    可在后续版本移植 grid/reachability 后替换本函数。
    """
    center = placement.get("center") or [0.0, 0.0]
    return float(center[0]), float(center[1])


def build_device_positions(placements: list[Dict]) -> Dict[str, Tuple[float, float]]:
    """instance_id → (x_m, y_m)。"""
    out: Dict[str, Tuple[float, float]] = {}
    for p in placements:
        iid = str(p.get("instance_id") or "")
        if not iid:
            continue
        out[iid] = resolve_device_xy(p)
    return out
