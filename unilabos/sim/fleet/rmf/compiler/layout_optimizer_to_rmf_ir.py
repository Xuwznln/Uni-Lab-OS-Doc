"""layout-optimizer 产物 → RmfMapIR（#18 §9.4 / #21 §3）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfDiagnostic, RmfLaneIR, RmfLevelIR, RmfMapIR, RmfVertexIR
from unilabos.sim.fleet.rmf.layout_optimizer.dock_resolver import resolve_device_xy
from unilabos.sim.fleet.rmf.layout_optimizer.ingest import LayoutOptimizerArtifacts
from unilabos.sim.fleet.rmf.layout_optimizer.slug import build_instance_waypoint_map


def _coarse_graph(artifacts: LayoutOptimizerArtifacts) -> tuple[list[Dict], list[list[int]]]:
    cg = artifacts.aisle_network.get("coarse_graph") or {}
    nodes = list(cg.get("nodes") or [])
    edges = list(cg.get("edges") or [])
    return nodes, edges


def _snap_device_to_nav(
    level: RmfLevelIR,
    device_idx: int,
    nav_indices: Dict[int, int],
    nodes: List[Dict],
    max_dist_m: float = 2.0,
) -> None:
    """把设备 waypoint 连到最近 nav 节点（双向 lane）。"""
    if device_idx < 0 or device_idx >= len(level.vertices):
        return
    dev = level.vertices[device_idx]
    best_nav_id: Optional[int] = None
    best_dist = float("inf")
    for node in nodes:
        nid = int(node["id"])
        nav_idx = nav_indices.get(nid)
        if nav_idx is None:
            continue
        nav = level.vertices[nav_idx]
        dist = math.hypot(dev.x_m - nav.x_m, dev.y_m - nav.y_m)
        if dist < best_dist:
            best_dist = dist
            best_nav_id = nav_idx
    if best_nav_id is None or best_dist > max_dist_m:
        return
    level.lanes.append(
        RmfLaneIR(v1=device_idx, v2=best_nav_id, bidirectional=True, speed_limit=0.5, graph_idx=0)
    )


def build_layout_optimizer_rmf_ir(
    artifacts: LayoutOptimizerArtifacts,
    *,
    lab_uuid: str = "",
    scene_hash: str = "",
    building_name: str = "building",
    default_level: str = "L1",
    include_coarse_nav: bool = True,
    snap_devices_to_nav: bool = True,
    nav_speed_limit: float = 0.5,
) -> RmfMapIR:
    """placements + aisle_network → RmfMapIR（米制 cartesian_meters）。"""
    ir = RmfMapIR(
        lab_uuid=lab_uuid,
        scene_hash=scene_hash,
        building_name=building_name,
        coordinate_system="cartesian_meters",
    )
    level = RmfLevelIR(name=default_level, elevation=0.0)
    ir.levels.append(level)

    waypoint_map = build_instance_waypoint_map(artifacts.placements)
    placement_by_id = {str(p["instance_id"]): p for p in artifacts.placements if p.get("instance_id")}
    waypoint_to_instance: Dict[str, str] = {v: k for k, v in waypoint_map.items()}
    device_indices: Dict[str, int] = {}

    # 1) 设备接驳 waypoints
    for iid, wp_name in sorted(waypoint_map.items()):
        placement = placement_by_id.get(iid, {})
        x, y = resolve_device_xy(placement) if placement else (0.0, 0.0)
        pickup = f"d_{wp_name}"
        dropoff = f"i_{wp_name}"
        idx = level.add_vertex(
            RmfVertexIR(
                name=wp_name,
                x_m=x,
                y_m=y,
                z_m=0.0,
                params={
                    "is_holding_point": True,
                    "pickup_dispenser": pickup,
                    "dropoff_ingestor": dropoff,
                },
            )
        )
        device_indices[iid] = idx

    # 2) coarse nav 网格 → vertices + lanes
    nav_node_id_to_idx: Dict[int, int] = {}
    if include_coarse_nav:
        nodes, edges = _coarse_graph(artifacts)
        for node in nodes:
            nid = int(node["id"])
            nav_name = f"nav_{nid}"
            nav_idx = level.add_vertex(
                RmfVertexIR(
                    name=nav_name,
                    x_m=float(node["x"]),
                    y_m=float(node["y"]),
                    z_m=0.0,
                    params={},
                )
            )
            nav_node_id_to_idx[nid] = nav_idx

        seen_edges: set[tuple[int, int]] = set()
        for edge in edges:
            if len(edge) != 2:
                continue
            a, b = int(edge[0]), int(edge[1])
            ia, ib = nav_node_id_to_idx.get(a), nav_node_id_to_idx.get(b)
            if ia is None or ib is None:
                continue
            key = (min(ia, ib), max(ia, ib))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            level.lanes.append(
                RmfLaneIR(v1=ia, v2=ib, bidirectional=True, speed_limit=nav_speed_limit, graph_idx=0)
            )

        if snap_devices_to_nav and nav_node_id_to_idx:
            nodes_list, _ = _coarse_graph(artifacts)
            for iid, dev_idx in device_indices.items():
                _snap_device_to_nav(level, dev_idx, nav_node_id_to_idx, nodes_list)

    missing_dock = [iid for iid in waypoint_map if iid not in placement_by_id]
    for iid in missing_dock:
        ir.diagnostics.append(
            RmfDiagnostic("warning", "missing_placement", f"transfers 引用但 placements 缺失: {iid}", iid)
        )

    ir._waypoint_device_uuid = {}  # type: ignore[attr-defined]
    ir._restricted_zones = []  # type: ignore[attr-defined]
    ir._waypoint_to_instance = waypoint_to_instance  # type: ignore[attr-defined]
    ir._layout_optimizer_dir = str(artifacts.directory)  # type: ignore[attr-defined]
    return ir


def merge_transfer_plan_into_semantic(
    semantic: Dict[str, Any],
    transfer_plan: Dict[str, Any],
    artifacts: LayoutOptimizerArtifacts,
) -> Dict[str, Any]:
    """把 transfer plan 引用写入 semantic_map（#18 §9.6）。"""
    waypoint_to_instance = {wp["waypointName"]: wp["instanceId"] for wp in transfer_plan.get("deviceWaypoints") or []}
    semantic = dict(semantic)
    semantic["waypoint_to_instance"] = waypoint_to_instance
    meta = transfer_plan.get("meta") or {}
    semantic["transfer_plan_ref"] = {
        "source": "layout_optimizer",
        "directory": str(artifacts.directory),
        "source_scene": meta.get("sourceScene"),
        "transfer_count": meta.get("transferCount"),
        "makespan_min": meta.get("makespanMin"),
    }
    return semantic
