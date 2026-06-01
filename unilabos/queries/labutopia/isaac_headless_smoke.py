from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from unilabos.queries.engine import QueryEngine, QueryNotFound
from unilabos.queries.labutopia.action_smoke import build_action_smoke
from unilabos.queries.labutopia.scene_source import LabUtopiaSceneSource
from unilabos.queries.labutopia.usd_source import LabUtopiaUsdSource


def _start_simulation_app():
    try:
        from isaacsim import SimulationApp
    except ImportError:
        from omni.isaac.kit import SimulationApp
    return SimulationApp({"headless": True})


def _open_stage(usd_path: str):
    from pxr import Usd

    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        raise RuntimeError(f"Isaac could not open USD stage: {usd_path}")
    return stage


def _stage_target_report(stage, target: str) -> Dict[str, Any]:
    prim = stage.GetPrimAtPath(target)
    if not prim or not prim.IsValid():
        return {"target": target, "exists": False}
    return {
        "target": target,
        "exists": True,
        "type_name": prim.GetTypeName(),
        "children": [str(child.GetPath()) for child in prim.GetChildren()],
    }


def _query_report(
    usd_path: str,
    target: str,
    config_dir: Optional[str] = None,
    labutopia_root: Optional[str] = None,
    action: Optional[str] = None,
) -> Dict[str, Any]:
    sources = []
    if config_dir:
        sources.append(LabUtopiaSceneSource.from_directory(config_dir, labutopia_root=labutopia_root))
    sources.append(LabUtopiaUsdSource(usd_path))
    engine = QueryEngine(sources=sources)

    report: Dict[str, Any] = {}
    for name, query in (
        ("pose", lambda: engine.query_pose(target).to_dict()),
        ("state", lambda: engine.query_state(target).to_dict()),
        ("affordances", lambda: [item.to_dict() for item in engine.query_affordance(target)]),
    ):
        try:
            report[name] = query()
        except QueryNotFound as exc:
            report[name] = {"ok": False, "error": str(exc)}
    if action:
        try:
            report["action_schema"] = engine.query_action_schema(action).to_dict()
            report["action_smoke"] = build_action_smoke(engine, action, target=target)
        except QueryNotFound as exc:
            report["action_schema"] = {"ok": False, "error": str(exc)}
    return report


def run_isaac_headless_smoke(
    usd_path: str | Path,
    target: str,
    config_dir: Optional[str | Path] = None,
    labutopia_root: Optional[str | Path] = None,
    action: Optional[str] = None,
    steps: int = 1,
    output_path: Optional[str | Path] = None,
    indent: int = 2,
    emit_stdout: bool = False,
) -> Dict[str, Any]:
    usd = str(Path(usd_path).expanduser())
    config = str(Path(config_dir).expanduser()) if config_dir else None
    root = str(Path(labutopia_root).expanduser()) if labutopia_root else None

    app = _start_simulation_app()
    try:
        stage = _open_stage(usd)
        for _ in range(max(0, steps)):
            app.update()
        report = {
            "ok": True,
            "runtime": "isaac_headless",
            "usd_path": usd,
            "target": target,
            "steps": steps,
            "stage": _stage_target_report(stage, target),
            "robo_unilabos_query": _query_report(
                usd_path=usd,
                target=target,
                config_dir=config,
                labutopia_root=root,
                action=action,
            ),
        }
    except Exception as exc:
        report = {
            "ok": False,
            "runtime": "isaac_headless",
            "usd_path": usd,
            "target": target,
            "steps": steps,
            "error": str(exc),
        }
    finally:
        text = json.dumps(report, ensure_ascii=False, indent=indent)
        if output_path:
            Path(output_path).expanduser().write_text(text + "\n", encoding="utf-8")
        if emit_stdout:
            print(text, flush=True)
        app.close()
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Load a LabUtopia USD in Isaac headless and emit a Robo-UniLabOS query report.")
    parser.add_argument("--usd", required=True, help="USD file to load.")
    parser.add_argument("--target", required=True, help="Prim path to query after stepping.")
    parser.add_argument("--config-dir", help="Optional LabUtopia task config directory for action/affordance context.")
    parser.add_argument("--labutopia-root", help="Root used to resolve relative usd_path values.")
    parser.add_argument("--action", help="Optional action schema to include in the report.")
    parser.add_argument("--steps", type=int, default=1, help="Number of Isaac app.update() steps.")
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    report = run_isaac_headless_smoke(
        usd_path=args.usd,
        target=args.target,
        config_dir=args.config_dir,
        labutopia_root=args.labutopia_root,
        action=args.action,
        steps=args.steps,
        output_path=args.output,
        indent=args.indent,
        emit_stdout=True,
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
