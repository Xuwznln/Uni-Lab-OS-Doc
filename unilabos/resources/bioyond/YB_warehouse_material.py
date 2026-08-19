"""宜宾电解液仓库物料展示约定：单瓶升载架、入库数量单位、未知类型占位尺寸、库存快照解析。

电解液试剂/加样头均在 stock-material typeMode=2；枪头盒等耗材在 typeMode=0。
首版监控只拉 0+2，typeMode=1（样品）不计入面板。
"""

from __future__ import annotations

import json
import types
from typing import Any, Iterable, List, Sequence, Tuple

# 入库到板位仓库时，LIMS 单瓶类型要换成对应载架，否则前端只显示一个点
BOTTLE_TO_CARRIER_CLASS = {
    "YB_DosingHead_L": "YB_DosingHead_L_Carrier",
    "YB_NormalLiq_100mL_Bottle": "YB_NormalLiq_100mL_Carrier",
    "YB_NormalLiq_250mL_Bottle": "YB_NormalLiq_250mL_Carrier",
    "YB_HighVis_100mL_Bottle": "YB_HighVis_100mL_Carrier",
    "YB_HighVis_250mL_Bottle": "YB_HighVis_250mL_Carrier",
    "YB_PrepBottle_15mL": "YB_PrepBottle_15mL_Carrier",
    "YB_PrepBottle_60mL": "YB_PrepBottle_60mL_Carrier",
    "YB_Vial_5mL": "YB_Vial_5mL_Carrier",
    "YB_Vial_20mL": "YB_Vial_20mL_Carrier",
}


def resolve_warehouse_material_class(class_name: str) -> str:
    """仓库槽位用板/载架展示；单瓶类名提升为对应 Carrier。"""
    return BOTTLE_TO_CARRIER_CLASS.get(class_name, class_name)


# LIMS 单位 → VolumeTracker 第三元。两元组会默认成 ul，粉末/试剂液瓶入库数量实际是 g。
_BIOYOND_UNIT_TO_TRACKER = {
    "g": "g",
    "克": "g",
    "ug": "ug",
    "μg": "ug",
    "µg": "ug",
    "微克": "ug",
    "mg": "mg",
    "毫克": "mg",
    "kg": "kg",
    "千克": "kg",
    "ul": "ul",
    "μl": "ul",
    "µl": "ul",
    "μL": "ul",
    "µL": "ul",
    "微升": "ul",
    "ml": "ml",
    "毫升": "ml",
    "l": "l",
    "升": "l",
    "个": "个",
}
_TRACKER_MASS_TYPE_KEYWORDS = ("加样头", "普通液", "高粘液", "液体试剂", "粉末")
_TRACKER_VOLUME_TYPE_KEYWORDS = ("样品瓶", "分装小瓶")


def bioyond_tracker_unit(material: dict, type_name: str | None = None) -> str:
    """从 LIMS 物料/子物料取 VolumeTracker 单位；缺省时粉末加样头和试剂液瓶按 g。"""
    raw = str(material.get("unit") or "").strip()
    mapped = _BIOYOND_UNIT_TO_TRACKER.get(raw) or _BIOYOND_UNIT_TO_TRACKER.get(raw.lower())
    resolved_type = str(type_name if type_name is not None else material.get("typeName") or "")
    if any(k in resolved_type for k in _TRACKER_MASS_TYPE_KEYWORDS):
        if mapped in ("g", "mg", "ug", "kg"):
            return mapped
        return "g"
    if mapped:
        return mapped
    if any(k in resolved_type for k in _TRACKER_VOLUME_TYPE_KEYWORDS):
        return "ul"
    return "g"


def apply_warehouse_placeholder_size(resource: Any) -> None:
    """未知类型 RegularContainer 默认尺寸为 0，入库后设成可见板尺寸。"""
    if resource is None:
        return
    sx = float(getattr(resource, "_size_x", 0) or 0)
    if sx > 1:
        return
    try:
        resource._size_x = 127.0
        resource._size_y = 86.0
        resource._size_z = 25.0
    except Exception:
        pass


def lookup_mapped_class(type_mapping: dict | None, type_name: str | None) -> str:
    """material_type_mappings 的显示名 → 类名；大小写不敏感，对不上则 RegularContainer。"""
    if not type_name:
        return "RegularContainer"
    needle = str(type_name).casefold()
    for key, value in (type_mapping or {}).items():
        display = value[0] if isinstance(value, (tuple, list)) and value else None
        if display and str(display).casefold() == needle:
            return str(key)
    return "RegularContainer"


def lookup_reverse_type_info(reverse_mapping: dict, type_name: str | None):
    """反向表 typeName → (className, uuid)；大小写不敏感。"""
    if not type_name:
        return None
    if type_name in reverse_mapping:
        return reverse_mapping[type_name]
    needle = str(type_name).casefold()
    for key, value in reverse_mapping.items():
        if str(key).casefold() == needle:
            return value
    return None


# TipTopCountInfo 规格码：21=50µL、22=1000µL、23=5000µL
TIP_CODE_50UL = "21"
TIP_CODE_1000UL = "22"
TIP_CODE_5000UL = "23"

# 枪头汇总仓库 UUID（与 0610 / warehouse_mapping 同一套）
TIP_WH_STACK = "3a19deab-d5cb-e4be-51dc-9c7d68eba113"  # 站内Tip头盒堆栈
TIP_WH_PREP = "3a1da6ce-f67c-6037-d75f-af34ba291752"  # 配液站内Tip头盒位置库
TIP_WH_DISPENSE = "3a1dad64-3a6a-20b5-5cd9-9a83ad4c952d"  # 分液站内Tip头盒位置库
TIP_WH_PREP_50 = "3a1e9ec0-7259-436a-05d4-fefe0da8ff71"  # 配液站内50uLTip盒位置库

TIP_WAREHOUSE_UUID_PREP = {TIP_WH_STACK, TIP_WH_PREP, TIP_WH_PREP_50}
TIP_WAREHOUSE_UUID_DISPENSE = {TIP_WH_STACK, TIP_WH_DISPENSE}

# 单格位置库的库位 id，mapping 缺失时仍能按 locations[].id 计入
_TIP_LOCATION_FALLBACK_PREP = {
    "3a1da6ce-f67f-6707-18bf-cc9aaacf437e",
    "3a1e9ec0-725a-195e-b075-77508be4c095",
}
_TIP_LOCATION_FALLBACK_DISPENSE = {
    "3a1dad64-3a6e-314c-f7d1-f8cabd943b3d",
}


def default_stock_monitor_slots() -> dict:
    """粉体左右各 10 格共 20；液体试剂仓常用 8 + 替换仓常用 7 = 15。"""
    powder_left = [f"{letter}01" for letter in "ABCDEFGHIJ"]
    powder_right = [f"{letter}01" for letter in "KLMNOPQRST"]
    liquid = (
        [{"warehouse": "配液站内试剂仓库", "site": s} for s in ("A01", "B01", "C01", "A02", "B02", "C02", "A03", "B03")]
        + [{"warehouse": "试剂替换仓库左", "site": s} for s in ("A01", "B01", "C01", "D01", "E01")]
        + [{"warehouse": "试剂替换仓库右", "site": s} for s in ("F01", "G01")]
    )
    return {
        "powder": (
            [{"warehouse": "粉末加样头堆栈左", "site": site} for site in powder_left]
            + [{"warehouse": "粉末加样头堆栈右", "site": site} for site in powder_right]
        ),
        "liquid": liquid,
    }


def empty_stock_snapshot() -> dict:
    return {
        "by_location": {},
        "by_material": {},
        "tips": {
            "prep_5000uL": 0,
            "prep_1000uL": 0,
            "prep_50uL": 0,
            "dispense_5000uL": 0,
        },
    }


def parse_tip_top_count_info(parameters: Any) -> dict:
    """解析 parameters.TipTopCountInfo，返回 {"21": n, "22": n, "23": n}。"""
    out = {TIP_CODE_50UL: 0, TIP_CODE_1000UL: 0, TIP_CODE_5000UL: 0}
    raw_params = parameters
    if not raw_params:
        return out
    if isinstance(raw_params, str):
        try:
            raw_params = json.loads(raw_params)
        except (json.JSONDecodeError, TypeError):
            return out
    if not isinstance(raw_params, dict):
        return out
    info = raw_params.get("TipTopCountInfo")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except (json.JSONDecodeError, TypeError):
            return out
    if not isinstance(info, dict):
        return out
    for code in (TIP_CODE_50UL, TIP_CODE_1000UL, TIP_CODE_5000UL):
        if code not in info:
            continue
        try:
            out[code] = int(float(info[code] or 0))
        except (TypeError, ValueError):
            out[code] = 0
    return out


def classify_tip_box(type_name: str | None) -> str:
    """混合 / 5000 / 50 / other。先判 5000 再判 50，避免 5000uL 命中 50uL。"""
    raw = type_name or ""
    if "混合" in raw:
        return "mixed"
    normalized = raw.replace("μ", "u").replace("µ", "u")
    folded = normalized.casefold()
    if "5000ul" in folded:
        return "5000"
    if "50ul" in folded:
        return "50"
    return "other"


def is_tip_rack_type(type_name: str | None) -> bool:
    return "枪头盒" in (type_name or "") or classify_tip_box(type_name) != "other"


def available_quantity(material: dict) -> float:
    """可分配量 = quantity - lockQuantity（lock>0 时扣减）。"""
    try:
        qty = float(material.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    try:
        lock = float(material.get("lockQuantity") or 0)
    except (TypeError, ValueError):
        lock = 0.0
    return max(qty - lock, 0.0)


def _collect_location_keys(material: dict, location: dict) -> set:
    keys: set = set()
    for src in (location, material):
        if not isinstance(src, dict):
            continue
        for field in ("id", "whid", "whId", "warehouseId", "warehouseID"):
            value = src.get(field)
            if value:
                keys.add(str(value))
    return keys


def expand_tip_id_set(warehouse_mapping: dict | None, warehouse_uuids: set, extra: Iterable[str] | None = None) -> set:
    ids = set(warehouse_uuids)
    if extra:
        ids.update(str(x) for x in extra if x)
    for info in (warehouse_mapping or {}).values():
        if not info:
            continue
        wu = info.get("uuid")
        if wu not in warehouse_uuids:
            continue
        ids.add(str(wu))
        for loc in (info.get("site_uuids") or {}).values():
            if loc:
                ids.add(str(loc))
    return ids


def resolve_slot_location_id(bioyond_config: dict | None, warehouse: str | None, site: str | None) -> str:
    mapping = (bioyond_config or {}).get("warehouse_mapping") or {}
    info = mapping.get(warehouse or "") or {}
    return str((info.get("site_uuids") or {}).get(site or "") or "")


def slot_entry_from_snapshot(
    snapshot: dict | None,
    bioyond_config: dict | None,
    kind: str,
    index: int,
) -> dict:
    """kind=powder/liquid，index 从 1 起。空库位返回 {}。"""
    slots_cfg = (bioyond_config or {}).get("stock_monitor_slots") or default_stock_monitor_slots()
    slots = slots_cfg.get(kind) or []
    if index < 1 or index > len(slots):
        return {}
    spec = slots[index - 1] or {}
    loc_id = resolve_slot_location_id(bioyond_config, spec.get("warehouse"), spec.get("site"))
    if not loc_id:
        return {}
    return ((snapshot or {}).get("by_location") or {}).get(loc_id) or {}


def build_stock_snapshot(materials: Sequence[dict] | None, warehouse_mapping: dict | None = None) -> dict:
    """按 location.id 索引试剂余量，并按盒型分流汇总枪头根数。"""
    snapshot = empty_stock_snapshot()
    prep_ids = expand_tip_id_set(warehouse_mapping, TIP_WAREHOUSE_UUID_PREP, _TIP_LOCATION_FALLBACK_PREP)
    disp_ids = expand_tip_id_set(warehouse_mapping, TIP_WAREHOUSE_UUID_DISPENSE, _TIP_LOCATION_FALLBACK_DISPENSE)
    tips = snapshot["tips"]

    for material in materials or []:
        if not isinstance(material, dict):
            continue
        name = str(material.get("name") or "")
        type_name = str(material.get("typeName") or "")
        material_id = str(material.get("id") or "")
        qty = available_quantity(material)
        unit = bioyond_tracker_unit(material)
        tip_counts = parse_tip_top_count_info(material.get("parameters"))
        kind = classify_tip_box(type_name)
        locations = material.get("locations") or []
        if not isinstance(locations, list):
            locations = []

        snapshot["by_material"][material_id] = {
            "name": name,
            "qty": qty,
            "unit": unit,
            "typeName": type_name,
            "tips": tip_counts,
        }

        counted_prep = False
        counted_disp = False
        for loc in locations:
            if not isinstance(loc, dict):
                continue
            loc_id = str(loc.get("id") or "")
            loc_keys = _collect_location_keys(material, loc)
            if loc_id:
                snapshot["by_location"][loc_id] = {
                    "name": name,
                    "qty": qty,
                    "unit": unit,
                    "typeName": type_name,
                    "materialId": material_id,
                    "tips": tip_counts,
                }
            in_prep = bool(loc_keys & prep_ids)
            in_disp = bool(loc_keys & disp_ids)
            if kind == "mixed" and in_prep and not counted_prep:
                tips["prep_5000uL"] += int(tip_counts.get(TIP_CODE_5000UL) or 0)
                tips["prep_1000uL"] += int(tip_counts.get(TIP_CODE_1000UL) or 0)
                counted_prep = True
            elif kind == "50" and in_prep and not counted_prep:
                tips["prep_50uL"] += int(tip_counts.get(TIP_CODE_50UL) or 0)
                counted_prep = True
            elif kind == "5000" and in_disp and not counted_disp:
                tips["dispense_5000uL"] += int(tip_counts.get(TIP_CODE_5000UL) or 0)
                counted_disp = True

    return snapshot


def ensure_carrier_volume_tracker(resource: Any) -> Any:
    """给载架挂 VolumeTracker，点进 Current Data 才能看到 liquids。"""
    tracker = getattr(resource, "tracker", None)
    if tracker is not None:
        return tracker
    from pylabrobot.resources.volume_tracker import VolumeTracker

    tracker = VolumeTracker(thing=f"{getattr(resource, 'name', 'carrier')}_volume_tracker", max_volume=1e9)
    resource.tracker = tracker
    resource.serialize_state = types.MethodType(lambda self: self.tracker.serialize(), resource)
    resource.load_state = types.MethodType(lambda self, state: self.tracker.load_state(state), resource)
    return tracker


def _set_tracker_liquids(tracker: Any, triples: List[Tuple[str, float, str]]) -> None:
    if tracker is None:
        return
    if hasattr(tracker, "liquid_history"):
        tracker.liquid_history.clear()
        tracker._unknown_counter = 0
    tracker.liquids = triples


def tip_liquids_for_box(type_name: str | None, tips: dict | None) -> List[Tuple[str, float, str]]:
    counts = tips or {}
    kind = classify_tip_box(type_name)
    triples: List[Tuple[str, float, str]] = []
    if kind == "mixed":
        n5000 = float(counts.get(TIP_CODE_5000UL) or 0)
        n1000 = float(counts.get(TIP_CODE_1000UL) or 0)
        if n5000:
            triples.append(("5000uL", n5000, "个"))
        if n1000:
            triples.append(("1000uL", n1000, "个"))
        return triples
    if kind == "5000":
        n5000 = float(counts.get(TIP_CODE_5000UL) or 0)
        if n5000:
            triples.append(("5000uL", n5000, "个"))
        return triples
    if kind == "50":
        n50 = float(counts.get(TIP_CODE_50UL) or 0)
        if n50:
            triples.append(("50uL", n50, "个"))
    return triples


def apply_stock_entry_to_resource(resource: Any, entry: dict | None) -> None:
    """把快照写到瓶 tracker 或枪头盒载架 tracker；枪头根数不摊到子孔。"""
    if resource is None or not entry:
        return
    type_name = str(entry.get("typeName") or "")
    tips = entry.get("tips") or {}
    if is_tip_rack_type(type_name) or any(int(tips.get(k) or 0) for k in (TIP_CODE_50UL, TIP_CODE_1000UL, TIP_CODE_5000UL)):
        tracker = ensure_carrier_volume_tracker(resource)
        _set_tracker_liquids(tracker, tip_liquids_for_box(type_name, tips))
        extra = getattr(resource, "unilabos_extra", None)
        if not isinstance(extra, dict):
            extra = {}
            resource.unilabos_extra = extra
        extra["tip_5000uL"] = int(tips.get(TIP_CODE_5000UL) or 0)
        extra["tip_1000uL"] = int(tips.get(TIP_CODE_1000UL) or 0)
        extra["tip_50uL"] = int(tips.get(TIP_CODE_50UL) or 0)
        return

    target = resource
    if hasattr(resource, "capacity") and getattr(resource, "capacity", 0):
        try:
            child = resource[0]
            if child is not None and getattr(child, "tracker", None) is not None:
                target = child
        except Exception:
            pass
    tracker = getattr(target, "tracker", None)
    if tracker is None:
        return
    name = str(entry.get("name") or "")
    qty = float(entry.get("qty") or 0)
    unit = str(entry.get("unit") or "g")
    triples: List[Tuple[str, float, str]] = [(name, qty, unit)] if name and qty > 0 else []
    _set_tracker_liquids(tracker, triples)
