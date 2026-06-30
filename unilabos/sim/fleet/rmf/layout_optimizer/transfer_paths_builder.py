"""transfers.json → RmfTransferPaths（带完整运动轨迹的转运路径，#18 §9.9 / #21 §4）。

由 `transfers.json`（5402 条任务 + ready/deadline/task_id）驱动，逐条配上其运动轨迹
（`navSequence` + `geometryM` 直角折线，取自可编辑的 path 目录 `rmf_nav_path_catalog.json`），
并补 nav 接驳点端点 + 设备工位 handler（`endpointMode="nav"`）。产物落盘 `rmf_transfer_paths.json`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from unilabos.sim.fleet.rmf.layout_optimizer.grid_router import snap_polyline_to_nav
from unilabos.sim.fleet.rmf.layout_optimizer.slug import build_instance_waypoint_map


def _star_name(layer: int, side: str) -> str:
    return f"star_l{layer}_{side}"


def _build_turn_index(
    dock_and_turn: Dict[str, Any],
    nav_nodes: List[Dict[str, Any]],
) -> tuple[Dict[str, int], Dict[int, Dict[str, Dict[str, Any]]]]:
    """从 dock_and_turn.json 构建设备层号与左右星点映射。"""
    nav_by_name = {str(n.get("name") or ""): n for n in nav_nodes if n.get("name")}

    instance_layer: Dict[str, int] = {}
    for dock in dock_and_turn.get("docks") or []:
        iid = str(dock.get("instance_id") or "")
        if not iid:
            continue
        try:
            instance_layer[iid] = int(dock.get("layer"))
        except Exception:  # noqa: BLE001
            continue

    by_layer_points: Dict[int, List[tuple[float, float]]] = {}
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

        left_name = _star_name(layer, "left")
        right_name = _star_name(layer, "right")
        left_nav = left_name if left_name in nav_by_name else None
        right_nav = right_name if right_name in nav_by_name else None

        stars[layer] = {
            "left": {"name": left_name, "x": left[0], "y": left[1], "nav": left_nav},
            "right": {"name": right_name, "x": right[0], "y": right[1], "nav": right_nav},
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


def _dedup_seq(seq: List[Optional[str]]) -> List[str]:
    out: List[str] = []
    for name in seq:
        if not name:
            continue
        if not out or out[-1] != name:
            out.append(name)
    return out


def _waypoint_map_from_catalog(catalog: Dict[str, Any]) -> Dict[str, str]:
    placements = [
        {"instance_id": o.get("instanceId"), "device_type": o.get("deviceType")}
        for o in (catalog.get("placementsOverlay") or [])
        if o.get("instanceId")
    ]
    return build_instance_waypoint_map(placements)


def build_transfer_paths(
    transfers_doc: Dict[str, Any],
    catalog: Dict[str, Any],
    *,
    router: Any = None,
    source_scene: Optional[str] = None,
) -> Dict[str, Any]:
    """transfers.json（doc）→ RmfTransferPaths dict（#18 §9.9）。

    轨迹来源：
    - `router`（FineGridRouter）：在 fine 网格上现算每对设备的直角折线（**推荐**，不依赖
      可能被 path_studio 编辑/损坏的 `catalog.paths`）；
    - 否则回退到 `catalog.paths`（按 transferBindings 索引 1:1 对齐）。
    catalog 仅提供静态参考：navGraph（吸附 navSequence）、deviceDockMap（dockNav 端点）、
    placementsOverlay（slug→wp handler）。
    """
    meta_in = catalog.get("meta") or {}
    t_meta = transfers_doc.get("meta") or {}
    wp_map = _waypoint_map_from_catalog(catalog)
    dock_by_inst = {str(d.get("instanceId")): d for d in (catalog.get("deviceDockMap") or [])}
    nav_nodes = (catalog.get("navGraph") or {}).get("nodes") or []

    path_by_id = {str(p.get("pathId")): p for p in (catalog.get("paths") or [])}
    bindings = catalog.get("transferBindings") or []
    path_by_pair: Dict[tuple, Dict[str, Any]] = {}
    for path in catalog.get("paths") or []:
        path_by_pair.setdefault((str(path.get("fromInstance")), str(path.get("toInstance"))), path)

    dock_xy_by_inst: Dict[str, tuple[float, float]] = {}
    for dock in catalog.get("deviceDockMap") or []:
        iid = str(dock.get("instanceId") or "")
        if not iid:
            continue
        try:
            dock_xy_by_inst[iid] = (float(dock.get("dockX")), float(dock.get("dockY")))
        except Exception:  # noqa: BLE001
            continue

    dock_and_turn: Dict[str, Any] = {}
    if router is not None:
        layout_dir = getattr(router, "layout_dir", "")
        if layout_dir:
            turn_path = Path(str(layout_dir)) / "dock_and_turn.json"
            if turn_path.is_file():
                try:
                    dock_and_turn = json.loads(turn_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    dock_and_turn = {}
    instance_layer, stars_by_layer = _build_turn_index(dock_and_turn, nav_nodes)

    # router 现算的轨迹按设备对缓存
    route_cache: Dict[tuple, Optional[Dict[str, Any]]] = {}

    def _route_pair(
        from_iid: str,
        to_iid: str,
        via_points: Optional[List[tuple[float, float]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if router is None:
            return None
        key = (from_iid, to_iid, tuple(via_points or []))
        if key in route_cache:
            return route_cache[key]
        result: Optional[Dict[str, Any]] = None
        if via_points:
            res = router.route_via(from_iid, to_iid, via_points)
        else:
            res = router.route(from_iid, to_iid)
        if res and res.get("geometryM"):
            geom = res["geometryM"]
            from_nav = (dock_by_inst.get(from_iid) or {}).get("dockNav")
            to_nav = (dock_by_inst.get(to_iid) or {}).get("dockNav")
            nav_seq = snap_polyline_to_nav(geom, nav_nodes, from_nav, to_nav)
            result = {
                "fromNav": from_nav or (nav_seq[0] if nav_seq else None),
                "toNav": to_nav or (nav_seq[-1] if nav_seq else None),
                "navSequence": nav_seq,
                "geometryM": geom,
                "pathId": None,
            }
        route_cache[key] = result
        return result

    device_waypoints: List[Dict[str, Any]] = []
    for overlay in catalog.get("placementsOverlay") or []:
        iid = str(overlay.get("instanceId") or "")
        if not iid:
            continue
        wp = wp_map.get(iid, "")
        dock = dock_by_inst.get(iid, {})
        device_waypoints.append(
            {
                "instanceId": iid,
                "waypointName": wp,
                "dockNav": dock.get("dockNav"),
                "x": dock.get("dockX"),
                "y": dock.get("dockY"),
                "level": "L1",
                "isHoldingPoint": True,
                "pickupDispenser": f"d_{wp}" if wp else "",
                "dropoffIngestor": f"i_{wp}" if wp else "",
            }
        )

    transfers_out: List[Dict[str, Any]] = []
    missing = 0
    star_routed = 0
    rows = transfers_doc.get("transfers") or []
    for i, row in enumerate(rows):
        from_iid = str(row.get("from_device") or "")
        to_iid = str(row.get("to_device") or "")
        tid = str(row.get("sample_id") or f"{from_iid}->{to_iid}")
        from_wp = wp_map.get(from_iid, "")
        to_wp = wp_map.get(to_iid, "")
        from_layer = instance_layer.get(from_iid)
        to_layer = instance_layer.get(to_iid)
        routing_policy = "direct_same_layer_or_no_star"
        via_transfer_points: List[str] = []

        via_points: List[tuple[float, float]] = []
        explicit_nav_seq: List[str] = []
        if (
            from_layer is not None
            and to_layer is not None
            and from_layer != to_layer
            and from_layer in stars_by_layer
            and to_layer in stars_by_layer
        ):
            src_xy = dock_xy_by_inst.get(from_iid)
            if src_xy is None and router is not None:
                dock_xy = router.dock_xy(from_iid)
                if dock_xy is not None:
                    src_xy = (float(dock_xy[0]), float(dock_xy[1]))
            if src_xy is not None:
                side = _pick_nearest_side(src_xy[0], stars_by_layer[from_layer])
                star_from = stars_by_layer[from_layer].get(side)
                star_to = stars_by_layer[to_layer].get(side)
                if star_from and star_to:
                    via_points = [
                        (float(star_from["x"]), float(star_from["y"])),
                        (float(star_to["x"]), float(star_to["y"])),
                    ]
                    via_transfer_points = [str(star_from["name"]), str(star_to["name"])]
                    if star_from.get("nav") and star_to.get("nav"):
                        explicit_nav_seq = _dedup_seq(
                            [
                                (dock_by_inst.get(from_iid) or {}).get("dockNav"),
                                star_from.get("nav"),
                                star_to.get("nav"),
                                (dock_by_inst.get(to_iid) or {}).get("dockNav"),
                            ]
                        )
                    routing_policy = "via_star_chain_A_Y_Yprime_B"

        traj = _route_pair(from_iid, to_iid, via_points if via_points else None)  # router 优先（现算，正确）
        if traj is None:
            # 回退到 catalog.paths（按 transferBindings 索引 1:1）
            binding = bindings[i] if i < len(bindings) else None
            if binding is not None and str(binding.get("transferId")) != tid:
                binding = None
            path = path_by_id.get(str(binding.get("pathId"))) if binding else None
            if path is None:
                path = path_by_pair.get((from_iid, to_iid))
            if path:
                traj = {
                    "fromNav": path.get("fromNav"),
                    "toNav": path.get("toNav"),
                    "navSequence": list(path.get("navSequence") or []),
                    "geometryM": list(path.get("geometryM") or []),
                    "pathId": path.get("pathId"),
                }
        if traj:
            from_nav = traj["fromNav"]
            to_nav = traj["toNav"]
            nav_seq = explicit_nav_seq if explicit_nav_seq else traj["navSequence"]
            geom = traj["geometryM"]
            path_id = traj["pathId"]
            if routing_policy == "via_star_chain_A_Y_Yprime_B":
                star_routed += 1
        else:
            from_nav = (dock_by_inst.get(from_iid) or {}).get("dockNav")
            to_nav = (dock_by_inst.get(to_iid) or {}).get("dockNav")
            nav_seq = [n for n in (from_nav, to_nav) if n]
            geom = []
            path_id = None
            missing += 1
        transfers_out.append(
            {
                "transferId": tid,
                "fromInstance": from_iid,
                "toInstance": to_iid,
                "fromWaypoint": from_nav,
                "toWaypoint": to_nav,
                "pickupHandler": f"d_{from_wp}" if from_wp else "",
                "dropoffHandler": f"i_{to_wp}" if to_wp else "",
                "pathId": path_id,
                "navSequence": nav_seq,
                "geometryM": geom,
                "readyTimeMin": int(row.get("ready_time") or 0),
                "deadlineMin": int(row.get("deadline") or 0),
                "taskId": str(row.get("task_id") or ""),
                "priority": str(row.get("priority") or "normal"),
                "payload": [{"sku": tid, "quantity": 1}] if tid else [],
                "routingPolicy": routing_policy,
                "viaTransferPoints": via_transfer_points,
            }
        )

    return {
        "meta": {
            "source": "transfers.json",
            "sourceScene": source_scene or meta_in.get("sourceScene") or t_meta.get("source_scene"),
            "scale": meta_in.get("scale") or t_meta.get("scale"),
            "transferCount": len(transfers_out),
            "makespanMin": int(t_meta.get("makespan_min") or 0),
            "coordinateFrame": meta_in.get("coordinateFrame", "lab_local_m"),
            "labOrigin": meta_in.get("labOrigin"),
            "timeUnit": "min",
            "endpointMode": "nav",
            "pathsResolved": len(transfers_out) - missing,
            "pathsMissing": missing,
            "starRoutingApplied": star_routed,
            "starRoutingCandidateLayers": len(stars_by_layer),
        },
        "deviceWaypoints": device_waypoints,
        "transfers": transfers_out,
    }
