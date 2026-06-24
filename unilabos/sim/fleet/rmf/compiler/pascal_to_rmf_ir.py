"""Pascal 发布版 scene + graph AGV 配置 → RmfMapIR（#18 §4.1 / #17 §6.3）。

输入是后端发布版 scene（`{nodes, rootNodeIds}` 或 `{nodes, links}`）+ 由 `rmf.coordinator`
从 graph 解析出的机器人配置列表。本模块只做几何/语义 → IR 的纯变换，坐标经
`coordinate_transform` 统一换算为 RMF 米制；不连接任何 runtime。

设备 waypoint 语义来自节点的 `data.rmf`（RmfMetadata，见 #17 §9.1）：
- workcellType=charger  → is_charger / is_parking_spot / is_holding_point
- workcellType=dispenser→ pickup_dispenser=<handler>
- workcellType=ingestor → dropoff_ingestor=<handler>
- restricted=true       → 计入 restricted_zones（不进 building.yaml，见 semantic_map）

无法自动推断的内容（charger/lane/target_map 等）由 validation 产出 diagnostics。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from unilabos.sim.fleet.rmf.coordinate_transform import DEFAULT_SCALE, DEFAULT_Y_FLIP, pascal_to_rmf
from unilabos.sim.fleet.rmf.compiler.rmf_ir import (
    RmfDiagnostic,
    RmfLevelIR,
    RmfMapIR,
    RmfRobotIR,
    RmfVertexIR,
)


def _node_position_mm(node: Dict[str, Any]) -> Optional[tuple]:
    """从节点取 (x_mm, y_mm, z_mm, rotation_z)；兼容 pose.position / 顶层 position。"""
    pose = node.get("pose") or {}
    pos = pose.get("position") if isinstance(pose, dict) else None
    if not pos:
        pos = node.get("position")
    if not isinstance(pos, dict):
        return None
    rot = (pose.get("rotation") if isinstance(pose, dict) else None) or node.get("rotation") or {}
    rotation_z = float(rot.get("z", 0.0)) if isinstance(rot, dict) else 0.0
    return (
        float(pos.get("x", 0.0)),
        float(pos.get("y", 0.0)),
        float(pos.get("z", 0.0)),
        rotation_z,
    )


def _rmf_meta(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """取节点的 RMF metadata：优先 data.rmf，其次 metadata.rmf。"""
    data = node.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("rmf"), dict):
        return data["rmf"]
    meta = node.get("metadata") or {}
    if isinstance(meta, dict) and isinstance(meta.get("rmf"), dict):
        return meta["rmf"]
    return None


def _vertex_params_for(meta: Dict[str, Any]) -> Dict[str, Any]:
    """根据 RmfMetadata.workcellType 推导 vertex 语义参数。"""
    params: Dict[str, Any] = {}
    workcell = (meta.get("workcellType") or "").lower()
    if workcell == "charger":
        params.update({"is_charger": True, "is_parking_spot": True, "is_holding_point": True})
    elif workcell == "dispenser":
        params["pickup_dispenser"] = meta.get("pickupWaypoint") or meta.get("placeId") or "dispenser"
        params["is_holding_point"] = True
    elif workcell == "ingestor":
        params["dropoff_ingestor"] = meta.get("dropoffWaypoint") or meta.get("placeId") or "ingestor"
        params["is_holding_point"] = True
    elif workcell in ("dock", "storage", "device"):
        params["is_holding_point"] = True
    return params


def build_rmf_map_ir(
    scene: Dict[str, Any],
    robots: Optional[List[Dict[str, Any]]] = None,
    *,
    lab_uuid: str = "",
    scene_hash: str = "",
    building_name: str = "building",
    default_level: str = "L1",
    scale: float = DEFAULT_SCALE,
    y_flip: bool = DEFAULT_Y_FLIP,
) -> RmfMapIR:
    """把 scene + robots 编译为 RmfMapIR（未校验；调用方再跑 validation.validate_ir）。"""
    ir = RmfMapIR(lab_uuid=lab_uuid, scene_hash=scene_hash, building_name=building_name)
    level = RmfLevelIR(name=default_level, elevation=0.0)
    ir.levels.append(level)

    nodes = scene.get("nodes") or []
    waypoint_device_uuid: Dict[str, str] = {}
    restricted_zones: List[Dict[str, Any]] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        meta = _rmf_meta(node)
        if not meta or meta.get("enabled") is False:
            continue
        pos = _node_position_mm(node)
        if pos is None:
            ir.diagnostics.append(
                RmfDiagnostic("warning", "missing_pose", f"节点 {node.get('id')} 含 rmf 元数据但无 pose，跳过", str(node.get("id", "")))
            )
            continue
        x_m, y_m, _ = pascal_to_rmf(pos[0], pos[1], pos[3], scale=scale, y_flip=y_flip)
        name = meta.get("placeId") or node.get("name") or node.get("id")
        params = _vertex_params_for(meta)
        level.add_vertex(RmfVertexIR(name=str(name), x_m=x_m, y_m=y_m, z_m=pos[2] * scale, params=params))
        if node.get("uuid"):
            waypoint_device_uuid[str(name)] = str(node["uuid"])
        if meta.get("restricted"):
            restricted_zones.append({"waypoint": str(name), "x_m": x_m, "y_m": y_m})

    # 机器人（来自 graph AGV 配置，由 coordinator 传入）
    for rcfg in robots or []:
        ir.robots.append(
            RmfRobotIR(
                robot_name=rcfg.get("robot_name") or rcfg.get("id") or "agv",
                fleet_name=rcfg.get("fleet_name", "unilab_agv"),
                kind=rcfg.get("kind", "sim"),
                footprint_radius=float(rcfg.get("footprint_radius", 0.35)),
                charger_waypoint=rcfg.get("charger_waypoint", ""),
                initial_waypoint=rcfg.get("initial_waypoint", ""),
                spawn_robot_type=rcfg.get("spawn_robot_type", "Open-RMF/TinyRobot"),
                target_map=dict(rcfg.get("target_map", {}) or {}),
            )
        )

    # 把 sim 机器人的出生点写到其 charger waypoint（spawn_robot_name/type）
    for robot in ir.robots:
        if robot.kind == "sim" and robot.charger_waypoint:
            idx = level.index_of(robot.charger_waypoint)
            if idx is not None:
                level.vertices[idx].params.setdefault("spawn_robot_name", robot.robot_name)
                level.vertices[idx].params.setdefault("spawn_robot_type", robot.spawn_robot_type)

    # 把附加产物挂到 IR 上，供 semantic_map_writer 取用（不进 building.yaml）
    ir._waypoint_device_uuid = waypoint_device_uuid  # type: ignore[attr-defined]
    ir._restricted_zones = restricted_zones  # type: ignore[attr-defined]
    return ir
