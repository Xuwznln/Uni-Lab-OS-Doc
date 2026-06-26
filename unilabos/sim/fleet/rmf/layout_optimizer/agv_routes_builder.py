"""designer 数据 → `RmfAgvRoutes`（黑点 waypoints + 轨迹 + 设备，#18 §10.6 / #21 §0.2/§4）。

读 layout-optimizer 产物（placements + flow_matrix + lab），产出**前端可直接读**的：
- `waypoints`：黑点（设备接驳点，= `agv_trajectory.png` 的黑点；唯一可选导航/目标点）。
- `routes`：AGV 轨迹（黑点→黑点，含米级直角折线 `geometryM` + 流量 `weight`）。
- `devices`：设备位置/朝向（`layout.png`，仅显示、不可选）。

黑点坐标取自 `FineGridRouter.dock_xy`（真实 dock cell，与 agv_trajectory.png 一致）；无 router 时退化用 placements.center。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from unilabos.sim.fleet.rmf.layout_optimizer.dock_resolver import resolve_device_xy
from unilabos.sim.fleet.rmf.layout_optimizer.ingest import LayoutOptimizerArtifacts
from unilabos.sim.fleet.rmf.layout_optimizer.slug import build_instance_waypoint_map


def _dock_name(wp_name: str, instance_id: str) -> str:
    """wp_<slug> → dock_<slug>（黑点名）。"""
    if wp_name.startswith("wp_"):
        return "dock_" + wp_name[3:]
    return "dock_" + instance_id


def build_agv_routes(
    artifacts: LayoutOptimizerArtifacts,
    *,
    router: Any = None,
    level: str = "L1",
    source_scene: Optional[str] = None,
) -> Dict[str, Any]:
    """layout-optimizer 产物 → RmfAgvRoutes dict（#18 §10.6）。"""
    placements = artifacts.placements
    flow = artifacts.flow_matrix or {}
    instances = {str(i.get("instance_id")): i for i in (flow.get("instances") or [])}
    wp_map = build_instance_waypoint_map(placements)
    placement_by_id = {str(p.get("instance_id")): p for p in placements if p.get("instance_id")}

    # 1) 黑点 waypoints（设备接驳点）
    dock_name: Dict[str, str] = {}
    waypoints: List[Dict[str, Any]] = []
    for iid in sorted(wp_map.keys()):
        wp = wp_map[iid]
        name = _dock_name(wp, iid)
        xy: Optional[tuple] = None
        if router is not None:
            try:
                xy = router.dock_xy(iid)
            except Exception:  # noqa: BLE001
                xy = None
        if xy is None:
            placement = placement_by_id.get(iid, {})
            xy = resolve_device_xy(placement) if placement else (0.0, 0.0)
        waypoints.append(
            {
                "name": name,
                "x": round(float(xy[0]), 3),
                "y": round(float(xy[1]), 3),
                "level": level,
                "kind": "device_dock",
                "instanceId": iid,
                "pickupDispenser": f"d_{name}",
                "dropoffIngestor": f"i_{name}",
            }
        )
        dock_name[iid] = name

    # 2) routes（AGV 轨迹，黑点→黑点）
    routes: List[Dict[str, Any]] = []
    route_cache: Dict[tuple, List[List[float]]] = {}
    idx = 0
    for edge in flow.get("flow_edges") or []:
        a = str(edge.get("from_instance") or "")
        b = str(edge.get("to_instance") or "")
        if a not in dock_name or b not in dock_name:
            continue
        from_wp = dock_name[a]
        to_wp = dock_name[b]
        geom: List[List[float]] = []
        if router is not None:
            key = (a, b)
            if key in route_cache:
                geom = route_cache[key]
            else:
                res = router.route(a, b)
                geom = res.get("geometryM") if res else []
                route_cache[key] = geom
        idx += 1
        routes.append(
            {
                "routeId": f"r_{idx:04d}",
                "fromWaypoint": from_wp,
                "toWaypoint": to_wp,
                "waypointSeq": [from_wp, to_wp],
                "geometryM": geom,
                "weight": int(edge.get("weight") or 0),
            }
        )

    # 3) devices（仅显示）
    devices: List[Dict[str, Any]] = []
    for placement in placements:
        iid = str(placement.get("instance_id") or "")
        if not iid:
            continue
        inst = instances.get(iid, {})
        devices.append(
            {
                "instanceId": iid,
                "center": placement.get("center"),
                "footprintKey": placement.get("footprint_key") or inst.get("footprint_key"),
                "rotationDeg": int(placement.get("rotation_deg") or 0),
                "deviceType": placement.get("device_type") or inst.get("device_type"),
                "bbox": placement.get("bbox") or inst.get("bbox"),
            }
        )

    origin = artifacts.lab_origin
    return {
        "meta": {
            "source": "layout_optimizer",
            "sourceScene": source_scene or artifacts.source_scene,
            "coordinateFrame": "lab_local_m",
            "labOrigin": [origin[0], origin[1]],
            "waypointCount": len(waypoints),
            "routeCount": len(routes),
        },
        "waypoints": waypoints,
        "routes": routes,
        "devices": devices,
    }
