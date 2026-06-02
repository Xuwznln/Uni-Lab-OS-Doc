from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from unilabos.hal.mock import MockHAL
from unilabos.queries.action_catalog_source import ActionCatalogSource
from unilabos.queries.engine import QueryEngine
from unilabos.queries.labutopia import (
    LabUtopiaAssetCardSource,
    LabUtopiaSceneSource,
    LabUtopiaUsdSource,
)
from unilabos.queries.labutopia.asset_card_generator import generate_asset_cards_to_directory
from unilabos.queries.resource_map_source import ResourceMapSource
from unilabos.queries.robot_asset import robot_model_source_from_asset
from unilabos.robo_unilabos import operations
from unilabos.robo_unilabos.asset_pack import build_asset_pack
from unilabos.robo_unilabos.resource_map import ResourceMap


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robo-unilabos",
        description="Robot-facing Robo-UniLabOS CLI.",
    )
    parser.add_argument("-g", "--graph", help="Uni-Lab-OS graph JSON with extra.robo_unilabos data.")
    parser.add_argument("--asset-cards", help="Directory of LabUtopia-style asset card JSON files.")
    parser.add_argument("--action-catalog", help="Generated Robo-UniLabOS action catalog JSON.")
    parser.add_argument("--labutopia-config", help="Directory of LabUtopia task YAML files.")
    parser.add_argument("--labutopia-root", help="Root used to resolve relative LabUtopia usd_path values.")
    parser.add_argument("--usd", help="LabUtopia USD stage path for direct prim pose/state queries.")
    parser.add_argument("--robot-asset", action="append", default=[], help="Robot asset directory or asset_manifest.json to expose as a query source.")
    parser.add_argument("--mock-hal", action="append", default=[], help="Register a MockHAL robot id for query pose/state tests.")
    parser.add_argument("--indent", type=int, default=None, help="Pretty-print JSON output.")

    surfaces = parser.add_subparsers(dest="surface", required=True)
    lab_parser = surfaces.add_parser("lab", help="Resource, spatial, safety, and transaction commands.")
    lab_commands = lab_parser.add_subparsers(dest="lab_command", required=True)

    list_parser = lab_commands.add_parser("list", help="List laboratory objects.")
    list_targets = list_parser.add_subparsers(dest="list_target", required=True)
    list_resources = list_targets.add_parser("resources", help="List robot-operable resources.")
    list_resources.add_argument("--all", action="store_true", help="Include graph nodes without robot-operable data.")

    inspect_parser = lab_commands.add_parser("inspect", help="Inspect a resource.")
    inspect_parser.add_argument("resource_id")

    where_parser = lab_commands.add_parser("where", help="Return the pose for a resource or affordance target.")
    where_parser.add_argument("target", help="Resource id or resource.affordance target.")

    affordances_parser = lab_commands.add_parser("affordances", help="List resource affordances.")
    affordances_parser.add_argument("resource_id")

    reachable_parser = lab_commands.add_parser("reachable", help="Check whether a robot can reach a target.")
    reachable_parser.add_argument("target", help="Resource id or resource.affordance target.")
    reachable_parser.add_argument("--robot", required=True, help="Robot resource id.")

    query_parser = surfaces.add_parser("query", help="Phase 13 query API commands.")
    query_commands = query_parser.add_subparsers(dest="query_command", required=True)

    pose_parser = query_commands.add_parser("pose", help="Run query_pose(target).")
    pose_parser.add_argument("target")
    pose_parser.add_argument("--frame")

    state_parser = query_commands.add_parser("state", help="Run query_state(target).")
    state_parser.add_argument("target")

    affordance_parser = query_commands.add_parser("affordance", help="Run query_affordance(target).")
    affordance_parser.add_argument("target")
    affordance_parser.add_argument("--kind")

    action_schema_parser = query_commands.add_parser("action-schema", help="Run query_action_schema(action).")
    action_schema_parser.add_argument("action")

    query_commands.add_parser("safety-zones", help="Run query_safety_zones().")

    verification_parser = query_commands.add_parser("verification", help="Run query_verification(task_id).")
    verification_parser.add_argument("task_id")
    verification_parser.add_argument("--action", help="Use an action schema's postconditions.")
    verification_parser.add_argument("--context-json", default="{}", help="Verification context JSON.")

    labutopia_parser = surfaces.add_parser("labutopia", help="LabUtopia import and simulation utilities.")
    labutopia_commands = labutopia_parser.add_subparsers(dest="labutopia_command", required=True)

    generate_parser = labutopia_commands.add_parser("generate-asset-cards", help="Generate asset cards from LabUtopia task YAML and USD files.")
    generate_parser.add_argument("--config-dir", help="LabUtopia config directory. Defaults to --labutopia-config.")
    generate_parser.add_argument("--output-dir", required=True, help="Directory for generated asset card JSON files.")
    generate_parser.add_argument("--root", help="LabUtopia root. Defaults to --labutopia-root.")
    generate_parser.add_argument("--isaac-headless", action="store_true", help="Start Isaac headless before direct USD queries.")
    generate_parser.add_argument("--isaac-steps", type=int, default=1)
    generate_parser.add_argument("--clean", action="store_true", help="Remove stale card JSON files from output-dir before writing.")

    smoke_parser = labutopia_commands.add_parser("isaac-smoke", help="Load a LabUtopia USD in Isaac headless and emit a query report.")
    smoke_parser.add_argument("--usd", required=True, help="USD file to load.")
    smoke_parser.add_argument("--target", required=True, help="Prim path to query after stepping.")
    smoke_parser.add_argument("--config-dir", help="LabUtopia config directory. Defaults to --labutopia-config.")
    smoke_parser.add_argument("--root", help="LabUtopia root. Defaults to --labutopia-root.")
    smoke_parser.add_argument("--action", help="Optional action schema to include in the report.")
    smoke_parser.add_argument("--steps", type=int, default=1)
    smoke_parser.add_argument("--output", help="Optional JSON output path.")

    report_parser = labutopia_commands.add_parser("task-report", help="Generate a LabUtopia task readiness report.")
    report_parser.add_argument("--config-dir", help="LabUtopia config directory. Defaults to --labutopia-config.")
    report_parser.add_argument("--root", help="LabUtopia root. Defaults to --labutopia-root.")
    report_parser.add_argument("--output", help="Optional JSON output path.")
    report_parser.add_argument("--no-action-smoke", action="store_true")
    report_parser.add_argument("--isaac-headless", action="store_true", help="Start Isaac headless before direct USD queries.")
    report_parser.add_argument("--isaac-steps", type=int, default=1)

    action_smoke_parser = labutopia_commands.add_parser("action-smoke", help="Generate a classical action contract smoke report.")
    action_smoke_parser.add_argument("--action", required=True)
    action_smoke_parser.add_argument("--target")
    action_smoke_parser.add_argument("--config-dir", help="LabUtopia config directory. Defaults to --labutopia-config.")
    action_smoke_parser.add_argument("--root", help="LabUtopia root. Defaults to --labutopia-root.")
    action_smoke_parser.add_argument("--output", help="Optional JSON output path.")

    assets_parser = surfaces.add_parser("assets", help="Build and inspect Robo-UniLabOS asset packs.")
    assets_commands = assets_parser.add_subparsers(dest="assets_command", required=True)

    build_pack_parser = assets_commands.add_parser("build-pack", help="Build a canonical resource map and action catalog from existing assets.")
    build_pack_parser.add_argument("--asset-cards", dest="pack_asset_cards", help="Asset card directory. Defaults to global --asset-cards.")
    build_pack_parser.add_argument("--task-report", required=True, help="LabUtopia task report JSON.")
    build_pack_parser.add_argument("--output-dir", required=True, help="Directory where resource_map.json, action_catalog.json, and manifest.json are written.")
    build_pack_parser.add_argument("--robot-asset", dest="pack_robot_assets", action="append", default=[], help="Robot asset directory or manifest to include in the resource map.")
    build_pack_parser.add_argument("--real-asset-card", action="append", default=[], help="Real RGB-D asset_card.json to include in the resource map.")
    build_pack_parser.add_argument("--startup-config", action="append", default=[], help="Uni-Lab-OS startup_config.json snapshot to import as real resource tree nodes.")
    build_pack_parser.add_argument("--horizon-root", action="append", default=[], help="Horizon/Arm7 asset import root to include as digital twin nodes.")
    build_pack_parser.add_argument("--reachable-by", action="append", default=[], help="Robot id to mark as explicitly reachable for generated sim affordances.")
    build_pack_parser.add_argument("--sim-robot-id", help="Add a LabUtopia simulation robot node and use it for default sim reachability.")

    return parser


def _require_graph(args: argparse.Namespace) -> ResourceMap:
    if not args.graph:
        raise ValueError("--graph is required for lab commands")
    return ResourceMap.from_file(args.graph)


def _build_query_engine(args: argparse.Namespace) -> QueryEngine:
    sources = []
    for robot_asset in args.robot_asset:
        sources.append(robot_model_source_from_asset(robot_asset))
    if args.graph:
        sources.append(ResourceMapSource.from_file(args.graph))
    if args.asset_cards:
        sources.append(LabUtopiaAssetCardSource.from_directory(args.asset_cards))
    if args.action_catalog:
        sources.append(ActionCatalogSource.from_file(args.action_catalog))
    if args.labutopia_config:
        sources.append(LabUtopiaSceneSource.from_directory(args.labutopia_config, labutopia_root=args.labutopia_root))
    if args.usd:
        sources.append(LabUtopiaUsdSource(args.usd))
    engine = QueryEngine(sources=sources)
    for robot_id in args.mock_hal:
        engine.hal_registry.register(robot_id, MockHAL(robot_id=robot_id))
    return engine


def _dispatch(args: argparse.Namespace) -> Dict[str, Any]:
    if args.surface == "lab" and args.lab_command == "list" and args.list_target == "resources":
        resource_map = _require_graph(args)
        return operations.list_resources(resource_map, include_all=args.all).to_dict()
    if args.surface == "lab" and args.lab_command == "inspect":
        resource_map = _require_graph(args)
        return operations.inspect_resource(resource_map, args.resource_id).to_dict()
    if args.surface == "lab" and args.lab_command == "where":
        resource_map = _require_graph(args)
        return operations.where(resource_map, args.target).to_dict()
    if args.surface == "lab" and args.lab_command == "affordances":
        resource_map = _require_graph(args)
        return operations.affordances(resource_map, args.resource_id).to_dict()
    if args.surface == "lab" and args.lab_command == "reachable":
        resource_map = _require_graph(args)
        return operations.reachable(resource_map, args.target, args.robot).to_dict()
    if args.surface == "query":
        engine = _build_query_engine(args)
        if args.query_command == "pose":
            payload = engine.query_pose(args.target, frame=args.frame).to_dict()
        elif args.query_command == "state":
            payload = engine.query_state(args.target).to_dict()
        elif args.query_command == "affordance":
            payload = {"affordances": [item.to_dict() for item in engine.query_affordance(args.target, kind=args.kind)]}
        elif args.query_command == "action-schema":
            payload = engine.query_action_schema(args.action).to_dict()
        elif args.query_command == "safety-zones":
            payload = {"safety_zones": [item.to_dict() for item in engine.query_safety_zones()]}
        elif args.query_command == "verification":
            context = json.loads(args.context_json)
            payload = engine.query_verification(args.task_id, context=context, action=args.action).to_dict()
        else:
            raise ValueError(f"Unsupported query command: {args.query_command}")
        return operations.result(f"query.{args.query_command}", payload, graph_source=args.graph).to_dict()
    if args.surface == "labutopia" and args.labutopia_command == "generate-asset-cards":
        config_dir = args.config_dir or args.labutopia_config
        if not config_dir:
            raise ValueError("--config-dir or --labutopia-config is required")
        payload = generate_asset_cards_to_directory(
            config_dir=config_dir,
            output_dir=args.output_dir,
            labutopia_root=args.root or args.labutopia_root,
            isaac_headless=args.isaac_headless,
            isaac_steps=args.isaac_steps,
            clean_existing=args.clean,
        )
        return operations.result("labutopia.generate-asset-cards", payload, graph_source=args.graph).to_dict()
    if args.surface == "labutopia" and args.labutopia_command == "isaac-smoke":
        from unilabos.queries.labutopia.isaac_headless_smoke import run_isaac_headless_smoke

        payload = run_isaac_headless_smoke(
            usd_path=args.usd,
            target=args.target,
            config_dir=args.config_dir or args.labutopia_config,
            labutopia_root=args.root or args.labutopia_root,
            action=args.action,
            steps=args.steps,
            output_path=args.output,
            indent=args.indent if args.indent is not None else 2,
        )
        return operations.result(
            "labutopia.isaac-smoke",
            payload,
            ok=bool(payload.get("ok")),
            error=payload.get("error"),
            graph_source=args.graph,
        ).to_dict()
    if args.surface == "labutopia" and args.labutopia_command == "task-report":
        from unilabos.queries.labutopia.task_report import generate_task_report_with_optional_isaac

        config_dir = args.config_dir or args.labutopia_config
        if not config_dir:
            raise ValueError("--config-dir or --labutopia-config is required")
        payload = generate_task_report_with_optional_isaac(
            config_dir=config_dir,
            labutopia_root=args.root or args.labutopia_root,
            include_action_smoke=not args.no_action_smoke,
            isaac_headless=args.isaac_headless,
            isaac_steps=args.isaac_steps,
        )
        if args.output:
            file_indent = args.indent if args.indent is not None else 2
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=file_indent)
                handle.write("\n")
        return operations.result("labutopia.task-report", payload, graph_source=args.graph).to_dict()
    if args.surface == "labutopia" and args.labutopia_command == "action-smoke":
        from unilabos.queries.labutopia.action_smoke import build_action_smoke

        sources = []
        config_dir = args.config_dir or args.labutopia_config
        if config_dir:
            sources.append(LabUtopiaSceneSource.from_directory(config_dir, labutopia_root=args.root or args.labutopia_root))
        if args.usd:
            sources.append(LabUtopiaUsdSource(args.usd))
        payload = build_action_smoke(QueryEngine(sources=sources), args.action, target=args.target)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=args.indent)
                handle.write("\n")
        return operations.result("labutopia.action-smoke", payload, graph_source=args.graph).to_dict()
    if args.surface == "assets" and args.assets_command == "build-pack":
        asset_cards = args.pack_asset_cards or args.asset_cards
        if not asset_cards:
            raise ValueError("--asset-cards is required for assets build-pack")
        payload = build_asset_pack(
            asset_card_dir=asset_cards,
            task_report_path=args.task_report,
            output_dir=args.output_dir,
            robot_assets=[*args.robot_asset, *args.pack_robot_assets],
            real_asset_cards=args.real_asset_card,
            startup_configs=args.startup_config,
            horizon_roots=args.horizon_root,
            reachable_by=args.reachable_by,
            sim_robot_id=args.sim_robot_id,
            indent=args.indent if args.indent is not None else 2,
        )
        return operations.result("assets.build-pack", payload, ok=bool(payload.get("ok")), graph_source=args.graph).to_dict()
    raise ValueError(f"Unsupported command: {args.surface}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        payload = _dispatch(args)
        exit_code = 0 if payload.get("ok") else 1
    except Exception as exc:
        payload = {
            "ok": False,
            "command": "robo-unilabos",
            "transaction_id": None,
            "resource_locks": [],
            "observations": {},
            "provenance": {},
            "error": str(exc),
        }
        exit_code = 1
    print(json.dumps(payload, ensure_ascii=False, indent=args.indent))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
