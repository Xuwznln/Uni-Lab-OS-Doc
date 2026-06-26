#!/usr/bin/env python3
"""designer 目录 → rmf_agv_routes.json（黑点 + 轨迹 + 设备，#18 §10.6 / #21 §0.2）。

用法:
    python scripts/rmf_build_agv_routes.py \\
        --dir ../uni-lab-designer/layout_optimizer/agv-only/examples/scene_2026-06-16_with_turn \\
        --out ../.rmf_run_logs/maps/latest/rmf_agv_routes.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unilabos.sim.fleet.rmf.layout_optimizer.agv_routes_builder import build_agv_routes
from unilabos.sim.fleet.rmf.layout_optimizer.ingest import load_layout_optimizer_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="designer → rmf_agv_routes.json（黑点+轨迹）")
    ap.add_argument("--dir", required=True, help="layout-optimizer 目录")
    ap.add_argument("--out", required=True, help="输出 rmf_agv_routes.json")
    ap.add_argument("--scene", default="", help="覆盖 sourceScene（可选）")
    ap.add_argument(
        "--no-router",
        action="store_true",
        help="不用 fine 网格算轨迹（geometryM 留空、黑点退化用设备中心；快）",
    )
    args = ap.parse_args()

    artifacts = load_layout_optimizer_dir(args.dir)
    router = None
    if not args.no_router:
        from unilabos.sim.fleet.rmf.layout_optimizer.grid_router import FineGridRouter

        router = FineGridRouter(args.dir)

    routes = build_agv_routes(artifacts, router=router, source_scene=args.scene or None)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(routes, ensure_ascii=False, indent=2), encoding="utf-8")

    m = routes["meta"]
    with_geom = sum(1 for r in routes["routes"] if r["geometryM"])
    print(f"rmf_agv_routes.json → {out}")
    print(
        f"waypoints(黑点)={m['waypointCount']}  routes(轨迹)={m['routeCount']}  "
        f"devices={len(routes['devices'])}  带 geometryM={with_geom}/{m['routeCount']}"
    )


if __name__ == "__main__":
    main()
