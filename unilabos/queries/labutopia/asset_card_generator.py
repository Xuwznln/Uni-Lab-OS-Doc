from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from unilabos.queries.labutopia.scene_source import (
    LabUtopiaSceneSource,
    UsdSourceFactory,
    labutopia_asset_id_from_prim_path,
)
from unilabos.queries.labutopia.task_configs import (
    _action_for_task_type,
    _affordance_kind_for_action,
    _target_entries_from_config,
)
from unilabos.queries.labutopia.usd_source import LabUtopiaUsdSource


def _start_isaac_app():
    try:
        from isaacsim import SimulationApp
    except ImportError:
        from omni.isaac.kit import SimulationApp
    return SimulationApp({"headless": True})


def _unique(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _geometry_from_state(values: Dict[str, Any]) -> Dict[str, Any]:
    if values.get("target_type") == "navigation_goal":
        source_type = "labutopia_navigation_config"
    elif values.get("target_type") == "task_config_target":
        source_type = "labutopia_task_config_position_range"
    elif values.get("resolved_prim_path") or any(values.get(key) for key in ("bbox_min", "bbox_max", "bbox_size")):
        # Tag as labutopia_usd only when the state values actually came from a
        # USD prim resolution (resolved path or computed bbox), not just any
        # non-empty dict.
        source_type = "labutopia_usd"
    else:
        source_type = "labutopia_task_config"
    geometry: Dict[str, Any] = {
        "source_type": source_type,
    }
    for key in ("bbox_min", "bbox_max", "bbox_size"):
        value = values.get(key)
        if isinstance(value, list):
            mapped_key = "bbox_size_m_approx" if key == "bbox_size" else key
            geometry[mapped_key] = [float(item) for item in value]
    return geometry


def _observable_signals(kinds: Iterable[str]) -> List[str]:
    signals = []
    kind_set = set(kinds)
    if kind_set & {"container", "beaker", "liquid_holder"}:
        signals.extend(["color", "volume_estimate", "liquid_level"])
    if kind_set & {"button", "operable_region", "instrument"}:
        signals.extend(["pose", "device_state"])
    if kind_set & {"articulated"}:
        signals.extend(["joint_position", "open_closed_state"])
    return _unique(signals or ["pose"])


def _center_from_state(values: Dict[str, Any]) -> List[float]:
    bbox_min = values.get("bbox_min")
    bbox_max = values.get("bbox_max")
    if isinstance(bbox_min, list) and isinstance(bbox_max, list) and len(bbox_min) == 3 and len(bbox_max) == 3:
        return [(float(bbox_min[i]) + float(bbox_max[i])) / 2.0 for i in range(3)]
    return []


def _action_hint_details(actions: Iterable[str], kinds: Iterable[str], state_values: Dict[str, Any]) -> Dict[str, Any]:
    action_set = set(actions)
    kind_set = set(kinds)
    center = _center_from_state(state_values)
    bbox_size = state_values.get("bbox_size")
    hints: Dict[str, Any] = {}
    if center:
        hints["nominal_target_pose_xyz"] = center
    elif isinstance(state_values.get("center_xyz"), list) and len(state_values["center_xyz"]) == 3:
        hints["nominal_target_pose_xyz"] = [float(item) for item in state_values["center_xyz"]]
    if isinstance(bbox_size, list) and len(bbox_size) == 3:
        hints["bbox_size_m"] = [float(item) for item in bbox_size]
    if "press_button" in action_set or "button" in kind_set:
        hints["press_button"] = {
            "contact_selector": "bbox_center",
            "axis": "x",
            "pre_contact_offset_m": [-0.06, 0.0, 0.0],
            "press_distance_m": 0.02,
            "verification_signal": "pose_axis_gt",
            "status": "geometric_hint_requires_controller_validation",
        }
    if action_set & {"pour", "place", "pick_place"} or kind_set & {"container", "liquid_holder"}:
        hints["container"] = {
            "opening_selector": "top_center",
            "approach_axis": "z",
            "pre_action_offset_m": [0.0, 0.0, 0.08],
            "status": "geometric_hint_requires_contact_validation",
        }
    if "open_lid" in action_set or "articulated" in kind_set:
        hints["articulated"] = {
            "handle_selector": "role_or_child_prim",
            "approach_axis": "z",
            "status": "requires_joint_axis_discovery",
        }
    if "move_to" in action_set or "navigation_goal" in kind_set:
        center_xyz = state_values.get("center_xyz")
        if isinstance(center_xyz, list) and len(center_xyz) == 3:
            hints["navigation_goal"] = {
                "nominal_goal_xyz": [float(item) for item in center_xyz],
                "x_bounds": state_values.get("x_bounds"),
                "y_bounds": state_values.get("y_bounds"),
                "offset_radius": state_values.get("offset_radius"),
                "status": "sim_navigation_goal_from_labutopia_config",
            }
    return hints


def generate_asset_cards(
    config_dir: str | Path,
    labutopia_root: Optional[str | Path] = None,
    usd_source_factory: UsdSourceFactory = LabUtopiaUsdSource,
) -> List[Dict[str, Any]]:
    scene = LabUtopiaSceneSource.from_directory(
        config_dir,
        labutopia_root=labutopia_root,
        usd_source_factory=usd_source_factory,
    )
    grouped: Dict[str, Dict[str, Any]] = {}
    for config in scene.configs:
        task_type = str(config.get("task_type") or "")
        action = _action_for_task_type(task_type)
        for entry in _target_entries_from_config(config):
            prim_path = entry["path"]
            role = str(entry["role"])
            kind = _affordance_kind_for_action(action, role)
            item = grouped.setdefault(
                prim_path,
                {
                    "prim_path": prim_path,
                    "usd_path": config.get("usd_path"),
                    "resolved_usd_path": str(scene.resolve_usd_path(config.get("usd_path")) or ""),
                    "tasks": [],
                    "roles": [],
                    "kinds": [],
                    "actions": [],
                    "constraints": [],
                    "source_files": [],
                },
            )
            item["tasks"].append(str(config.get("name") or config.get("_file_stem") or task_type))
            item["roles"].append(role)
            item["kinds"].append(kind)
            item["actions"].append(action)
            item["constraints"].append({"role": role, **dict(entry.get("constraints") or {})})
            if config.get("_source_file"):
                item["source_files"].append(str(config["_source_file"]))

    cards: List[Dict[str, Any]] = []
    for prim_path, item in sorted(grouped.items()):
        state = scene.query_state(prim_path)
        state_values = state.values if state is not None else {}
        kinds = _unique(item["kinds"])
        actions = _unique(item["actions"])
        tasks = _unique(item["tasks"])
        card = {
            "asset_id": labutopia_asset_id_from_prim_path(prim_path),
            "source": {
                "dataset": "LabUtopia",
                "usd_path": item["usd_path"],
                "resolved_usd_path": item["resolved_usd_path"],
                "prim_path": prim_path,
                "source_files": _unique(item["source_files"]),
            },
            "asset_class_tags": kinds,
            "geometry": _geometry_from_state(state_values),
            "affordances": kinds,
            "candidate_tasks": tasks,
            "psb_semantics": {
                "observable_or_relevant_signals": _observable_signals(kinds),
            },
            "operation_hints": {
                "action_primitives": actions,
                "roles": _unique(item["roles"]),
                "constraints": item["constraints"],
                "details": _action_hint_details(actions, kinds, state_values),
            },
            "metadata": {
                "generator": "unilabos.queries.labutopia.asset_card_generator",
                "needs_manual_verification": not bool(state_values.get("bbox_size")),
            },
        }
        if state_values.get("children"):
            card["source"]["children"] = list(state_values["children"])
        cards.append(card)
    return cards


def _source_mix_from_cards(cards: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for card in cards:
        source_type = str(((card.get("geometry") or {}).get("source_type")) or "unknown")
        counts[source_type] = counts.get(source_type, 0) + 1
    return counts


def write_asset_cards(
    cards: Iterable[Dict[str, Any]],
    output_dir: str | Path,
    extra_summary: Optional[Dict[str, Any]] = None,
    clean_existing: bool = False,
) -> Dict[str, Any]:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    cards = list(cards)
    if clean_existing:
        new_names = {f"{str(card['asset_id'])}.json" for card in cards}
        for stale in path.glob("*.json"):
            if stale.name == "summary.json":
                continue
            if stale.name not in new_names:
                stale.unlink()
    written = []
    for card in cards:
        asset_id = str(card["asset_id"])
        target = path / f"{asset_id}.json"
        target.write_text(json.dumps(card, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(str(target))
    summary: Dict[str, Any] = {
        "card_count": len(written),
        "cards": written,
        "source_mix": _source_mix_from_cards(cards),
    }
    if extra_summary:
        summary.update(extra_summary)
    (path / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def generate_asset_cards_to_directory(
    config_dir: str | Path,
    output_dir: str | Path,
    labutopia_root: Optional[str | Path] = None,
    usd_source_factory: UsdSourceFactory = LabUtopiaUsdSource,
    isaac_headless: bool = False,
    isaac_steps: int = 1,
    clean_existing: bool = False,
) -> Dict[str, Any]:
    app = None
    if isaac_headless:
        app = _start_isaac_app()
    try:
        if app is not None:
            for _ in range(max(0, isaac_steps)):
                app.update()
        cards = generate_asset_cards(
            config_dir=config_dir,
            labutopia_root=labutopia_root,
            usd_source_factory=usd_source_factory,
        )
        extra: Dict[str, Any] = {
            "config_dir": str(config_dir),
            "labutopia_root": str(labutopia_root) if labutopia_root else None,
        }
        if isaac_headless:
            extra["runtime"] = {
                "isaac_headless": True,
                "steps": isaac_steps,
            }
        summary = write_asset_cards(
            cards,
            output_dir,
            extra_summary=extra,
            clean_existing=clean_existing,
        )
        return summary
    finally:
        if app is not None:
            app.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Robo-UniLabOS asset cards from LabUtopia YAML and USD files.")
    parser.add_argument("--config-dir", required=True, help="LabUtopia config directory containing task YAML files.")
    parser.add_argument("--output-dir", required=True, help="Directory for generated asset card JSON files.")
    parser.add_argument("--labutopia-root", help="Root used to resolve relative usd_path values.")
    parser.add_argument("--isaac-headless", action="store_true", help="Start Isaac headless so direct USD/pxr queries are available.")
    parser.add_argument("--isaac-steps", type=int, default=1, help="Isaac app.update() steps before generating cards.")
    parser.add_argument("--clean", action="store_true", help="Remove stale card JSON files from output-dir before writing.")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    summary = generate_asset_cards_to_directory(
        config_dir=args.config_dir,
        output_dir=args.output_dir,
        labutopia_root=args.labutopia_root,
        isaac_headless=args.isaac_headless,
        isaac_steps=args.isaac_steps,
        clean_existing=args.clean,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=args.indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
