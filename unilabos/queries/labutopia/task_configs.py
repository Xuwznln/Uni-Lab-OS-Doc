from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from unilabos.queries.models import ActionSchema, QueryAffordance, State


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load LabUtopia task configs") from exc
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"LabUtopia task config must be a mapping: {path}")
    return data


def _is_labutopia_prim_path(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("/World")


def _is_targetish_key(key: str) -> bool:
    normalized = key.lower()
    skipped_tokens = ("camera", "material", "looks", "robot", "joint", "light")
    return not any(token in normalized for token in skipped_tokens)


def _paths_from_config(config: Dict[str, Any]) -> Dict[str, Any]:
    paths: Dict[str, Any] = {}
    for key, value in config.items():
        if key.endswith("_path") or key.endswith("_button_path") or key.endswith("_obj_path"):
            paths[key] = value
        elif _is_targetish_key(key) and _is_labutopia_prim_path(value):
            paths[key] = value
    task = config.get("task") or {}
    if isinstance(task, dict):
        for key, value in task.items():
            if key.endswith("_path") or key.endswith("_button_path") or key.endswith("_obj_path"):
                paths[f"task.{key}"] = value
            elif _is_targetish_key(key) and _is_labutopia_prim_path(value):
                paths[f"task.{key}"] = value
            elif _is_targetish_key(key) and isinstance(value, list):
                path_items = []
                for item in value:
                    if isinstance(item, dict) and _is_labutopia_prim_path(item.get("path")):
                        path_items.append(item)
                    elif _is_labutopia_prim_path(item):
                        path_items.append({"path": item})
                if path_items:
                    paths[f"task.{key}"] = path_items
    return paths


def _action_for_task_type(task_type: str) -> str:
    normalized = task_type.lower()
    mapping = {
        "press": "press_button",
        "pick": "pick",
        "mobile_pick": "pick",
        "place": "place",
        "pickplace": "pick_place",
        "placepress": "press_button",
        "pickpour": "pour",
        "pour": "pour",
        "open": "open_lid",
        "close": "open_lid",
        "openclose": "open_lid",
        "stir": "stir",
        "liquidmixing": "stir",
        "shake": "shake",
        "cleanbeaker": "clean",
        "device_operate": "device_operate",
        "opentransportpour": "pour",
        "navigation": "move_to",
    }
    return mapping.get(normalized, task_type or "unknown")


def _affordance_kind_for_action(action: str, role: str) -> str:
    if action == "press_button":
        return "button"
    if action == "pick":
        return "grasp_region"
    if action in ("place", "pick_place", "pour", "clean"):
        return "container"
    if action == "open_lid":
        return "articulated"
    if action in ("stir", "shake"):
        return "container"
    if action == "device_operate":
        return "instrument" if "device" in role else "operable_region"
    if action == "move_to":
        return "navigation_goal"
    return role or action


def _resolve_config_relative_path(config: Dict[str, Any], relative_path: str) -> Path:
    path = Path(relative_path).expanduser()
    if path.is_absolute():
        return path
    source_file = config.get("_source_file")
    candidates: List[Path] = []
    if source_file:
        source = Path(str(source_file)).expanduser()
        candidates.extend([source.parent.parent / path, source.parent / path])
    candidates.append(path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _navigation_entries_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if _action_for_task_type(str(config.get("task_type") or "")) != "move_to":
        return []
    task = config.get("task") or {}
    if not isinstance(task, dict):
        return []
    navigation_config_path = task.get("navigation_config_path")
    if not isinstance(navigation_config_path, str):
        return []

    resolved = _resolve_config_relative_path(config, navigation_config_path)
    if not resolved.exists():
        return [
            {
                "path": "navigation://default",
                "role": "task.navigation_config_path",
                "constraints": {
                    "navigation_config_path": navigation_config_path,
                    "resolved_navigation_config_path": str(resolved),
                    "status": "navigation_config_missing",
                },
            }
        ]

    data = _load_yaml(resolved)
    assets = data.get("assets")
    if not isinstance(assets, list):
        assets = []
    entries: List[Dict[str, Any]] = []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or f"asset_{index}")
        constraints = dict(asset)
        constraints["navigation_config_path"] = navigation_config_path
        constraints["resolved_navigation_config_path"] = str(resolved)
        entries.append(
            {
                "path": f"navigation://{name}",
                "role": f"task.navigation_config_path[{index}]",
                "constraints": constraints,
            }
        )
    return entries


def _inferred_entries_from_config(config: Dict[str, Any], existing_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if existing_entries:
        return []
    task_type = str(config.get("task_type") or "")
    action = _action_for_task_type(task_type)
    defaults = {
        "stir": [
            ("/World/target_beaker", "inferred.target_beaker"),
            ("/World/glass_rod", "inferred.stir_tool"),
        ],
        "shake": [
            ("/World/target_beaker", "inferred.target_beaker"),
        ],
    }
    entries: List[Dict[str, Any]] = []
    for path, role in defaults.get(action, []):
        entries.append(
            {
                "path": path,
                "role": role,
                "constraints": {
                    "inferred_from_task_type": task_type or action,
                    "source": "labutopia_task_convention",
                },
            }
        )
    return entries


def _deduplicate_entries(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for entry in entries:
        key = (entry.get("path"), entry.get("role"))
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _target_entries_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    paths = _paths_from_config(config)
    for key, value in paths.items():
        if isinstance(value, str) and _is_labutopia_prim_path(value):
            entries.append({"path": value, "role": key, "constraints": {}})
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, dict) or not _is_labutopia_prim_path(item.get("path")):
                    continue
                entries.append(
                    {
                        "path": item["path"],
                        "role": f"{key}[{index}]",
                        "constraints": {
                            name: constraint
                            for name, constraint in item.items()
                            if name != "path"
                        },
                    }
                )
    entries.extend(_navigation_entries_from_config(config))
    entries.extend(_inferred_entries_from_config(config, entries))
    return _deduplicate_entries(entries)


class LabUtopiaTaskConfigSource:
    name = "labutopia_task_configs"

    def __init__(self, configs: Iterable[Dict[str, Any]], source_path: Optional[str] = None):
        self.source_path = source_path
        self._configs_by_name: Dict[str, Dict[str, Any]] = {}
        self._schemas_by_action: Dict[str, ActionSchema] = {}
        self._schemas_by_task: Dict[str, ActionSchema] = {}
        self._duplicate_task_names: List[str] = []
        for config in configs:
            name = str(config.get("name") or config.get("_file_stem") or "")
            if name:
                if name in self._configs_by_name:
                    self._duplicate_task_names.append(name)
                    warnings.warn(
                        f"LabUtopia task name '{name}' appears in more than one config; "
                        f"keeping the first occurrence and ignoring later ones.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
                self._configs_by_name[name] = config
            schema = self._schema_from_config(config)
            if schema is not None:
                if schema.action not in self._schemas_by_action:
                    self._schemas_by_action[schema.action] = schema
                if name:
                    self._schemas_by_task[name] = schema

    @property
    def duplicate_task_names(self) -> List[str]:
        return list(self._duplicate_task_names)

    @property
    def configs(self) -> List[Dict[str, Any]]:
        return list(self._configs_by_name.values())

    @classmethod
    def from_directory(cls, directory: str | Path) -> "LabUtopiaTaskConfigSource":
        path = Path(directory)
        configs: List[Dict[str, Any]] = []
        for config_path in sorted(path.glob("*.yaml")):
            config = _load_yaml(config_path)
            config["_file_stem"] = config_path.stem
            config["_source_file"] = str(config_path)
            configs.append(config)
        return cls(configs, source_path=str(path))

    def _schema_from_config(self, config: Dict[str, Any]) -> Optional[ActionSchema]:
        task_type = str(config.get("task_type") or "")
        if not task_type:
            return None
        action = _action_for_task_type(task_type)
        paths = _paths_from_config(config)
        postconditions: List[Dict[str, Any]] = []
        if action == "press_button":
            target = paths.get("target_button_path") or paths.get("sub_obj_path")
            postconditions.append({"type": "pose_axis_gt", "target": paths.get("sub_obj_path", target), "axis": "x", "threshold": 0.405})
        elif action == "pour":
            postconditions.append({"type": "custom", "name": "liquid_transferred"})
        elif action == "open_lid":
            postconditions.append({"type": "device_state", "state": "open"})
        return ActionSchema(
            action=action,
            args={
                "task_type": task_type,
                "controller_type": config.get("controller_type"),
                "targets": paths,
            },
            preconditions=[
                {"type": "scene_loaded", "usd_path": config.get("usd_path")},
            ],
            postconditions=postconditions,
            policy_preference=["classical", "ACT", "DiffusionPolicy", "VLA"],
            timeout_s=float(((config.get("task") or {}).get("max_steps") or 1000) / 30.0),
            metadata={
                "source": self.name,
                "labutopia_task_name": config.get("name"),
                "source_file": config.get("_source_file"),
                "mode": config.get("mode"),
            },
        )

    def query_pose(self, target: str, frame: Optional[str] = None):
        return None

    def query_state(self, target: str) -> Optional[State]:
        config = self._configs_by_name.get(target)
        if config is None:
            return None
        return State(
            name=target,
            values={
                "task_type": config.get("task_type"),
                "controller_type": config.get("controller_type"),
                "mode": config.get("mode"),
                "usd_path": config.get("usd_path"),
                "paths": _paths_from_config(config),
                "robot": dict(config.get("robot") or {}),
                "cameras": list(config.get("cameras") or []),
            },
            source=self.name,
        )

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        result: List[QueryAffordance] = []
        for config in self._configs_by_name.values():
            task_type = str(config.get("task_type") or "")
            action = _action_for_task_type(task_type)
            if action == "unknown":
                continue
            for entry in _target_entries_from_config(config):
                path = entry["path"]
                if target not in {str(config.get("name")), path}:
                    continue
                item_kind = _affordance_kind_for_action(action, str(entry["role"]))
                if kind is not None and item_kind != kind:
                    continue
                result.append(
                    QueryAffordance(
                        id=str(entry["role"]),
                        kind=item_kind,
                        action_primitives=[action],
                        target=path,
                        constraints=dict(entry["constraints"]),
                        metadata={
                            "source": self.name,
                            "labutopia_task_name": config.get("name"),
                            "task_type": task_type,
                            "controller_type": config.get("controller_type"),
                            "source_file": config.get("_source_file"),
                            "usd_path": config.get("usd_path"),
                        },
                    )
                )
        return result

    def query_action_schema(self, action: str) -> Optional[ActionSchema]:
        return self._schemas_by_task.get(action) or self._schemas_by_action.get(action)

    def query_task_schema(self, task_name: str) -> Optional[ActionSchema]:
        """Return the action schema bound to the given LabUtopia task name.

        Unlike ``query_action_schema(action)`` which returns the first schema
        registered for an action and is therefore shared across tasks, this
        accessor returns the per-task schema whose ``metadata.labutopia_task_name``
        matches ``task_name`` exactly.
        """
        return self._schemas_by_task.get(task_name)

    def query_safety_zones(self):
        return []
