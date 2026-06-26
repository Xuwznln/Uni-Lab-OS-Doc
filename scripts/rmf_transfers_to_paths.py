#!/usr/bin/env python3
"""transfers.json + path 目录 → rmf_transfer_paths.json（#18 §9.9 / #21 §4）。

用法:
    python scripts/rmf_transfers_to_paths.py \\
        --transfers ../uni-lab-designer/layout_optimizer/agv-only/examples/scene_2026-06-16_with_turn/transfers.json \\
        --catalog   ../.rmf_run_logs/maps/latest/rmf_nav_path_catalog.json \\
        --out       ../.rmf_run_logs/maps/latest/rmf_transfer_paths.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unilabos.sim.fleet.rmf.layout_optimizer.transfer_paths_builder import build_transfer_paths


def main() -> None:
    ap = argparse.ArgumentParser(description="transfers.json → RmfTransferPaths（带轨迹）")
    ap.add_argument("--transfers", required=True, help="layout-optimizer transfers.json")
    ap.add_argument("--catalog", required=True, help="rmf_nav_path_catalog.json（提供 navGraph/dock/slug 等静态参考）")
    ap.add_argument("--out", required=True, help="输出 rmf_transfer_paths.json")
    ap.add_argument(
        "--layout-dir",
        default="",
        help="layout-optimizer 目录；给定则用 fine 网格现算轨迹（推荐，不依赖可能被编辑的 catalog.paths）",
    )
    ap.add_argument("--scene", default="", help="覆盖 sourceScene（可选）")
    args = ap.parse_args()

    transfers_doc = json.loads(Path(args.transfers).read_text(encoding="utf-8"))
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))

    router = None
    if args.layout_dir:
        from unilabos.sim.fleet.rmf.layout_optimizer.grid_router import FineGridRouter

        router = FineGridRouter(args.layout_dir)

    paths = build_transfer_paths(transfers_doc, catalog, router=router, source_scene=args.scene or None)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(paths, ensure_ascii=False, indent=2), encoding="utf-8")

    m = paths["meta"]
    print(f"rmf_transfer_paths.json → {out}")
    print(
        f"transfers={m['transferCount']}  pathsResolved={m['pathsResolved']}  "
        f"pathsMissing={m['pathsMissing']}  deviceWaypoints={len(paths['deviceWaypoints'])}"
    )


if __name__ == "__main__":
    main()
