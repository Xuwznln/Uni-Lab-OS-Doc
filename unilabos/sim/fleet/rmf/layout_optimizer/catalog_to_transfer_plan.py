"""RmfNavPathCatalog → RmfTransferPlan（path_studio 导出/保存依赖，#21 §7 P2 / #18 §9.2）。

输出与运行时 `rmf_transfer_plan.json` 一致：`endpointMode="nav"`，即 transfers 的
`fromWaypoint/toWaypoint` 用 nav 接驳点（path.fromNav/toNav），而 `pickupHandler/dropoffHandler`
用设备 `wp_<slug>` 的发放器/收纳器（d_/i_），AGV 到 nav 停靠点由相邻设备工位发放/收纳。
"""

from __future__ import annotations

from typing import Any, Dict, List

from unilabos.sim.fleet.rmf.layout_optimizer.slug import build_instance_waypoint_map


def _waypoint_map(catalog: Dict[str, Any]) -> Dict[str, str]:
    """placementsOverlay(instanceId/deviceType) → {instanceId: wp_<slug>}（与编译期一致）。"""
    placements = [
        {"instance_id": o.get("instanceId"), "device_type": o.get("deviceType")}
        for o in (catalog.get("placementsOverlay") or [])
        if o.get("instanceId")
    ]
    return build_instance_waypoint_map(placements)


def build_transfer_plan_from_catalog(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """catalog（navGraph + paths + transferBindings）→ RmfTransferPlan dict。"""
    meta_in = catalog.get("meta") or {}
    wp_map = _waypoint_map(catalog)
    dock_by_inst = {str(d.get("instanceId")): d for d in (catalog.get("deviceDockMap") or [])}
    path_by_id = {str(p.get("pathId")): p for p in (catalog.get("paths") or [])}

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

    transfers: List[Dict[str, Any]] = []
    for binding in catalog.get("transferBindings") or []:
        path = path_by_id.get(str(binding.get("pathId")))
        if not path:
            continue
        from_iid = str(path.get("fromInstance") or "")
        to_iid = str(path.get("toInstance") or "")
        from_wp = wp_map.get(from_iid, "")
        to_wp = wp_map.get(to_iid, "")
        tid = str(binding.get("transferId") or "")
        transfers.append(
            {
                "transferId": tid,
                "fromInstance": from_iid,
                "toInstance": to_iid,
                "fromWaypoint": path.get("fromNav"),
                "toWaypoint": path.get("toNav"),
                "pickupHandler": f"d_{from_wp}" if from_wp else "",
                "dropoffHandler": f"i_{to_wp}" if to_wp else "",
                "pathId": path.get("pathId"),
                "navSequence": list(path.get("navSequence") or []),
                "readyTimeMin": int(binding.get("readyTimeMin") or 0),
                "deadlineMin": int(binding.get("deadlineMin") or 0),
                "taskId": str(binding.get("taskId") or ""),
                "priority": str(binding.get("priority") or "normal"),
                "payload": [{"sku": tid, "quantity": 1}] if tid else [],
            }
        )

    return {
        "meta": {
            "source": "nav_path_catalog",
            "sourceScene": meta_in.get("sourceScene"),
            "scale": meta_in.get("scale"),
            "transferCount": len(transfers),
            "coordinateFrame": meta_in.get("coordinateFrame", "lab_local_m"),
            "labOrigin": meta_in.get("labOrigin"),
            "timeUnit": "min",
            "endpointMode": "nav",
        },
        "deviceWaypoints": device_waypoints,
        "transfers": transfers,
    }
