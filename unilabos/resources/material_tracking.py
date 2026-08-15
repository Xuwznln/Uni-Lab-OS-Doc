"""PyLabRobot 物料（Material）的 UUID-keyed 根字段快照与差异计算。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from unilabos.resources.resource_tracker import EXTRA_CLASS, extract_plr_sites


TRACKER_STATE_ROOT_FIELDS: Tuple[str, ...] = (
    "liquids",
    "liquid_history",
    "unknown_counter",
)
MATERIAL_ROOT_FIELDS: Tuple[str, ...] = (
    "uuid",
    "name",
    "parent_uuid",
    "type",
    "class",
    "config",
    "data",
    "extra",
    "barcode",
    "barcode_symbology",
    "sites",
    *TRACKER_STATE_ROOT_FIELDS,
)
DEFAULT_MATERIAL_ROOT_FIELDS = MATERIAL_ROOT_FIELDS
_PLR_STRUCTURAL_CONFIG_FIELDS = frozenset(
    {
        "name",
        "children",
        "parent_name",
        "location",
        "rotation",
        "size_x",
        "size_y",
        "size_z",
        "cross_section_type",
        "bottom_type",
        "barcode",
        "barcode_symbology",
        "sites",
    }
)


@dataclass(frozen=True)
class MaterialSnapshot:
    """一个物料 UUID 在某一时刻的规范根字段快照。"""

    material_uuid: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class MaterialFieldChange:
    """同一物料 UUID 两次快照之间发生变化的根字段。"""

    material_uuid: str
    changed_fields: Tuple[str, ...]
    values: Mapping[str, Any]


@dataclass(frozen=True)
class MaterialSnapshotSetDiff:
    """一批物料快照之间按 UUID 计算出的原子新增、修改和删除。"""

    added: Tuple[MaterialSnapshot, ...]
    updated: Tuple[MaterialFieldChange, ...]
    deleted_material_uuids: Tuple[str, ...]


def capture_material_snapshot(
    material: Any,
    fields: Optional[Iterable[str]] = None,
) -> MaterialSnapshot:
    """读取单个物料的指定根字段并生成不可受后续原地修改影响的快照。

    Args:
        material: 带稳定 ``unilabos_uuid`` 的 PyLabRobot 资源或兼容对象。
        fields: 需要比较的根字段；未提供时使用默认字段集合。

    Returns:
        以稳定物料 UUID 标识的根字段快照。

    Raises:
        ValueError: 物料缺少稳定 UUID，或序列化状态不是字典。
    """
    material_uuid = str(getattr(material, "unilabos_uuid", "") or "")
    if not material_uuid:
        raise ValueError("物料缺少 unilabos_uuid，无法生成可追踪快照")

    selected_fields = tuple(fields or DEFAULT_MATERIAL_ROOT_FIELDS)
    config_serializer = getattr(material, "serialize", None)
    serialized_config = config_serializer() if callable(config_serializer) else {}
    if not isinstance(serialized_config, dict):
        raise ValueError("物料 serialize() 必须返回字典")
    state_serializer = getattr(material, "serialize_state", None)
    serialized_state = state_serializer() if callable(state_serializer) else {}
    if not isinstance(serialized_state, dict):
        raise ValueError("物料 serialize_state() 必须返回字典")

    data = deepcopy(serialized_state)
    injected_state = getattr(material, "_unilabos_state", None)
    if isinstance(injected_state, dict):
        data.update(deepcopy(injected_state))

    extra = deepcopy(getattr(material, "unilabos_extra", {}) or {})
    parent = getattr(material, "parent", None)
    parent_uuid = str(getattr(parent, "unilabos_uuid", "") or "") or None
    barcode_value = serialized_config.get("barcode")
    barcode = ""
    barcode_symbology = ""
    if isinstance(barcode_value, dict):
        barcode = str(barcode_value.get("data", "") or "")
        barcode_symbology = str(barcode_value.get("symbology", "") or "")
    elif barcode_value:
        barcode = str(barcode_value)

    root_values: Dict[str, Any] = {
        "uuid": material_uuid,
        "name": getattr(material, "name", ""),
        "parent_uuid": parent_uuid,
        "type": str(getattr(material, "category", "") or ""),
        "class": str(extra.get(EXTRA_CLASS, "") or ""),
        "config": {
            key: deepcopy(value)
            for key, value in serialized_config.items()
            if key not in _PLR_STRUCTURAL_CONFIG_FIELDS
        },
        "data": data,
        "extra": extra,
        "barcode": barcode,
        "barcode_symbology": barcode_symbology,
        "sites": extract_plr_sites(material, serialized_config),
    }
    for state_field in TRACKER_STATE_ROOT_FIELDS:
        root_values[state_field] = data.pop(state_field, None)
    snapshot_fields = {
        field: deepcopy(root_values[field])
        for field in selected_fields
        if field in root_values
    }
    return MaterialSnapshot(material_uuid=material_uuid, fields=snapshot_fields)


def diff_material_snapshots(
    before: MaterialSnapshot,
    after: MaterialSnapshot,
) -> Optional[MaterialFieldChange]:
    """比较同一物料的两个快照并返回发生变化的根字段。

    Args:
        before: 较早的物料根字段快照。
        after: 较新的物料根字段快照。

    Returns:
        有变化时返回字段差异；字段完全相同时返回 ``None``。

    Raises:
        ValueError: 两个快照的物料 UUID 不一致，禁止跨物料比较。
    """
    if before.material_uuid != after.material_uuid:
        raise ValueError("只能比较同一 material_uuid 的物料快照")

    changed_fields = tuple(
        sorted(
            field
            for field in set(before.fields) | set(after.fields)
            if before.fields.get(field) != after.fields.get(field)
        )
    )
    if not changed_fields:
        return None

    return MaterialFieldChange(
        material_uuid=after.material_uuid,
        changed_fields=changed_fields,
        values={field: deepcopy(after.fields.get(field)) for field in changed_fields},
    )


def diff_material_snapshot_sets(
    before: Iterable[MaterialSnapshot],
    after: Iterable[MaterialSnapshot],
) -> MaterialSnapshotSetDiff:
    """按稳定物料 UUID 将两批快照拆成新增、修改和删除原子操作。

    Args:
        before: 上一次成功同步时保存的物料快照集合。
        after: 本次同步时重新生成的物料快照集合。

    Returns:
        UUID 排序稳定的新增快照、字段修改和删除 UUID。

    Raises:
        ValueError: 任一批次中存在重复的物料 UUID，无法确定单写者。
    """

    def index_snapshots(
        snapshots: Iterable[MaterialSnapshot],
        batch_name: str,
    ) -> Dict[str, MaterialSnapshot]:
        """按 UUID 索引单批快照，并拒绝同一批中的重复身份。"""
        indexed: Dict[str, MaterialSnapshot] = {}
        for snapshot in snapshots:
            if snapshot.material_uuid in indexed:
                raise ValueError(f"{batch_name}快照中存在重复 material_uuid: {snapshot.material_uuid}")
            indexed[snapshot.material_uuid] = snapshot
        return indexed

    before_by_uuid = index_snapshots(before, "旧")
    after_by_uuid = index_snapshots(after, "新")
    before_uuids = set(before_by_uuid)
    after_uuids = set(after_by_uuid)

    added = tuple(
        deepcopy(after_by_uuid[material_uuid])
        for material_uuid in sorted(after_uuids - before_uuids)
    )
    deleted_material_uuids = tuple(sorted(before_uuids - after_uuids))
    updated = tuple(
        change
        for material_uuid in sorted(before_uuids & after_uuids)
        if (
            change := diff_material_snapshots(
                before_by_uuid[material_uuid],
                after_by_uuid[material_uuid],
            )
        )
        is not None
    )
    return MaterialSnapshotSetDiff(
        added=added,
        updated=updated,
        deleted_material_uuids=deleted_material_uuids,
    )
