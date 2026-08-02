"""Edge inventory to the legacy HostNode material-query contract.

The inventory service deliberately stores normalized templates, instances,
relations and contents.  HostNode consumers still expect a flat list of
``ResourceDict``-shaped nodes.  This module is the compatibility seam between
those two models; callers outside the microbackend should not need to know the
inventory table layout.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

from unilabos.app.scheduler.inventory.store import InventoryStore


_RESOURCE_FIELDS = {
    "id",
    "uuid",
    "name",
    "description",
    "schema",
    "model",
    "icon",
    "parent_uuid",
    "type",
    "class",
    "pose",
    "position",
    "config",
    "data",
    "extra",
    "machine_name",
    "barcode",
    "barcode_symbology",
    "liquids",
    "liquid_history",
    "unknown_counter",
}
_TRACKER_STATE_FIELDS = ("liquids", "liquid_history", "unknown_counter")


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resource_spec(template: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract an optional ResourceDict prototype from a template spec.

    ``resource`` is the preferred additive convention.  ``resource_dict`` is
    accepted for early fixtures, while a top-level ResourceDict remains
    compatible with templates created before the convention was introduced.
    Warehouse-only properties are ignored by the ResourceDict projection.
    """

    spec = _json_object((template or {}).get("spec_json", "{}"))
    nested = spec.get("resource")
    if not isinstance(nested, dict):
        nested = spec.get("resource_dict")
    candidate = nested if isinstance(nested, dict) else spec
    resource = {
        key: deepcopy(value)
        for key, value in candidate.items()
        if key in _RESOURCE_FIELDS
    }
    if "schema" not in resource and isinstance(candidate.get("resource_schema"), dict):
        resource["schema"] = deepcopy(candidate["resource_schema"])
    if "class" not in resource and isinstance(candidate.get("klass"), str):
        resource["class"] = candidate["klass"]
    return resource


def _instance_by_uuid(store: InventoryStore, value: str) -> Optional[Dict[str, Any]]:
    """Resolve both the Edge UUID and the retained legacy Cloud UUID."""

    return store.query_one(
        "SELECT * FROM material_instance "
        "WHERE edge_uuid = ? OR legacy_cloud_id = ? "
        "ORDER BY CASE WHEN edge_uuid = ? THEN 0 ELSE 1 END LIMIT 1",
        (value, value, value),
    )


def _node_from_instance(
    store: InventoryStore, instance: Dict[str, Any]
) -> Dict[str, Any]:
    template = store.get_template(str(instance.get("template_id") or ""))
    base = _resource_spec(template)

    edge_uuid = str(instance.get("edge_uuid") or "")
    barcode = str(instance.get("barcode") or base.get("barcode") or "")
    node_id = str(base.get("id") or barcode or edge_uuid)
    template_name = str((template or {}).get("name") or "")

    config = base.get("config") if isinstance(base.get("config"), dict) else {}
    data = base.get("data") if isinstance(base.get("data"), dict) else {}
    extra = base.get("extra") if isinstance(base.get("extra"), dict) else {}
    config = deepcopy(config)
    data = deepcopy(data)
    extra = deepcopy(extra)

    relation = store.get_relation(edge_uuid)
    slot_id = str((relation or {}).get("slot_id") or "")
    if slot_id:
        # Existing device-side mounting code already consumes this key.
        extra.setdefault("update_resource_site", slot_id)

    inventory_meta = extra.setdefault("edge_inventory", {})
    if not isinstance(inventory_meta, dict):
        inventory_meta = {}
        extra["edge_inventory"] = inventory_meta
    inventory_meta.update(
        {
            "template_id": str(instance.get("template_id") or ""),
            "lot_id": str(instance.get("lot_id") or ""),
            "status": str(instance.get("status") or ""),
            "version": int(instance.get("version") or 1),
            "legacy_cloud_id": str(instance.get("legacy_cloud_id") or ""),
            "slot_id": slot_id,
        }
    )

    content = store.get_content(edge_uuid)
    state = _json_object((content or {}).get("state_json", "{}"))
    nested_data = state.pop("data", None)
    if isinstance(nested_data, dict):
        data.update(nested_data)
    for key in _TRACKER_STATE_FIELDS:
        if key in state:
            base[key] = state.pop(key)
    # Content is runtime state.  Unknown state keys stay in ``data`` so older
    # consumers retain them instead of losing information during projection.
    data.update(state)

    node: Dict[str, Any] = {
        **base,
        "id": node_id,
        "uuid": edge_uuid,
        "name": str(base.get("name") or template_name or node_id),
        "description": str(base.get("description") or ""),
        "schema": base.get("schema") if isinstance(base.get("schema"), dict) else {},
        "model": base.get("model") if isinstance(base.get("model"), dict) else {},
        "icon": str(base.get("icon") or ""),
        "parent_uuid": str(instance.get("parent_uuid") or ""),
        # ``container`` is the safest PLR-compatible fallback for an instance
        # whose early inventory template only carried warehouse properties.
        "type": str(
            base.get("type") or (template or {}).get("category") or "container"
        ),
        "class": str(base.get("class") or ""),
        "config": config,
        "data": data,
        "extra": extra,
        "machine_name": str(base.get("machine_name") or ""),
        "barcode": barcode,
        "barcode_symbology": str(base.get("barcode_symbology") or ""),
    }
    return node


def _instance_by_id(
    store: InventoryStore, resource_id: str
) -> Optional[Dict[str, Any]]:
    # Most local IDs are one of these indexed instance identities.
    direct = store.query_one(
        "SELECT * FROM material_instance "
        "WHERE edge_uuid = ? OR legacy_cloud_id = ? OR barcode = ? "
        "ORDER BY CASE WHEN edge_uuid = ? THEN 0 "
        "WHEN legacy_cloud_id = ? THEN 1 ELSE 2 END LIMIT 1",
        (resource_id, resource_id, resource_id, resource_id, resource_id),
    )
    if direct is not None:
        return direct

    # A full ResourceDict prototype may define a legacy logical ``id``.  This
    # is intentionally a compatibility scan; Edge UUID remains the canonical
    # identity for new callers.
    for instance in store.query_all(
        "SELECT * FROM material_instance ORDER BY edge_uuid ASC"
    ):
        if _node_from_instance(store, instance).get("id") == resource_id:
            return instance
    return None


def build_legacy_material_nodes(
    store: InventoryStore,
    *,
    uuids: Optional[Iterable[str]] = None,
    resource_id: Optional[str] = None,
    with_children: bool = True,
) -> List[Dict[str, Any]]:
    """Return a deterministic flat ResourceDict list for legacy callers."""

    roots: List[Dict[str, Any]] = []
    for value in uuids or []:
        instance = _instance_by_uuid(store, str(value))
        if instance is not None:
            roots.append(instance)
    if resource_id:
        instance = _instance_by_id(store, resource_id)
        if instance is not None:
            roots.append(instance)

    nodes: List[Dict[str, Any]] = []
    visited: set[str] = set()

    def append(instance: Dict[str, Any]) -> None:
        edge_uuid = str(instance.get("edge_uuid") or "")
        if not edge_uuid or edge_uuid in visited:
            return
        visited.add(edge_uuid)
        nodes.append(_node_from_instance(store, instance))
        if with_children:
            for child in store.component_children_of(edge_uuid):
                append(child)

    for root in roots:
        append(root)
    return nodes


__all__ = ["build_legacy_material_nodes"]
