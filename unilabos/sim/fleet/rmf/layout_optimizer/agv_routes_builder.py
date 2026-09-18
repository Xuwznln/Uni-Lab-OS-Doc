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


def _infer_layer_from_y(y: float, stars_by_layer: Dict[int, Dict[str, Dict[str, Any]]]) -> Optional[int]:
    """按 y 最近原则给点位推断层号（用于 charge_docks 未显式给 layer 的情况）。"""
    best: Optional[Tuple[float, int]] = None
    for layer, stars in stars_by_layer.items():
        left = stars.get("left")
        right = stars.get("right")
        if not left and not right:
            continue
        ys: List[float] = []
        if left:
            ys.append(float(left["y"]))
        if right:
            ys.append(float(right["y"]))
        if not ys:
            continue
        y_ref = sum(ys) / len(ys)
        dist = abs(float(y) - y_ref)
        cand = (dist, int(layer))
        if best is None or cand < best:
            best = cand
    return best[1] if best is not None else None


def _plan_route_via_points(
    *,
    from_name: str,
    to_name: str,
    from_xy: Tuple[float, float],
    to_xy: Tuple[float, float],
    waypoint_layer: Dict[str, int],
    waypoint_side: Dict[str, str],
    waypoint_kind: Dict[str, str],
    stars_by_layer: Dict[int, Dict[str, Dict[str, Any]]],
) -> Tuple[List[Tuple[float, float]], List[str], str]:
    """按 A-Y-Y'-B 原则规划中转星点。"""
    via_points: List[Tuple[float, float]] = []
    via_waypoints: List[str] = []
    from_layer = waypoint_layer.get(from_name)
    to_layer = waypoint_layer.get(to_name)
    if (
        from_layer is not None
        and to_layer is not None
        and from_layer != to_layer
        and from_layer in stars_by_layer
        and to_layer in stars_by_layer
    ):
        side = ""
        # 充电点优先使用其自身侧（避免被起点位置牵引到反侧，导致异常折线）。
        if waypoint_kind.get(to_name) == "charge_dock":
            side = str(waypoint_side.get(to_name) or "")
        elif waypoint_kind.get(from_name) == "charge_dock":
            side = str(waypoint_side.get(from_name) or "")
        if side not in ("left", "right"):
            side = _pick_nearest_side(from_xy[0], stars_by_layer[from_layer])
        star_from = stars_by_layer[from_layer].get(side)
        star_to = stars_by_layer[to_layer].get(side)
        if star_from and star_to:
            via_points = [
                (float(star_from["x"]), float(star_from["y"])),
                (float(star_to["x"]), float(star_to["y"])),
            ]
            via_waypoints = [str(star_from["name"]), str(star_to["name"])]
            return via_points, via_waypoints, "via_star_chain_A_Y_Yprime_B"
    return via_points, via_waypoints, "direct_same_layer_or_no_star"


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

    # 1) 黑点 waypoints（设备接驳点 + 星点 + 充电点）
    dock_name: Dict[str, str] = {}  # instance_id -> dock_*
    dock_xy: Dict[str, Tuple[float, float]] = {}  # instance_id -> (x,y)
    waypoints: List[Dict[str, Any]] = []
    waypoint_xy: Dict[str, Tuple[float, float]] = {}  # waypoint 名 -> (x,y)
    waypoint_layer: Dict[str, int] = {}  # waypoint 名 -> layer
    waypoint_side: Dict[str, str] = {}  # waypoint 名 -> left/right（若可推断）
    waypoint_kind: Dict[str, str] = {}  # waypoint 名 -> kind
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
        waypoint_xy[name] = (float(xy[0]), float(xy[1]))
        waypoint_kind[name] = "device_dock"
        if iid in instance_layer:
            waypoint_layer[name] = int(instance_layer[iid])

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
            nm = str(star["name"])
            waypoint_xy[nm] = (float(star["x"]), float(star["y"]))
            waypoint_kind[nm] = "turn_star"
            waypoint_layer[nm] = int(layer)
            waypoint_side[nm] = side

    # 1.2) 充电点（来自 dock_and_turn.charge_docks；名称统一为 dock_charge_<n>）
    charge_points = (artifacts.dock_and_turn or {}).get("charge_docks") or []
    for i, c in enumerate(charge_points, start=1):
        point = c.get("point") if isinstance(c, dict) else None
        if not (isinstance(point, list) and len(point) >= 2):
            continue
        x, y = float(point[0]), float(point[1])
        raw_name = str((c.get("name") if isinstance(c, dict) else "") or f"dock_charge_{i}")
        name = raw_name if raw_name.startswith("dock_") else f"dock_{raw_name}"
        base_name = name
        suffix = 1
        while name in waypoint_xy:
            suffix += 1
            name = f"{base_name}_{suffix}"
        layer_raw = c.get("layer") if isinstance(c, dict) else None
        layer_val: Optional[int] = None
        if layer_raw is not None and str(layer_raw).strip() != "":
            try:
                layer_val = int(layer_raw)
            except Exception:  # noqa: BLE001
                layer_val = None
        if layer_val is None:
            layer_val = _infer_layer_from_y(y, stars_by_layer)
        side = str((c.get("side") if isinstance(c, dict) else "") or "").strip()
        if not side and layer_val is not None and layer_val in stars_by_layer:
            side = _pick_nearest_side(x, stars_by_layer[layer_val])
        rec: Dict[str, Any] = {
            "name": name,
            "x": round(x, 3),
            "y": round(y, 3),
            "level": level,
            "kind": "charge_dock",
            "isCharger": True,
        }
        if layer_val is not None:
            rec["layer"] = int(layer_val)
        if side:
            rec["side"] = side
        waypoints.append(rec)
        waypoint_xy[name] = (x, y)
        waypoint_kind[name] = "charge_dock"
        if layer_val is not None:
            waypoint_layer[name] = int(layer_val)
        if side in ("left", "right"):
            waypoint_side[name] = side

    # 2) routes（AGV 轨迹，黑点→黑点）
    routes: List[Dict[str, Any]] = []
    route_cache: Dict[tuple, List[List[float]]] = {}
    route_pairs: set[Tuple[str, str]] = set()
    idx = 0
    
    def _append_route(
        *,
        from_wp: str,
        to_wp: str,
        from_xy: Tuple[float, float],
        to_xy: Tuple[float, float],
        weight: int,
        from_instance: Optional[str] = None,
        to_instance: Optional[str] = None,
        route_tag: str = "",
    ) -> None:
        nonlocal idx
        if not from_wp or not to_wp or from_wp == to_wp:
            return
        if (from_wp, to_wp) in route_pairs:
            return
        via_points, via_waypoints, base_policy = _plan_route_via_points(
            from_name=from_wp,
            to_name=to_wp,
            from_xy=from_xy,
            to_xy=to_xy,
            waypoint_layer=waypoint_layer,
            waypoint_side=waypoint_side,
            waypoint_kind=waypoint_kind,
            stars_by_layer=stars_by_layer,
        )
        routing_policy = f"{route_tag}:{base_policy}" if route_tag else base_policy
        geom: List[List[float]] = []
        if router is not None and from_instance and to_instance:
            key = (from_instance, to_instance, tuple(via_waypoints))
            if key in route_cache:
                geom = route_cache[key]
            else:
                if via_points:
                    res = router.route_via(from_instance, to_instance, via_points)
                else:
                    res = router.route(from_instance, to_instance)
                geom = res.get("geometryM") if res else []
                route_cache[key] = geom
        elif router is not None:
            checkpoints = [from_xy, *via_points, to_xy]
            merged: List[List[float]] = []
            ok = True
            for i in range(len(checkpoints) - 1):
                seg = router.route_between_points(checkpoints[i], checkpoints[i + 1])
                pts = list(seg.get("geometryM") or []) if seg else []
                if not pts:
                    ok = False
                    break
                if merged:
                    merged.extend(pts[1:])
                else:
                    merged.extend(pts)
            if ok:
                geom = merged
        if not geom:
            if via_points:
                geom = _normalize_geometry([from_xy, *via_points, to_xy])
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
                "weight": int(weight),
                "routingPolicy": routing_policy,
                "viaTransferPoints": via_waypoints,
            }
        )
        route_pairs.add((from_wp, to_wp))

    for edge in flow.get("flow_edges") or []:
        a = str(edge.get("from_instance") or "")
        b = str(edge.get("to_instance") or "")
        if a not in dock_name or b not in dock_name:
            continue
        from_wp = dock_name[a]
        to_wp = dock_name[b]
        _append_route(
            from_wp=from_wp,
            to_wp=to_wp,
            from_xy=waypoint_xy.get(from_wp, (0.0, 0.0)),
            to_xy=waypoint_xy.get(to_wp, (0.0, 0.0)),
            weight=int(edge.get("weight") or 0),
            from_instance=a,
            to_instance=b,
        )

    # 2.1) charge_docks 相关路线：设备 dock ↔ 充电点（显式 A-Y-Y'-B 轨迹）
    charge_waypoints = sorted([nm for nm, k in waypoint_kind.items() if k == "charge_dock"])
    device_waypoints = sorted([nm for nm, k in waypoint_kind.items() if k == "device_dock"])
    for cwp in charge_waypoints:
        cxy = waypoint_xy.get(cwp)
        if cxy is None:
            continue
        for dwp in device_waypoints:
            dxy = waypoint_xy.get(dwp)
            if dxy is None:
                continue
            _append_route(
                from_wp=dwp,
                to_wp=cwp,
                from_xy=dxy,
                to_xy=cxy,
                weight=0,
                route_tag="charge_return",
            )
            _append_route(
                from_wp=cwp,
                to_wp=dwp,
                from_xy=cxy,
                to_xy=dxy,
                weight=0,
                route_tag="charge_depart",
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
