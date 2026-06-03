#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from unilabos.sim.backends.isaac.lab_layout import (
    DEFAULT_BEAKER_USD,
    DEFAULT_HOTPLATE_USD,
    DEFAULT_ROBOARM_URDF,
    DEFAULT_TABLE_USD,
    central_island_layout,
    layout_to_manifest,
    render_builder_script,
    validate_layout_assets,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the Isaac builder for the RoboArm Chem 04 lab layout")
    parser.add_argument("--layout", choices=["central-island"], default="central-island")
    parser.add_argument("--builder-out", required=True)
    parser.add_argument("--manifest-out", default=None)
    parser.add_argument("--stage-out", required=True)
    parser.add_argument("--robot-urdf", default=DEFAULT_ROBOARM_URDF)
    parser.add_argument("--table-usd", default=DEFAULT_TABLE_USD)
    parser.add_argument("--hotplate-usd", default=DEFAULT_HOTPLATE_USD)
    parser.add_argument("--beaker-usd", default=DEFAULT_BEAKER_USD)
    parser.add_argument("--check-assets", action="store_true")
    return parser.parse_args(argv)


def _build_layout(args: argparse.Namespace):
    if args.layout != "central-island":
        raise ValueError(f"unsupported layout: {args.layout}")
    return central_island_layout(
        robot_urdf=args.robot_urdf,
        table_usd=args.table_usd,
        hotplate_usd=args.hotplate_usd,
        beaker_usd=args.beaker_usd,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    layout = _build_layout(args)
    if args.check_assets:
        missing = validate_layout_assets(layout)
        if missing:
            raise FileNotFoundError("Isaac 布局资产缺失: " + "; ".join(missing))

    builder_path = Path(args.builder_out)
    builder_path.parent.mkdir(parents=True, exist_ok=True)
    builder_path.write_text(render_builder_script(layout, default_output_stage=args.stage_out), encoding="utf-8")

    if args.manifest_out:
        manifest_path = Path(args.manifest_out)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = layout_to_manifest(layout, output_stage=args.stage_out)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "layout": layout.name,
                "builder": str(builder_path),
                "manifest": str(args.manifest_out) if args.manifest_out else None,
                "stage": str(args.stage_out),
                "query_targets": layout.query_targets,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
