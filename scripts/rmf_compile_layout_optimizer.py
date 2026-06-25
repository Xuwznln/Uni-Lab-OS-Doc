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

from unilabos.sim.fleet.rmf.compiler import compile_layout_optimizer_dir, dump_semantic_map_json
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
    args = ap.parse_args()

    from unilabos.sim.fleet.rmf.layout_optimizer.ingest import load_layout_optimizer_dir

    artifacts = load_layout_optimizer_dir(args.dir)
    ir, _building_meters, semantic, transfer_plan = compile_layout_optimizer_dir(
        args.dir,
        lab_uuid=args.lab_uuid,
        scene_hash=args.scene_hash,
        include_coarse_nav=not args.no_coarse_nav,
        snap_devices_to_nav=not args.no_snap,
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    lab = args.lab_uuid or "demo_lab"
    building_name = f"{lab}.building.yaml"
    building, floorplan_path, _bounds = finalize_building_for_dashboard(
        ir,
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
    if ir.has_errors():
        print("警告: 编译含 error 级诊断", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
