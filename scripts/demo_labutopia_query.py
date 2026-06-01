#!/usr/bin/env python3
"""Demo: use LabUtopia scenes/assets as the query source, then exercise the
Phase 3 query interfaces over a *real scene* (not just static builtin schemas).

This is transport-free (in-process), so it needs neither the edge, ROS2, cloud,
nor a proxy — only the LabUtopia asset cards + task configs on disk.

Usage:
    python scripts/demo_labutopia_query.py \\
        --asset_cards <dir of *.json asset cards> \\
        --labutopia_config <dir of LabUtopia *.yaml task configs>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unilabos_client import RoboUniLabOS, RemoteQueryError


def _short(obj, n=400):
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " ...}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset_cards", required=True)
    ap.add_argument("--labutopia_config", required=True)
    args = ap.parse_args()

    client = RoboUniLabOS.from_sources(
        asset_cards=args.asset_cards,
        labutopia_config=args.labutopia_config,
    )

    # enumerate scene entities from the asset-card filenames
    asset_ids = sorted(p.stem for p in Path(args.asset_cards).glob("*.json") if p.name != "summary.json")
    print(f"== LabUtopia scene loaded: {len(asset_ids)} assets ==")
    print("  e.g.:", ", ".join(asset_ids[:8]), "...")

    # pick representative targets across affordance kinds
    def pick(substr):
        return next((a for a in asset_ids if substr.lower() in a.lower()), None)

    targets = [t for t in {pick("button"), pick("beaker"), pick("cabinet"), pick("dryingbox")} if t]
    print(f"\n== probing targets: {targets} ==")

    for t in targets:
        print(f"\n--- {t} ---")
        for op, call in (
            ("query_pose", lambda: client.query_pose(t)),
            ("query_affordance", lambda: client.query_affordance(t)),
            ("query_state", lambda: client.query_state(t)),
        ):
            try:
                print(f"  [{op}] {_short(call())}")
            except RemoteQueryError as e:
                print(f"  [{op}] (none: {e.code})")

    print("\n== action schemas derived from LabUtopia task configs ==")
    for action in ("press_button", "open_lid", "pour", "move_to"):
        try:
            sch = client.query_action_schema(action)
            print(f"  [{action}] postconditions={_short(sch.get('postconditions'))} src={sch.get('metadata', {}).get('source')}")
        except RemoteQueryError as e:
            print(f"  [{action}] (none: {e.code})")

    try:
        zones = client.query_safety_zones()
        zlist = zones.get("safety_zones", [])
        kinds = {}
        for z in zlist:
            kinds[z["zone_type"]] = kinds.get(z["zone_type"], 0) + 1
        print(f"\n== query_safety_zones: {len(zlist)} zones {kinds} ==")
    except RemoteQueryError as e:
        print(f"\n== query_safety_zones (none: {e.code}) ==")

    print("\n== DONE ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
