"""宜宾电解液仓库物料展示约定：单瓶升载架、入库数量单位、未知类型占位尺寸。"""

from typing import Any

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
