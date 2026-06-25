"""instance_id → RMF-safe waypoint 名（#18 §9.3 / #21 §3.3）。"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, List


# 常见设备类型片段 → 可读 slug 片段（可逐步扩充）
_TYPE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("96孔", "96"),
    ("液体工作站", "liquid"),
    ("固体分配", "solid_disp"),
    ("称量工作站", "weigh"),
    ("恒温振荡反应板", "shaker"),
    ("酸碱淬灭工作站", "quench"),
    ("液液萃取", "lle"),
    ("过滤工作站", "filter"),
    ("惰性气体置换", "inert_gas"),
    ("平行离心浓缩仪", "conc"),
    ("自动过滤", "filter_auto"),
    ("Celite", "celite"),
)


def _replica_index(instance_id: str) -> str:
    match = re.search(r"_(\d+)$", instance_id)
    return match.group(1) if match else "0"


def slug_device_type(device_type: str) -> str:
    """把中文/混合 device_type 转为 RMF-safe 短 slug。"""
    text = device_type or ""
    for cn, en in _TYPE_REPLACEMENTS:
        text = text.replace(cn, en)
    ascii_slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    if len(ascii_slug) >= 2:
        return ascii_slug[:28]
    digest = hashlib.sha1(device_type.encode("utf-8")).hexdigest()[:8]
    return f"t{digest}"


def instance_to_waypoint_name(instance_id: str, device_type: str = "") -> str:
    """生成唯一 RMF waypoint 名：wp_<type_slug>_<replica>。"""
    idx = _replica_index(instance_id)
    type_slug = slug_device_type(device_type or instance_id)
    return f"wp_{type_slug}_{idx}"


def build_instance_waypoint_map(placements: Iterable[Dict]) -> Dict[str, str]:
    """instance_id → waypointName；重名时追加后缀保证唯一。"""
    mapping: Dict[str, str] = {}
    used: set[str] = set()
    for item in placements:
        iid = str(item.get("instance_id") or "")
        if not iid:
            continue
        base = instance_to_waypoint_name(iid, str(item.get("device_type") or ""))
        name = base
        suffix = 1
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        mapping[iid] = name
    return mapping
