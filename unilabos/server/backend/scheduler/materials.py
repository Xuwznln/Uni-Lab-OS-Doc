"""把动作参数解析为 Materials Authority 分配的规范物料 UUID。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from unilabos.registry.material_locks import normalize_material_parameter_names


_MATERIAL_UUID_FIELDS = (
    "material_uuid",
    "resource_uuid",
    "unilabos_uuid",
    "uuid",
)


def extract_material_uuids(value: Any) -> set[str]:
    """Extract authoritative material UUIDs from a resolved action argument.

    Supported inputs are UUID strings, resource reference dictionaries, flat
    resource-tree lists, and resolved PLR objects.  ``id``/``name`` are
    deliberately not accepted as lock identities: only authority-issued UUIDs
    may define mutual exclusion.
    """

    if value is None:
        return set()
    if isinstance(value, str):
        stripped = value.strip()
        return {stripped} if stripped else set()
    if isinstance(value, Mapping):
        for field_name in _MATERIAL_UUID_FIELDS:
            field_value = value.get(field_name)
            if isinstance(field_value, str) and field_value.strip():
                return {field_value.strip()}
        for nested_name in ("data", "identity", "material", "resource"):
            nested = value.get(nested_name)
            if isinstance(nested, Mapping):
                nested_ids = extract_material_uuids(nested)
                if nested_ids:
                    return nested_ids
        return set()
    if isinstance(value, (list, tuple, set, frozenset)):
        result: set[str] = set()
        for item in value:
            result.update(extract_material_uuids(item))
        return result

    for field_name in _MATERIAL_UUID_FIELDS:
        field_value = getattr(value, field_name, None)
        if isinstance(field_value, str) and field_value.strip():
            return {field_value.strip()}
    content = getattr(value, "res_content", None)
    if content is not None:
        content_ids = extract_material_uuids(content)
        if content_ids:
            return content_ids
    extra = getattr(value, "unilabos_extra", None)
    if isinstance(extra, Mapping):
        return extract_material_uuids(extra)
    return set()


def material_uuids_for_parameters(
    parameter_names: Any,
    action_args: Mapping[str, Any],
) -> tuple[str, ...]:
    """解析动作声明引用的全部物料，缺失或非权威身份时拒绝。"""

    names = normalize_material_parameter_names(parameter_names)
    material_uuids: set[str] = set()
    for name in names:
        if name not in action_args:
            raise ValueError(
                f"materials_need_lock 参数 {name!r} 未出现在 action_args 中"
            )
        resolved = extract_material_uuids(action_args[name])
        if not resolved:
            raise ValueError(
                f"materials_need_lock 参数 {name!r} 无法解析权威物料 UUID"
            )
        material_uuids.update(resolved)
    return tuple(sorted(material_uuids))


__all__ = [
    "extract_material_uuids",
    "material_uuids_for_parameters",
    "normalize_material_parameter_names",
]
