#!/usr/bin/env python3
"""在 building.yaml 的 nav 路点上标注充电桩参数（RMF fleet adapter / Gazebo 需要）。

直接在已有 nav_* 路点上加 is_charger / spawn_robot_*，保证该点进入 nav_graph，
避免孤立 charger 顶点无法被 fleet_adapter 识别。
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml

CHARGER_PARAMS = {
    "is_charger": [4, True],
    "is_holding_point": [4, True],
    "is_parking_spot": [4, True],
}


def _charger_params(robot_name: str) -> Dict[str, List[Any]]:
    return {
        **CHARGER_PARAMS,
        "spawn_robot_name": [1, robot_name],
        "spawn_robot_type": [1, "TinyRobot"],
    }


def inject_chargers(
    building: Dict[str, Any],
    robots: List[Dict[str, str]],
) -> Dict[str, Any]:
    out = copy.deepcopy(building)
    level = out.get("levels", {}).get("L1")
    if not level:
        raise ValueError("building.yaml 缺少 levels.L1")
    vertices: List[Any] = list(level.get("vertices") or [])
    names = {row[3] for row in vertices if len(row) >= 4}
    for spec in robots:
        anchor = spec["anchor_waypoint"]
        robot_name = spec["robot_name"]
        if anchor not in names:
            raise ValueError(f"未找到锚点路点: {anchor}")
        for i, row in enumerate(vertices):
            if len(row) >= 4 and row[3] == anchor:
                params = dict(row[4]) if len(row) > 4 and isinstance(row[4], dict) else {}
                params.update(_charger_params(robot_name))
                vertices[i] = [row[0], row[1], row[2], anchor, params]
                break
    level["vertices"] = vertices
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="向 building.yaml 注入 RMF 仿真充电桩")
    ap.add_argument("--building", required=True, help="building.yaml 路径")
    ap.add_argument("--robot", action="append", default=[], metavar="NAME:ANCHOR", help="机器人:锚点路点，如 unilab_agv1:nav_0")
    ap.add_argument("--manifest", default="", help="可选：写入 manifest.json 片段")
    args = ap.parse_args()

    robots: List[Dict[str, str]] = []
    for item in args.robot:
        name, _, anchor = item.partition(":")
        if not name or not anchor:
            raise SystemExit(f"无效 --robot 参数: {item}")
        robots.append({"robot_name": name, "anchor_waypoint": anchor})

    building_path = Path(args.building)
    building = yaml.safe_load(building_path.read_text(encoding="utf-8"))
    patched = inject_chargers(building, robots)
    building_path.write_text(yaml.safe_dump(patched, sort_keys=True, allow_unicode=True), encoding="utf-8")
    print(f"已在 nav 路点标注 {len(robots)} 个充电桩 → {building_path}")

    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest: Dict[str, Any] = {}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["chargers"] = [r["anchor_waypoint"] for r in robots]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

