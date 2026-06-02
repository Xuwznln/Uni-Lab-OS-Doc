from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from unilabos.queries.robot_asset import load_robot_asset_manifest, resolve_asset_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: Dict[str, Any], indent: int = 2) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")


def _as_float_list(value: Any) -> List[float]:
    if not isinstance(value, list):
        return []
    result: List[float] = []
    for item in value:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            return []
    return result


def _xyz_dict(xyz: Sequence[float]) -> Dict[str, float]:
    return {"x": float(xyz[0]), "y": float(xyz[1]), "z": float(xyz[2])}


def _pose(xyz: Sequence[float], frame_id: str, unit: str = "m") -> Dict[str, Any]:
    return {
        "frame_id": frame_id,
        "position": _xyz_dict(xyz),
        "orientation_rpy": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
        "unit": unit,
    }


def _pose_with_rotation(
    xyz: Sequence[float],
    frame_id: str,
    unit: str = "m",
    rotation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pose = _pose(xyz, frame_id=frame_id, unit=unit)
    rotation = rotation if isinstance(rotation, dict) else {}
    pose["orientation_rpy"] = {
        "roll": float(rotation.get("roll", rotation.get("x", 0.0)) or 0.0),
        "pitch": float(rotation.get("pitch", rotation.get("y", 0.0)) or 0.0),
        "yaw": float(rotation.get("yaw", rotation.get("z", 0.0)) or 0.0),
    }
    return pose


def _safe_id(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip() or fallback
    safe = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw).strip("_")
    return safe or fallback


def _unique_id(base_id: str, used: Dict[str, int], uuid: Optional[str] = None) -> str:
    base = _safe_id(base_id, "resource")
    if base not in used:
        used[base] = 1
        return base
    if uuid:
        candidate = f"{base}__{_safe_id(uuid, 'uuid')[:8]}"
        if candidate not in used:
            used[candidate] = 1
            return candidate
    used[base] += 1
    candidate = f"{base}__{used[base]}"
    while candidate in used:
        used[base] += 1
        candidate = f"{base}__{used[base]}"
    used[candidate] = 1
    return candidate


def _is_zero_xyz(value: Dict[str, Any]) -> bool:
    return all(float(value.get(axis, 0.0) or 0.0) == 0.0 for axis in ("x", "y", "z"))


def _xyz_from_mapping(value: Any) -> List[float]:
    data = value if isinstance(value, dict) else {}
    return [
        float(data.get("x", 0.0) or 0.0),
        float(data.get("y", 0.0) or 0.0),
        float(data.get("z", 0.0) or 0.0),
    ]


def _first_existing(root: Path, candidates: Sequence[str]) -> Optional[str]:
    for relative in candidates:
        path = root / relative
        if path.exists():
            return str(path)
    return None


def _existing_files(root: Path, candidates: Sequence[str]) -> List[str]:
    files: List[str] = []
    for relative in candidates:
        path = root / relative
        if path.exists():
            files.append(str(path))
    return files


def _center_from_card(card: Dict[str, Any]) -> List[float]:
    geometry = card.get("geometry") or {}
    bbox_min = _as_float_list(geometry.get("bbox_min"))
    bbox_max = _as_float_list(geometry.get("bbox_max"))
    if len(bbox_min) == 3 and len(bbox_max) == 3:
        return [(bbox_min[i] + bbox_max[i]) / 2.0 for i in range(3)]
    details = ((card.get("operation_hints") or {}).get("details") or {})
    nominal = _as_float_list(details.get("nominal_target_pose_xyz"))
    if len(nominal) == 3:
        return nominal
    navigation = details.get("navigation_goal") or {}
    goal = _as_float_list(navigation.get("nominal_goal_xyz"))
    return goal if len(goal) == 3 else [0.0, 0.0, 0.0]


def _bbox_size(card: Dict[str, Any]) -> List[float]:
    geometry = card.get("geometry") or {}
    size = _as_float_list(geometry.get("bbox_size_m_approx"))
    return size if len(size) == 3 else []


def _resource_type_from_card(card: Dict[str, Any]) -> str:
    tags = [str(item) for item in card.get("asset_class_tags") or card.get("affordances") or []]
    if "navigation_goal" in tags:
        return "navigation_goal"
    if "instrument" in tags:
        return "instrument"
    if "articulated" in tags:
        return "articulated_resource"
    if "container" in tags:
        return "container"
    if "grasp_region" in tags:
        return "grasp_region"
    if "button" in tags:
        return "button"
    if tags:
        return tags[0]
    return "labutopia_asset"


def _action_primitives_for_affordance(card: Dict[str, Any], kind: str) -> List[str]:
    hints = card.get("operation_hints") or {}
    primitives = [str(item) for item in hints.get("action_primitives") or [] if isinstance(item, str)]
    if primitives:
        return primitives
    mapping = {
        "button": ["press_button"],
        "operable_region": ["device_operate"],
        "navigation_goal": ["move_to"],
        "grasp_region": ["pick"],
        "articulated": ["open_lid"],
        "container": ["pick", "place", "pour"],
    }
    return mapping.get(kind, [kind])


def _affordance_id(kind: str) -> str:
    mapping = {
        "button": "button",
        "operable_region": "operate",
        "navigation_goal": "goal",
        "grasp_region": "grasp",
        "articulated": "articulation",
        "container": "container",
    }
    return mapping.get(kind, kind.replace(" ", "_"))


def _iter_asset_cards(card_dir: str | Path) -> List[Dict[str, Any]]:
    root = Path(card_dir)
    cards: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        if path.name == "summary.json":
            continue
        cards.append(_load_json(path))
    return cards


def resource_node_from_labutopia_card(
    card: Dict[str, Any],
    reachable_by: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    asset_id = str(card.get("asset_id") or card.get("id"))
    source = card.get("source") or {}
    prim_path = str(source.get("prim_path") or asset_id)
    center = _center_from_card(card)
    size = _bbox_size(card)
    frame_id = "labutopia_world"
    reachable = list(reachable_by or [])
    affordances: List[Dict[str, Any]] = []
    seen_ids = set()
    for kind_value in card.get("affordances") or []:
        kind = str(kind_value)
        affordance_id = _affordance_id(kind)
        if affordance_id in seen_ids:
            continue
        seen_ids.add(affordance_id)
        affordances.append(
            {
                "id": affordance_id,
                "kind": kind,
                "frame_id": f"{asset_id}/{affordance_id}",
                "pose": _pose(center, frame_id=frame_id),
                "action_primitives": _action_primitives_for_affordance(card, kind),
                "reachable_by": reachable,
                "constraints": card.get("operation_hints") or {},
                "metadata": {
                    "source": "labutopia_asset_card",
                    "asset_id": asset_id,
                    "prim_path": prim_path,
                    "candidate_tasks": list(card.get("candidate_tasks") or []),
                    "needs_manual_verification": bool((card.get("metadata") or {}).get("needs_manual_verification", False)),
                },
            }
        )

    safety: Dict[str, Any] = {
        "geometry_source_type": (card.get("geometry") or {}).get("source_type"),
        "source_usd": source.get("resolved_usd_path") or source.get("usd_path"),
        "source_files": list(source.get("source_files") or []),
    }
    if size:
        safety["bbox_size_m"] = size
    return {
        "id": asset_id,
        "name": asset_id,
        "type": "resource",
        "class": f"labutopia.{_resource_type_from_card(card)}",
        "position": _xyz_dict(center),
        "config": {},
        "data": {},
        "extra": {
            "robo_unilabos": {
                "resource_type": _resource_type_from_card(card),
                "frame_id": frame_id,
                "pose": _pose(center, frame_id=frame_id),
                "reachable_by": reachable,
                "affordances": affordances,
                "state_variables": {
                    "asset_class_tags": list(card.get("asset_class_tags") or []),
                    "candidate_tasks": list(card.get("candidate_tasks") or []),
                    "psb_semantics": dict(card.get("psb_semantics") or {}),
                    "prim_path": prim_path,
                },
                "safety": safety,
                "calibration": {
                    "source": "labutopia",
                    "dataset": source.get("dataset"),
                    "prim_path": prim_path,
                },
            }
        },
    }


def sim_robot_node(robot_id: str = "labutopia_franka") -> Dict[str, Any]:
    return {
        "id": robot_id,
        "name": robot_id,
        "type": "device",
        "class": "labutopia.sim_robot",
        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        "config": {},
        "data": {},
        "extra": {
            "robo_unilabos": {
                "resource_type": "sim_robot",
                "frame_id": "labutopia_world",
                "pose": _pose([0.0, 0.0, 0.0], frame_id="labutopia_world"),
                "state_variables": {
                    "mode": "simulation",
                    "boundary": "Reachability is inherited from LabUtopia task contracts, not real robot calibration.",
                },
            }
        },
    }


def robot_node_from_asset(asset: str | Path) -> Dict[str, Any]:
    manifest, asset_root = load_robot_asset_manifest(asset)
    robot_id = str(manifest.get("robot_id") or "robot_asset")
    workspace = manifest.get("workspace") or {}
    center = workspace.get("center", [0.0, 0.0, 0.0])
    base_frame = str(manifest.get("base_frame") or workspace.get("frame_id") or "base_link")
    return {
        "id": robot_id,
        "name": str(manifest.get("display_name") or robot_id),
        "type": "device",
        "class": "robot_asset.urdf",
        "position": _xyz_dict(center),
        "config": {},
        "data": {},
        "extra": {
            "robo_unilabos": {
                "resource_type": "robot_arm",
                "frame_id": base_frame,
                "pose": _pose([0.0, 0.0, 0.0], frame_id=base_frame),
                "affordances": [
                    {
                        "id": "tool0",
                        "kind": "end_effector",
                        "frame_id": f"{robot_id}/tool0",
                        "pose": _pose([0.0, 0.0, 0.0], frame_id=base_frame),
                        "action_primitives": ["move_to", "press_button", "pick", "place"],
                        "metadata": {
                            "tool_link": manifest.get("tool_link"),
                            "tool_offset_xyz": manifest.get("tool_offset_xyz"),
                        },
                    }
                ],
                "state_variables": {
                    "asset_role": manifest.get("asset_role"),
                    "maturity": (manifest.get("validation") or {}).get("maturity"),
                },
                "robot_bindings": {
                    "asset_manifest": str(Path(asset)),
                    "urdf": str(resolve_asset_path(asset_root, manifest.get("urdf"))) if manifest.get("urdf") else None,
                },
                "safety": {
                    "workspace": workspace,
                    "validation_boundaries": list((manifest.get("validation") or {}).get("boundaries") or []),
                },
            }
        },
    }


def real_asset_node_from_card(asset_card_path: str | Path) -> Dict[str, Any]:
    path = Path(asset_card_path)
    card = _load_json(path)
    asset_id = str(card.get("asset_id") or path.stem)
    geometry = card.get("geometry") or {}
    bbox = geometry.get("camera_frame_bbox_m_all_visible_depth") or {}
    bbox_min = _as_float_list(bbox.get("min_xyz"))
    bbox_max = _as_float_list(bbox.get("max_xyz"))
    center = [(bbox_min[i] + bbox_max[i]) / 2.0 for i in range(3)] if len(bbox_min) == 3 and len(bbox_max) == 3 else [0.0, 0.0, 0.0]
    size = [bbox_max[i] - bbox_min[i] for i in range(3)] if len(bbox_min) == 3 and len(bbox_max) == 3 else []
    driver_path = path.parent.parent / "driver_schema" / "driver_schema.json"
    driver = _load_json(driver_path) if driver_path.exists() else {}
    affordances: List[Dict[str, Any]] = []
    for action in driver.get("actions") or []:
        if not isinstance(action, dict):
            continue
        name = str(action.get("name") or "action")
        kind = str(action.get("kind") or "operable_region")
        affordances.append(
            {
                "id": name,
                "kind": kind,
                "frame_id": f"{asset_id}/{name}",
                "pose": _pose(center, frame_id="real_asset_camera_frame"),
                "action_primitives": [name],
                "constraints": {
                    "preconditions": list(action.get("preconditions") or []),
                    "parameters": list(action.get("parameters") or []),
                    "target_mask": action.get("target_mask"),
                },
                "metadata": {
                    "source": "real_rgbd_asset_card",
                    "evidence_level": driver.get("evidence_level"),
                    "safety_notes": list(action.get("safety_notes") or []),
                },
            }
        )
    return {
        "id": asset_id,
        "name": str(card.get("asset_name") or asset_id),
        "type": "device",
        "class": str(driver.get("asset_type") or "real_rgbd_asset"),
        "position": _xyz_dict(center),
        "config": {},
        "data": {},
        "extra": {
            "robo_unilabos": {
                "resource_type": str(driver.get("asset_type") or "real_rgbd_asset"),
                "frame_id": "real_asset_camera_frame",
                "pose": _pose(center, frame_id="real_asset_camera_frame"),
                "affordances": affordances,
                "state_variables": {
                    "observable_states": list(driver.get("observable_states") or []),
                    "semantic_layer": dict(card.get("semantic_layer") or {}),
                    "visual_layer": dict(card.get("visual_layer") or {}),
                    "quality_notes": list(card.get("quality_notes") or []),
                },
                "safety": {
                    "bbox_size_m": size,
                    "evidence_level": driver.get("evidence_level"),
                    "not_supported_yet": list(driver.get("not_supported_yet") or []),
                },
                "calibration": {
                    "coordinate_references": dict(driver.get("coordinate_references") or {}),
                    "geometry_source": geometry.get("source_ply"),
                },
            }
        },
    }


def _startup_config_nodes(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    nodes = data.get("nodes") if isinstance(data, dict) else []
    return [node for node in nodes or [] if isinstance(node, dict)]


def _startup_resource_type(node: Dict[str, Any]) -> str:
    raw_type = str(node.get("type") or "").lower()
    raw_class = str(node.get("class") or node.get("klass") or "").lower()
    node_id = str(node.get("id") or node.get("name") or "").lower()
    text = f"{node_id} {raw_type} {raw_class}"
    if "robotic_arm" in text or "arm_slider" in text or "moveit" in text:
        return "robot_arm"
    if "liquid_handler" in text:
        return "liquid_handler"
    if raw_type == "deck" or "deck" in text:
        return "deck_workspace"
    if "tip_spot" in text:
        return "tip_spot"
    if "tip_rack" in text or "filtertiprack" in text:
        return "tip_rack"
    if raw_type in {"plate", "well_plate"} or "wellplate" in text:
        return "labware_plate"
    if raw_type == "well" or node_id.endswith("_well"):
        return "well"
    if "hotel" in text:
        return "storage_hotel"
    if "host_node" in text:
        return "host_node"
    if raw_type:
        return raw_type
    return "unilabos_resource"


def _startup_pose(node: Dict[str, Any]) -> Tuple[List[float], Dict[str, Any], str]:
    pose = node.get("pose") if isinstance(node.get("pose"), dict) else {}
    position_3d = pose.get("position_3d") if isinstance(pose.get("position_3d"), dict) else {}
    if position_3d and not _is_zero_xyz(position_3d):
        xyz = _xyz_from_mapping(position_3d)
        source = "pose.position_3d"
    elif isinstance(pose.get("position"), dict):
        xyz = _xyz_from_mapping(pose.get("position"))
        source = "pose.position"
    else:
        xyz = _xyz_from_mapping(node.get("position"))
        source = "position"
    rotation = pose.get("rotation") if isinstance(pose.get("rotation"), dict) else {}
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    if not rotation and isinstance(config.get("rotation"), dict):
        rotation = config.get("rotation")
        source = f"{source}+config.rotation"
    return xyz, rotation, source


def _startup_size(node: Dict[str, Any]) -> List[float]:
    pose = node.get("pose") if isinstance(node.get("pose"), dict) else {}
    size = pose.get("size") if isinstance(pose.get("size"), dict) else {}
    if size and any(float(size.get(key, 0.0) or 0.0) for key in ("width", "height", "depth")):
        return [
            float(size.get("width", 0.0) or 0.0),
            float(size.get("height", 0.0) or 0.0),
            float(size.get("depth", 0.0) or 0.0),
        ]
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    if any(key in config for key in ("size_x", "size_y", "size_z")):
        return [
            float(config.get("size_x", 0.0) or 0.0),
            float(config.get("size_y", 0.0) or 0.0),
            float(config.get("size_z", 0.0) or 0.0),
        ]
    return []


def _startup_device_endpoints(node: Dict[str, Any]) -> Dict[str, Any]:
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    endpoints: Dict[str, Any] = {}
    for key in ("backend", "device_config", "simulator"):
        if key in config:
            endpoints[key] = config.get(key)
    return endpoints


def _startup_robot_bindings(node: Dict[str, Any], resource_type: str) -> Dict[str, Any]:
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    model = node.get("model") if isinstance(node.get("model"), dict) else {}
    bindings: Dict[str, Any] = {}
    if resource_type == "robot_arm":
        bindings["moveit_type"] = config.get("moveit_type")
        bindings["joint_poses"] = config.get("joint_poses", {})
    if model:
        bindings["model"] = model
    return {key: value for key, value in bindings.items() if value not in (None, {}, [])}


def _startup_affordances(
    node: Dict[str, Any],
    node_id: str,
    resource_type: str,
    pose: Dict[str, Any],
    reachable_by: Sequence[str],
) -> List[Dict[str, Any]]:
    specs = {
        "robot_arm": [("tool0", "end_effector", ["move_to", "pick", "place", "press_button"])],
        "liquid_handler": [("liquid_handling", "liquid_handling_workspace", ["pick_tip", "drop_tip", "aspirate", "dispense", "mix"])],
        "deck_workspace": [("deck", "deck_workspace", ["place_labware", "move_to_slot"])],
        "tip_rack": [("tips", "tip_source", ["pick_tip"])],
        "tip_spot": [("tip", "tip_spot", ["pick_tip"])],
        "labware_plate": [("wells", "labware_wells", ["aspirate", "dispense", "read_liquid_state"])],
        "well": [("well", "well", ["aspirate", "dispense", "read_liquid_state"])],
        "storage_hotel": [("storage", "plate_storage", ["load_plate", "unload_plate"])],
    }
    affordances: List[Dict[str, Any]] = []
    for affordance_id, kind, primitives in specs.get(resource_type, []):
        affordance_reachable_by = [] if resource_type == "robot_arm" else list(reachable_by)
        affordances.append(
            {
                "id": affordance_id,
                "kind": kind,
                "frame_id": f"{node_id}/{affordance_id}",
                "pose": pose,
                "action_primitives": primitives,
                "reachable_by": affordance_reachable_by,
                "constraints": {
                    "source": "unilabos_startup_config",
                    "manual_verification_required": True,
                },
                "metadata": {
                    "source": "unilabos_startup_config",
                    "uuid": node.get("uuid"),
                    "parent_uuid": node.get("parent_uuid"),
                    "needs_manual_verification": True,
                },
            }
        )
    return affordances


def _startup_access_zones(
    node_id: str,
    resource_type: str,
    pose: Dict[str, Any],
    reachable_by: Sequence[str],
    size: Sequence[float],
) -> List[Dict[str, Any]]:
    zones: List[Dict[str, Any]] = []
    if len(size) == 3 and any(float(item) for item in size):
        zones.append(
            {
                "id": "footprint",
                "frame_id": pose["frame_id"],
                "pose": pose,
                "reachable_by": list(reachable_by),
                "keepout": resource_type in {"robot_arm", "storage_hotel"},
                "metadata": {
                    "resource": node_id,
                    "bbox_size": list(size),
                    "unit": pose.get("unit", "mm"),
                    "source": "unilabos_startup_config",
                },
            }
        )
    if resource_type == "deck_workspace" and not zones:
        zones.append(
            {
                "id": "deck_workspace",
                "frame_id": pose["frame_id"],
                "pose": pose,
                "reachable_by": list(reachable_by),
                "keepout": False,
                "metadata": {
                    "resource": node_id,
                    "source": "unilabos_startup_config",
                    "manual_verification_required": True,
                },
            }
        )
    return zones


def _startup_reachable_by(
    node: Dict[str, Any],
    resource_type: str,
    uuid_to_node: Dict[str, Dict[str, Any]],
    uuid_to_id: Dict[str, str],
) -> List[str]:
    if resource_type == "liquid_handler":
        return [uuid_to_id.get(str(node.get("uuid")), str(node.get("id") or "liquid_handler"))]
    if resource_type == "storage_hotel":
        arm_ids = [
            mapped_id
            for uuid, mapped_id in uuid_to_id.items()
            if _startup_resource_type(uuid_to_node.get(uuid, {})) == "robot_arm"
        ]
        return arm_ids
    parent_uuid = str(node.get("parent_uuid") or "")
    seen = set()
    while parent_uuid and parent_uuid not in seen:
        seen.add(parent_uuid)
        parent = uuid_to_node.get(parent_uuid)
        if not parent:
            break
        parent_type = _startup_resource_type(parent)
        if parent_type == "liquid_handler":
            return [uuid_to_id[parent_uuid]]
        parent_uuid = str(parent.get("parent_uuid") or "")
    return []


def resource_nodes_from_startup_config(config_path: str | Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    payload = _load_json(config_path)
    raw_nodes = _startup_config_nodes(payload)
    used_ids: Dict[str, int] = {}
    uuid_to_id: Dict[str, str] = {}
    for index, node in enumerate(raw_nodes):
        uuid = str(node.get("uuid") or "")
        base_id = node.get("id") or node.get("name") or uuid or f"startup_node_{index + 1}"
        node_id = _unique_id(str(base_id), used_ids, uuid=uuid)
        if uuid:
            uuid_to_id[uuid] = node_id

    uuid_to_node = {str(node.get("uuid")): node for node in raw_nodes if node.get("uuid")}
    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_nodes):
        uuid = str(raw.get("uuid") or "")
        node_id = uuid_to_id.get(uuid) or _unique_id(str(raw.get("id") or raw.get("name") or f"startup_node_{index + 1}"), used_ids)
        parent_uuid = str(raw.get("parent_uuid") or "")
        parent_id = uuid_to_id.get(parent_uuid)
        frame_id = f"{parent_id}_frame" if parent_id else "unilabos_lab_world"
        resource_type = _startup_resource_type(raw)
        xyz, rotation, pose_source = _startup_pose(raw)
        pose = _pose_with_rotation(xyz, frame_id=frame_id, unit="mm", rotation=rotation)
        size = _startup_size(raw)
        reachable_by = _startup_reachable_by(raw, resource_type, uuid_to_node=uuid_to_node, uuid_to_id=uuid_to_id)
        raw_config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        raw_data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        raw_model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
        state_variables = {
            "source": "unilabos_startup_config",
            "uuid": uuid or None,
            "parent_uuid": parent_uuid or None,
            "parent_id": parent_id,
            "source_type": raw.get("type"),
            "source_class": raw.get("class") or raw.get("klass"),
            "pose_source": pose_source,
            "config_keys": sorted(raw_config.keys()),
            "schema": raw.get("schema") if isinstance(raw.get("schema"), dict) else {},
        }
        nodes.append(
            {
                "id": node_id,
                "name": str(raw.get("name") or raw.get("id") or node_id),
                "type": str(raw.get("type") or "resource"),
                "class": str(raw.get("class") or raw.get("klass") or resource_type),
                "uuid": uuid or None,
                "parent_uuid": parent_uuid or None,
                "position": _xyz_dict(xyz),
                "pose": raw.get("pose", {}),
                "config": raw_config,
                "data": raw_data,
                "model": raw_model,
                "description": raw.get("description"),
                "extra": {
                    "robo_unilabos": {
                        "resource_type": resource_type,
                        "frame_id": frame_id,
                        "pose": pose,
                        "reachable_by": reachable_by,
                        "affordances": _startup_affordances(raw, node_id, resource_type, pose, reachable_by),
                        "access_zones": _startup_access_zones(node_id, resource_type, pose, reachable_by, size),
                        "state_variables": state_variables,
                        "device_endpoints": _startup_device_endpoints(raw),
                        "robot_bindings": _startup_robot_bindings(raw, resource_type),
                        "safety": {
                            "bbox_size": size,
                            "model": raw_model,
                            "manual_verification_required": True,
                            "boundary": "Imported from Uni-Lab-OS startup resource tree; no real robot calibration or execution guarantee is inferred.",
                        },
                        "calibration": {
                            "source": "unilabos_startup_config",
                            "source_file": str(config_path),
                            "evidence": "resource_tree_snapshot",
                            "pose_source": pose_source,
                            "parent_frame": frame_id,
                        },
                    }
                },
            }
        )
        if parent_id:
            links.append(
                {
                    "source": parent_id,
                    "target": node_id,
                    "kind": "contains",
                    "source_uuid": parent_uuid,
                    "target_uuid": uuid or None,
                    "source_file": str(config_path),
                }
            )
    return nodes, links


def horizon_nodes_from_asset_root(horizon_root: str | Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    root = Path(horizon_root)
    frame_id = "horizon_world"
    scene_usd = _first_existing(root, ["isaac/horizon_v2_with_arm7_scene.usd"])
    platform_usd = _first_existing(root, ["isaac/HOR_Horizon_V2_1_2508_basepython.usd"])
    arm_usd = _first_existing(root, ["isaac/horizon_arm7.usd", "isaac/configuration/horizon_arm7_robot.usd"])
    robot_usd = _first_existing(root, ["isaac/configuration/horizon_arm7_robot.usd"])
    arm_urdf = _first_existing(root, ["source/7/urdf/7.urdf", "source/arm7_isaac/urdf/horizon_arm7.urdf"])
    preview_images = _existing_files(root, ["isaac/horizon_v2_with_arm7_scene.png", "isaac/horizon_v2_with_arm7_scene_framed.png"])
    real_arm_link_files = _existing_files(
        root,
        [
            "real_arm_link/README.md",
            "real_arm_link/isaac_shadow_sync.py",
            "real_arm_link/joint_mapping.py",
            "real_arm_link/rcs_client.py",
        ],
    )
    platform_pose = _pose([0.0, 0.0, 0.0], frame_id=frame_id)
    arm_pose = _pose([0.0, 0.0, 0.0], frame_id=frame_id)
    platform_node = {
        "id": "horizon_v2",
        "name": "Horizon V2",
        "type": "device",
        "class": "horizon.automation_platform",
        "position": _xyz_dict([0.0, 0.0, 0.0]),
        "config": {},
        "data": {},
        "extra": {
            "robo_unilabos": {
                "resource_type": "automation_platform",
                "frame_id": frame_id,
                "pose": platform_pose,
                "affordances": [
                    {
                        "id": "workcell",
                        "kind": "automation_workcell",
                        "frame_id": "horizon_v2/workcell",
                        "pose": platform_pose,
                        "action_primitives": ["load_scene_asset", "inspect_scene", "plan_layout"],
                        "constraints": {"manual_verification_required": True},
                        "metadata": {"source": "horizon_v2_import", "needs_manual_verification": True},
                    }
                ],
                "access_zones": [
                    {
                        "id": "workspace",
                        "frame_id": frame_id,
                        "pose": platform_pose,
                        "reachable_by": ["horizon_arm7"],
                        "keepout": False,
                        "metadata": {
                            "bbox_size": [2.0, 2.0, 1.5],
                            "unit": "m",
                            "source": "horizon_v2_import",
                            "manual_verification_required": True,
                        },
                    }
                ],
                "state_variables": {
                    "asset_role": "digital_twin_platform",
                    "maturity": "asset_import_only",
                    "scene_usd": scene_usd,
                    "preview_images": preview_images,
                },
                "safety": {
                    "source_files": _existing_files(root, ["isaac/horizon_v2_with_arm7_scene.usd", "isaac/HOR_Horizon_V2_1_2508_basepython.usd"]),
                    "manual_verification_required": True,
                    "boundary": "Horizon CAD/USD import is a digital twin asset layer, not a calibrated real execution contract.",
                },
                "calibration": {
                    "source": "horizon_v2_import",
                    "asset_root": str(root),
                    "evidence": "usd_and_preview_assets",
                },
            }
        },
    }
    arm_node = {
        "id": "horizon_arm7",
        "name": "Horizon Arm7",
        "type": "device",
        "class": "robot_asset.arm7",
        "position": _xyz_dict([0.0, 0.0, 0.0]),
        "config": {},
        "data": {},
        "extra": {
            "robo_unilabos": {
                "resource_type": "robot_arm",
                "frame_id": frame_id,
                "pose": arm_pose,
                "affordances": [
                    {
                        "id": "tool0",
                        "kind": "end_effector",
                        "frame_id": "horizon_arm7/tool0",
                        "pose": arm_pose,
                        "action_primitives": ["move_to", "pick", "place", "press_button"],
                        "constraints": {"manual_verification_required": True},
                        "metadata": {
                            "source": "horizon_v2_import",
                            "tool_link": "tool0",
                            "needs_manual_verification": True,
                        },
                    }
                ],
                "access_zones": [
                    {
                        "id": "nominal_workspace",
                        "frame_id": frame_id,
                        "pose": arm_pose,
                        "reachable_by": ["horizon_arm7"],
                        "keepout": False,
                        "metadata": {
                            "bbox_size": [1.2, 1.2, 1.0],
                            "unit": "m",
                            "source": "horizon_v2_import",
                            "manual_verification_required": True,
                        },
                    }
                ],
                "state_variables": {
                    "asset_role": "digital_twin_robot",
                    "maturity": "asset_import_only",
                    "real_arm_link_files": real_arm_link_files,
                },
                "robot_bindings": {
                    "asset_root": str(root),
                    "scene_usd": scene_usd,
                    "platform_usd": platform_usd,
                    "arm_usd": arm_usd,
                    "robot_usd": robot_usd,
                    "urdf": arm_urdf,
                },
                "safety": {
                    "workspace": {"frame_id": frame_id, "center": [0.0, 0.0, 0.0], "size": [1.2, 1.2, 1.0], "unit": "m"},
                    "manual_verification_required": True,
                    "boundary": "Arm7 USD/URDF metadata is queryable; live control, calibration, and reachability still require hardware integration.",
                },
                "calibration": {
                    "source": "horizon_v2_import",
                    "asset_root": str(root),
                    "evidence": "usd_urdf_asset_snapshot",
                },
            }
        },
    }
    return [platform_node, arm_node], [
        {
            "source": "horizon_v2",
            "target": "horizon_arm7",
            "kind": "hosts_robot",
            "source_file": str(root),
        }
    ]


def resource_map_from_asset_cards(
    card_dir: str | Path,
    reachable_by: Optional[Sequence[str]] = None,
    sim_robot_id: Optional[str] = None,
    robot_assets: Optional[Sequence[str | Path]] = None,
    real_asset_cards: Optional[Sequence[str | Path]] = None,
    startup_configs: Optional[Sequence[str | Path]] = None,
    horizon_roots: Optional[Sequence[str | Path]] = None,
) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []
    effective_reachable = list(reachable_by or [])
    if sim_robot_id:
        nodes.append(sim_robot_node(sim_robot_id))
        if not effective_reachable:
            effective_reachable = [sim_robot_id]
    for card in _iter_asset_cards(card_dir):
        nodes.append(resource_node_from_labutopia_card(card, reachable_by=effective_reachable))
    for asset in robot_assets or []:
        nodes.append(robot_node_from_asset(asset))
    for asset_card in real_asset_cards or []:
        nodes.append(real_asset_node_from_card(asset_card))
    for startup_config in startup_configs or []:
        startup_nodes, startup_links = resource_nodes_from_startup_config(startup_config)
        nodes.extend(startup_nodes)
        links.extend(startup_links)
    for horizon_root in horizon_roots or []:
        horizon_nodes, horizon_links = horizon_nodes_from_asset_root(horizon_root)
        nodes.extend(horizon_nodes)
        links.extend(horizon_links)
    return {
        "graph_id": "robo_unilabos_canonical_asset_pack",
        "schema": "minimal_robo_unilabos_resource_graph.v0",
        "generated_at": _utc_now(),
        "nodes": nodes,
        "links": links,
    }


def action_catalog_from_task_report(report_path: str | Path) -> Dict[str, Any]:
    report = _load_json(report_path)
    actions: List[Dict[str, Any]] = []
    for task in report.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        schema_result = task.get("schema") or {}
        if not schema_result.get("ok"):
            continue
        schema = schema_result.get("value")
        if not isinstance(schema, dict):
            continue
        task_name = str(task.get("name") or (schema.get("metadata") or {}).get("labutopia_task_name") or schema.get("action"))
        actions.append(
            {
                "id": task_name,
                "task_name": task_name,
                "action": schema.get("action"),
                "status": task.get("status"),
                "source_file": task.get("source_file"),
                "usd_path": task.get("usd_path"),
                "schema": schema,
                "postcondition_types": list(task.get("postcondition_types") or []),
            }
        )
    return {
        "schema_version": "robo_unilabos.action_catalog.v1",
        "generated_at": _utc_now(),
        "source_report": str(report_path),
        "summary": dict(report.get("summary") or {}),
        "actions": actions,
    }


def build_asset_pack(
    asset_card_dir: str | Path,
    task_report_path: str | Path,
    output_dir: str | Path,
    robot_assets: Optional[Sequence[str | Path]] = None,
    real_asset_cards: Optional[Sequence[str | Path]] = None,
    startup_configs: Optional[Sequence[str | Path]] = None,
    horizon_roots: Optional[Sequence[str | Path]] = None,
    reachable_by: Optional[Sequence[str]] = None,
    sim_robot_id: Optional[str] = None,
    indent: int = 2,
) -> Dict[str, Any]:
    output = Path(output_dir)
    resource_map = resource_map_from_asset_cards(
        asset_card_dir,
        reachable_by=reachable_by,
        sim_robot_id=sim_robot_id,
        robot_assets=robot_assets,
        real_asset_cards=real_asset_cards,
        startup_configs=startup_configs,
        horizon_roots=horizon_roots,
    )
    action_catalog = action_catalog_from_task_report(task_report_path)
    resource_map_path = output / "resource_map.json"
    action_catalog_path = output / "action_catalog.json"
    manifest_path = output / "manifest.json"
    _write_json(resource_map_path, resource_map, indent=indent)
    _write_json(action_catalog_path, action_catalog, indent=indent)
    manifest = {
        "schema_version": "robo_unilabos.asset_pack.v1",
        "generated_at": _utc_now(),
        "asset_card_dir": str(asset_card_dir),
        "task_report_path": str(task_report_path),
        "resource_map": str(resource_map_path),
        "action_catalog": str(action_catalog_path),
        "node_count": len(resource_map["nodes"]),
        "action_count": len(action_catalog["actions"]),
        "robot_assets": [str(item) for item in robot_assets or []],
        "real_asset_cards": [str(item) for item in real_asset_cards or []],
        "startup_configs": [str(item) for item in startup_configs or []],
        "horizon_roots": [str(item) for item in horizon_roots or []],
        "sim_robot_id": sim_robot_id,
        "default_reachable_by": list(reachable_by or []),
    }
    _write_json(manifest_path, manifest, indent=indent)
    return {
        "ok": True,
        "manifest": manifest,
        "resource_map": str(resource_map_path),
        "action_catalog": str(action_catalog_path),
        "node_count": manifest["node_count"],
        "action_count": manifest["action_count"],
    }
