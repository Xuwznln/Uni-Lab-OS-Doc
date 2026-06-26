#!/usr/bin/env python3
"""layout-optimizer 目录 → RMF building.yaml + semantic_map + transfer_plan（#21）。

用法:
    python scripts/rmf_compile_layout_optimizer.py \\
        --dir ../uni-lab-designer/layout_optimizer/agv-only/examples/scene_2026-06-16_with_turn \\
        --out /tmp/rmf_layout_out
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unilabos.sim.fleet.rmf.compiler import (
    build_ir_from_agv_routes,
    compile_layout_optimizer_dir,
    dump_semantic_map_json,
)
from unilabos.sim.fleet.rmf.compiler.reference_image_export import FLOORPLAN_FILENAME, finalize_building_for_dashboard


def main() -> None:
    ap = argparse.ArgumentParser(description="layout-optimizer → RMF 编译产物")
    ap.add_argument("--dir", required=True, help="layout-optimizer 输出目录（含 placements/transfers 等）")
    ap.add_argument("--out", required=True, help="产物输出目录")
    ap.add_argument("--lab-uuid", default="demo_lab")
    ap.add_argument("--scene-hash", default="")
    ap.add_argument("--no-coarse-nav", action="store_true")
    ap.add_argument("--no-snap", action="store_true")
    ap.add_argument("--scene", default="", help="场景 JSON（可选，默认从 lab.json _meta.source_scene 解析）")
    ap.add_argument(
        "--route-overrides",
        default="",
        help="可选路线编辑 JSON（#21 §7.0 入口 B；disableLanes/addLanes/setSpeedLimit，按 waypoint 名）",
    )
    ap.add_argument(
        "--black-dots",
        action="store_true",
        help="黑点导航模型（#18 §10.5）：building.yaml 顶点=黑点 dock_* + 走廊折线拐点，去掉所有 nav_*",
    )
    ap.add_argument(
        "--agv-routes",
        default="",
        help="--black-dots 用的 rmf_agv_routes.json 路径（默认 <out>/rmf_agv_routes.json）",
    )
    args = ap.parse_args()

    from unilabos.sim.fleet.rmf.layout_optimizer.ingest import load_layout_optimizer_dir

    route_overrides = None
    if args.route_overrides:
        route_overrides = json.loads(Path(args.route_overrides).read_text(encoding="utf-8"))

    artifacts = load_layout_optimizer_dir(args.dir)
    ir, _building_meters, semantic, transfer_plan = compile_layout_optimizer_dir(
        args.dir,
        lab_uuid=args.lab_uuid,
        scene_hash=args.scene_hash,
        include_coarse_nav=not args.no_coarse_nav,
        snap_devices_to_nav=not args.no_snap,
        route_overrides=route_overrides,
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    lab = args.lab_uuid or "demo_lab"
    building_name = f"{lab}.building.yaml"

    # 黑点导航模型（#18 §10.5）：building.yaml 改用 rmf_agv_routes.json 重建（黑点 + 直角折线，去 nav_*）。
    # semantic_map / transfer_plan 仍取自常规编译，保持引用一致。
    building_ir = ir
    if args.black_dots:
        routes_path = Path(args.agv_routes) if args.agv_routes else (out / "rmf_agv_routes.json")
        if not routes_path.is_file():
            raise SystemExit(f"--black-dots 需要 rmf_agv_routes.json，但未找到: {routes_path}")
        routes_doc = json.loads(routes_path.read_text(encoding="utf-8"))
        building_ir = build_ir_from_agv_routes(
            routes_doc,
            lab_uuid=args.lab_uuid,
            scene_hash=args.scene_hash,
            building_name=ir.building_name,
        )
        for d in building_ir.diagnostics:
            if d.code in {"black_dot_map", "lane_components_bridged"}:
                print(f"[black-dots] {d.message}")

    building, floorplan_path, _bounds = finalize_building_for_dashboard(
        building_ir,
        out,
        lab=artifacts.lab,
        placements=artifacts.placements,
        layout_dir=Path(args.dir),
        scene_path=Path(args.scene) if args.scene else None,
    )
    import yaml

    (out / building_name).write_text(
        yaml.safe_dump(building, sort_keys=True, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    # building_map_server 要求 *.building.yaml 后缀；保留 building.yaml 软链兼容旧脚本
    legacy = out / "building.yaml"
    if legacy.exists() or legacy.is_symlink():
        legacy.unlink()
    legacy.symlink_to(building_name)
    (out / "semantic_map.json").write_text(
        json.dumps(semantic, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "rmf_transfer_plan.json").write_text(
        json.dumps(transfer_plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "diagnostics.json").write_text(
        json.dumps(ir.diagnostics_as_dicts(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"building.yaml → {out / building_name}")
    print(f"floorplan → {floorplan_path}")
    print(f"semantic_map.json → {out / 'semantic_map.json'}")
    print(f"rmf_transfer_plan.json → {out / 'rmf_transfer_plan.json'}")
    print(f"transfers={len(transfer_plan.get('transfers') or [])}  diagnostics={len(ir.diagnostics)}")
    for d in ir.diagnostics:
        if d.code.startswith("route_override"):
            print(f"[route-override] {d.level}: {d.message}")
    if ir.has_errors():
        print("警告: 编译含 error 级诊断", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
