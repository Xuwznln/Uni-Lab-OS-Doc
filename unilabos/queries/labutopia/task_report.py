from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from unilabos.queries.engine import QueryEngine, QueryNotFound
from unilabos.queries.labutopia.action_smoke import build_action_smoke
from unilabos.queries.labutopia.scene_source import LabUtopiaSceneSource
from unilabos.queries.labutopia.task_configs import _action_for_task_type, _target_entries_from_config
from unilabos.queries.labutopia.usd_source import LabUtopiaUsdSource


def _start_isaac_app():
    try:
        from isaacsim import SimulationApp
    except ImportError:
        from omni.isaac.kit import SimulationApp
    return SimulationApp({"headless": True})


def _query_or_error(query):
    try:
        value = query()
        if hasattr(value, "to_dict"):
            return {"ok": True, "value": value.to_dict()}
        return {"ok": True, "value": value}
    except QueryNotFound as exc:
        return {"ok": False, "error": str(exc)}
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _postcondition_types(schema: Optional[Dict[str, Any]]) -> List[str]:
    if not schema:
        return []
    return [str(item.get("type")) for item in schema.get("postconditions", []) if isinstance(item, dict)]


def _pose_source_type(pose_payload: Dict[str, Any]) -> Optional[str]:
    if not pose_payload.get("ok"):
        return None
    metadata = (pose_payload.get("value") or {}).get("metadata") or {}
    explicit = metadata.get("source_type")
    if explicit:
        return str(explicit)
    return str((pose_payload.get("value") or {}).get("source") or "unknown")


def _target_report(engine: QueryEngine, entry: Dict[str, Any]) -> Dict[str, Any]:
    target = str(entry["path"])
    pose = _query_or_error(lambda: engine.query_pose(target))
    state = _query_or_error(lambda: engine.query_state(target))
    affordances = _query_or_error(lambda: [item.to_dict() for item in engine.query_affordance(target)])

    missing = []
    if not pose["ok"]:
        missing.append("pose")
    if not state["ok"]:
        missing.append("state")
    if not affordances["ok"]:
        missing.append("affordance")
    values = state.get("value", {}).get("values", {}) if state["ok"] else {}
    return {
        "target": target,
        "role": entry.get("role"),
        "constraints": dict(entry.get("constraints") or {}),
        "pose": pose,
        "pose_source_type": _pose_source_type(pose),
        "state": state,
        "affordances": affordances,
        "bbox_size": values.get("bbox_size"),
        "missing": missing,
    }


def _task_status(task: Dict[str, Any]) -> str:
    if task["missing"]:
        return "partial"
    if not task["targets"]:
        return "metadata_only"
    return "ready_for_policy_or_controller"


def generate_task_report(
    config_dir: str | Path,
    labutopia_root: Optional[str | Path] = None,
    usd_source_factory=LabUtopiaUsdSource,
    include_action_smoke: bool = True,
) -> Dict[str, Any]:
    scene = LabUtopiaSceneSource.from_directory(
        config_dir,
        labutopia_root=labutopia_root,
        usd_source_factory=usd_source_factory,
    )
    engine = QueryEngine(sources=[scene])

    tasks: List[Dict[str, Any]] = []
    for config in scene.configs:
        task_name = str(config.get("name") or config.get("_file_stem") or "")
        task_type = str(config.get("task_type") or "")
        action = _action_for_task_type(task_type)
        resolved_usd = scene.resolve_usd_path(config.get("usd_path"))

        # Prefer task-scoped schema metadata so multi-task / same-action collisions
        # do not cause `schema.metadata.labutopia_task_name` to point at a sibling.
        task_schema = scene.task_source.query_task_schema(task_name) if task_name else None
        if task_schema is not None:
            schema = {"ok": True, "value": task_schema.to_dict()}
        else:
            schema = _query_or_error(lambda action=action: engine.query_action_schema(action))
        schema_value = schema["value"] if schema["ok"] else None
        target_reports = [_target_report(engine, entry) for entry in _target_entries_from_config(config)]

        missing = []
        if resolved_usd is None or not Path(resolved_usd).exists():
            missing.append("usd")
        if not schema["ok"]:
            missing.append("action_schema")
        if not target_reports:
            missing.append("targets")
        for target in target_reports:
            missing.extend(f"{target['role']}:{item}" for item in target["missing"])

        action_smoke = None
        if include_action_smoke and schema["ok"] and action == "press_button":
            first_target = target_reports[0]["target"] if target_reports else None
            action_smoke = _query_or_error(lambda action=action, first_target=first_target: build_action_smoke(engine, action, target=first_target))

        task_report = {
            "name": task_name,
            "task_type": task_type,
            "action": action,
            "mode": config.get("mode"),
            "source_file": config.get("_source_file"),
            "usd_path": config.get("usd_path"),
            "resolved_usd_path": str(resolved_usd) if resolved_usd is not None else None,
            "schema": schema,
            "postcondition_types": _postcondition_types(schema_value),
            "targets": target_reports,
            "missing": missing,
            "action_smoke": action_smoke,
        }
        task_report["status"] = _task_status(task_report)
        tasks.append(task_report)

    summary = _summarize_tasks(tasks)
    return {
        "ok": True,
        "source": "labutopia_task_report",
        "config_dir": str(config_dir),
        "labutopia_root": str(labutopia_root) if labutopia_root else None,
        "summary": summary,
        "tasks": tasks,
    }


def generate_task_report_with_optional_isaac(
    config_dir: str | Path,
    labutopia_root: Optional[str | Path] = None,
    usd_source_factory=LabUtopiaUsdSource,
    include_action_smoke: bool = True,
    isaac_headless: bool = False,
    isaac_steps: int = 1,
    close_isaac_app: bool = False,
) -> Dict[str, Any]:
    """Generate the LabUtopia readiness report, optionally inside Isaac headless.

    Programmatic callers get the report dict back before serialization, so this
    helper does not close the Isaac SimulationApp by default. The module CLI
    handles the close after it has written stdout and the optional output file.
    """
    if not isaac_headless:
        return generate_task_report(
            config_dir=config_dir,
            labutopia_root=labutopia_root,
            usd_source_factory=usd_source_factory,
            include_action_smoke=include_action_smoke,
        )

    app = _start_isaac_app()
    try:
        for _ in range(max(0, isaac_steps)):
            app.update()
        report = generate_task_report(
            config_dir=config_dir,
            labutopia_root=labutopia_root,
            usd_source_factory=usd_source_factory,
            include_action_smoke=include_action_smoke,
        )
        report["runtime"] = {
            "isaac_headless": True,
            "steps": isaac_steps,
        }
        return report
    finally:
        if close_isaac_app:
            app.close()


def _summarize_tasks(tasks: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    task_list = list(tasks)
    by_action: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    missing_counts: Dict[str, int] = {}
    pose_source_mix: Dict[str, int] = {}
    fallback_targets: List[Dict[str, Any]] = []
    for task in task_list:
        by_action[task["action"]] = by_action.get(task["action"], 0) + 1
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
        for item in task.get("missing", []):
            missing_counts[item] = missing_counts.get(item, 0) + 1
        for target in task.get("targets", []):
            source_type = target.get("pose_source_type") or "unknown"
            pose_source_mix[source_type] = pose_source_mix.get(source_type, 0) + 1
            metadata = ((target.get("pose") or {}).get("value") or {}).get("metadata") or {}
            if metadata.get("resolved_by_fallback"):
                fallback_targets.append(
                    {
                        "task": task.get("name"),
                        "target": target.get("target"),
                        "resolved_prim_path": metadata.get("resolved_prim_path"),
                    }
                )
    return {
        "task_count": len(task_list),
        "by_action": by_action,
        "by_status": by_status,
        "missing_counts": missing_counts,
        "pose_source_mix": pose_source_mix,
        "resolved_by_fallback_targets": fallback_targets,
    }


def write_task_report(report: Dict[str, Any], output: str | Path, indent: int = 2) -> None:
    Path(output).expanduser().write_text(json.dumps(report, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a Robo-UniLabOS readiness report for LabUtopia task configs.")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--labutopia-root")
    parser.add_argument("--output")
    parser.add_argument("--no-action-smoke", action="store_true")
    parser.add_argument("--isaac-headless", action="store_true", help="Start Isaac headless so direct USD/pxr queries are available.")
    parser.add_argument("--isaac-steps", type=int, default=1, help="Isaac app.update() steps before generating the report.")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    if args.isaac_headless:
        app = _start_isaac_app()
        try:
            for _ in range(max(0, args.isaac_steps)):
                app.update()
            report = generate_task_report(
                config_dir=args.config_dir,
                labutopia_root=args.labutopia_root,
                include_action_smoke=not args.no_action_smoke,
            )
            report["runtime"] = {
                "isaac_headless": True,
                "steps": args.isaac_steps,
            }
            if args.output:
                write_task_report(report, args.output, indent=args.indent)
            print(json.dumps(report, ensure_ascii=False, indent=args.indent), flush=True)
            return 0 if report.get("ok") else 1
        finally:
            app.close()

    report = generate_task_report(
        config_dir=args.config_dir,
        labutopia_root=args.labutopia_root,
        include_action_smoke=not args.no_action_smoke,
    )
    if args.output:
        write_task_report(report, args.output, indent=args.indent)
    print(json.dumps(report, ensure_ascii=False, indent=args.indent))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
