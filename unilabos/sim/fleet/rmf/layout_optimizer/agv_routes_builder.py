"""designer 数据 → `RmfAgvRoutes`（黑点 waypoints + 轨迹 + 设备，#18 §10.6 / #21 §0.2/§4）。

读 layout-optimizer 产物（placements + flow_matrix + lab），产出**前端可直接读**的：
- `waypoints`：黑点（设备接驳点，= `agv_trajectory.png` 的黑点；唯一可选导航/目标点）。
- `routes`：AGV 轨迹（黑点→黑点，含米级直角折线 `geometryM` + 流量 `weight`）。
- `devices`：设备位置/朝向（`layout.png`，仅显示、不可选）。

黑点坐标取自 `FineGridRouter.dock_xy`（真实 dock cell，与 agv_trajectory.png 一致）；无 router 时退化用 placements.center。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from unilabos.sim.fleet.rmf.layout_optimizer.dock_resolver import resolve_device_xy
from unilabos.sim.fleet.rmf.layout_optimizer.ingest import LayoutOptimizerArtifacts
from unilabos.sim.fleet.rmf.layout_optimizer.slug import build_instance_waypoint_map


def _dock_name(wp_name: str, instance_id: str) -> str:
    """wp_<slug> → dock_<slug>（黑点名）。"""
    if wp_name.startswith("wp_"):
        return "dock_" + wp_name[3:]
    return "dock_" + instance_id


def _star_name(layer: int, side: str) -> str:
    return f"star_l{layer}_{side}"


def _build_turn_index(dock_and_turn: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[int, Dict[str, Dict[str, Any]]]]:
    """从 dock_and_turn.json 构建设备层号与左右星点索引。"""
    instance_layer: Dict[str, int] = {}
    for dock in dock_and_turn.get("docks") or []:
        iid = str(dock.get("instance_id") or "")
        if not iid:
            continue
        try:
            instance_layer[iid] = int(dock.get("layer"))
        except Exception:  # noqa: BLE001
            continue

    by_layer_points: Dict[int, List[Tuple[float, float]]] = {}
    for turning in dock_and_turn.get("turning_points") or []:
        point = turning.get("point") or []
        if not isinstance(point, list) or len(point) < 2:
            continue
        try:
            layer = int(turning.get("layer"))
            x = float(point[0])
            y = float(point[1])
        except Exception:  # noqa: BLE001
            continue
        by_layer_points.setdefault(layer, []).append((x, y))

    stars: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for layer, points in by_layer_points.items():
        if not points:
            continue
        points_sorted = sorted(points, key=lambda xy: xy[0])
        left = points_sorted[0]
        right = points_sorted[-1]
        stars[layer] = {
            "left": {"name": _star_name(layer, "left"), "x": left[0], "y": left[1]},
            "right": {"name": _star_name(layer, "right"), "x": right[0], "y": right[1]},
        }
    return instance_layer, stars


def _pick_nearest_side(x: float, layer_stars: Dict[str, Dict[str, Any]]) -> str:
    left = layer_stars.get("left")
    right = layer_stars.get("right")
    if not left:
        return "right"
    if not right:
        return "left"
    return "left" if abs(float(left["x"]) - x) <= abs(float(right["x"]) - x) else "right"


def _dedup_waypoint_seq(seq: List[str]) -> List[str]:
    out: List[str] = []
    for name in seq:
        if not name:
            continue
        if not out or out[-1] != name:
            out.append(name)
    return out


def _normalize_geometry(points: List[Tuple[float, float]]) -> List[List[float]]:
    out: List[List[float]] = []
    for x, y in points:
        px = round(float(x), 3)
        py = round(float(y), 3)
        if out and out[-1] == [px, py]:
            continue
        out.append([px, py])
    return out


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
    instance_layer, stars_by_layer = _build_turn_index(artifacts.dock_and_turn or {})

    # 1) 黑点 waypoints（设备接驳点）
    dock_name: Dict[str, str] = {}
    dock_xy: Dict[str, Tuple[float, float]] = {}
    waypoints: List[Dict[str, Any]] = []
    for iid in sorted(wp_map.keys()):
        wp = wp_map[iid]
        name = _dock_name(wp, iid)
        xy: Optional[Tuple[float, float]] = None
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
        dock_xy[iid] = (float(xy[0]), float(xy[1]))

    # 1.1) 两侧星形转运点（来自 dock_and_turn.turning_points）
    for layer in sorted(stars_by_layer.keys()):
        layer_stars = stars_by_layer[layer]
        for side in ("left", "right"):
            star = layer_stars.get(side)
            if not star:
                continue
            waypoints.append(
                {
                    "name": star["name"],
                    "x": round(float(star["x"]), 3),
                    "y": round(float(star["y"]), 3),
                    "level": level,
                    "kind": "turn_star",
                    "isTransferPoint": True,
                    "layer": layer,
                    "side": side,
                }
            )

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
        from_xy = dock_xy.get(a, (0.0, 0.0))
        to_xy = dock_xy.get(b, (0.0, 0.0))
        from_layer = instance_layer.get(a)
        to_layer = instance_layer.get(b)

        via_points: List[Tuple[float, float]] = []
        via_waypoints: List[str] = []
        routing_policy = "direct_same_layer_or_no_star"
        if (
            from_layer is not None
            and to_layer is not None
            and from_layer != to_layer
            and from_layer in stars_by_layer
            and to_layer in stars_by_layer
        ):
            side = _pick_nearest_side(from_xy[0], stars_by_layer[from_layer])
            star_from = stars_by_layer[from_layer].get(side)
            star_to = stars_by_layer[to_layer].get(side)
            if star_from and star_to:
                via_points = [
                    (float(star_from["x"]), float(star_from["y"])),
                    (float(star_to["x"]), float(star_to["y"])),
                ]
                via_waypoints = [str(star_from["name"]), str(star_to["name"])]
                routing_policy = "via_star_chain_A_Y_Yprime_B"

        geom: List[List[float]] = []
        if router is not None:
            key = (a, b, tuple(via_waypoints))
            if key in route_cache:
                geom = route_cache[key]
            else:
                if via_points:
                    res = router.route_via(a, b, via_points)
                else:
                    res = router.route(a, b)
                geom = res.get("geometryM") if res else []
                route_cache[key] = geom
        else:
            if via_points:
                geom = _normalize_geometry([from_xy, via_points[0], via_points[1], to_xy])
            else:
                geom = _normalize_geometry([from_xy, to_xy])

        waypoint_seq = _dedup_waypoint_seq([from_wp, *via_waypoints, to_wp])
        idx += 1
        routes.append(
            {
                "routeId": f"r_{idx:04d}",
                "fromWaypoint": from_wp,
                "toWaypoint": to_wp,
                "waypointSeq": waypoint_seq,
                "geometryM": geom,
                "weight": int(edge.get("weight") or 0),
                "routingPolicy": routing_policy,
                "viaTransferPoints": via_waypoints,
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
            "starWaypointCount": sum(1 for w in waypoints if w.get("kind") == "turn_star"),
            "layerCount": len(stars_by_layer),
            "routeCount": len(routes),
        },
        "waypoints": waypoints,
        "routes": routes,
        "devices": devices,
    }
