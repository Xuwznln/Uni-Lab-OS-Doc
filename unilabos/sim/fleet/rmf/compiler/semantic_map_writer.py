"""RmfMapIR → semantic_map.json（#18 §4.1 / #17 §6.3）。

承载 building.yaml 无法表达、但运行/回流需要的语义映射：
- waypoint ↔ Pascal 设备 uuid（用于 overlay 选中、回流定位）
- waypoint → SEER target id（真实 AGV 下发用）
- restricted / cleaning zone（lane 生成约束 + 前端高亮）
"""

from __future__ import annotations

from typing import Any, Dict, List

from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfMapIR


def build_semantic_map(
    ir: RmfMapIR,
    waypoint_device_uuid: Dict[str, str] | None = None,
    restricted_zones: List[Dict[str, Any]] | None = None,
    waypoint_to_instance: Dict[str, str] | None = None,
    transfer_plan_ref: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """组装 semantic_map.json 内容。

    Args:
        ir: 编译 IR。
        waypoint_device_uuid: waypoint 名 → Pascal 设备 uuid（来自 scene 解析）。
        restricted_zones: 受限区域多边形列表（米制）。
    """
    seer_targets: Dict[str, str] = {}
    for robot in ir.robots:
        if robot.kind == "real" and robot.target_map:
            for waypoint, target_id in robot.target_map.items():
                seer_targets[waypoint] = target_id

    chargers: List[str] = []
    pickups: List[str] = []
    dropoffs: List[str] = []
    for level in ir.levels:
        for v in level.vertices:
            if v.params.get("is_charger"):
                chargers.append(v.name)
            if v.params.get("pickup_dispenser"):
                pickups.append(v.name)
            if v.params.get("dropoff_ingestor"):
                dropoffs.append(v.name)

    result: Dict[str, Any] = {
        "lab_uuid": ir.lab_uuid,
        "scene_hash": ir.scene_hash,
        "building_name": ir.building_name,
        "coordinate_system": ir.coordinate_system,
        "waypoint_device_uuid": dict(waypoint_device_uuid or {}),
        "seer_targets": seer_targets,
        "chargers": chargers,
        "pickups": pickups,
        "dropoffs": dropoffs,
        "restricted_zones": list(restricted_zones or []),
    }
    if waypoint_to_instance:
        result["waypoint_to_instance"] = dict(waypoint_to_instance)
    if transfer_plan_ref:
        result["transfer_plan_ref"] = dict(transfer_plan_ref)
    return result


def dump_semantic_map_json(ir: RmfMapIR, **kwargs: Any) -> str:
    import json

    return json.dumps(build_semantic_map(ir, **kwargs), ensure_ascii=False, indent=2)
