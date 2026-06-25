"""transfers.json → RmfTransferPlan（#18 §9.2 / #21 §4）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from unilabos.sim.fleet.rmf.layout_optimizer.dock_resolver import resolve_device_xy
from unilabos.sim.fleet.rmf.layout_optimizer.ingest import LayoutOptimizerArtifacts
from unilabos.sim.fleet.rmf.layout_optimizer.slug import build_instance_waypoint_map


def _handler_names(waypoint_name: str) -> tuple[str, str]:
    return f"d_{waypoint_name}", f"i_{waypoint_name}"


def build_transfer_plan(
    artifacts: LayoutOptimizerArtifacts,
    *,
    level: str = "L1",
    coordinate_frame: str = "lab_local_m",
    source_scene: Optional[str] = None,
) -> Dict[str, Any]:
    """把 layout-optimizer 产物编译为 RmfTransferPlan dict（#18 §9.2）。"""
    waypoint_map = build_instance_waypoint_map(artifacts.placements)
    placement_by_id = {str(p["instance_id"]): p for p in artifacts.placements if p.get("instance_id")}

    device_waypoints: List[Dict[str, Any]] = []
    for iid, wp_name in sorted(waypoint_map.items()):
        placement = placement_by_id.get(iid, {})
        x, y = resolve_device_xy(placement) if placement else (0.0, 0.0)
        pickup, dropoff = _handler_names(wp_name)
        device_waypoints.append(
            {
                "instanceId": iid,
                "waypointName": wp_name,
                "x": round(x, 4),
                "y": round(y, 4),
                "level": level,
                "isHoldingPoint": True,
                "pickupDispenser": pickup,
                "dropoffIngestor": dropoff,
            }
        )

    transfers_out: List[Dict[str, Any]] = []
    for row in artifacts.transfers:
        from_iid = str(row.get("from_device") or "")
        to_iid = str(row.get("to_device") or "")
        from_wp = waypoint_map.get(from_iid)
        to_wp = waypoint_map.get(to_iid)
        if not from_wp or not to_wp:
            continue
        sample_id = str(row.get("sample_id") or "")
        transfers_out.append(
            {
                "transferId": sample_id or f"{from_iid}->{to_iid}",
                "fromInstance": from_iid,
                "toInstance": to_iid,
                "fromWaypoint": from_wp,
                "toWaypoint": to_wp,
                "readyTimeMin": int(row.get("ready_time") or 0),
                "deadlineMin": int(row.get("deadline") or 0),
                "taskId": str(row.get("task_id") or ""),
                "priority": str(row.get("priority") or "normal"),
                "payload": [{"sku": sample_id, "quantity": 1}] if sample_id else [],
            }
        )

    meta = artifacts.transfers_meta
    origin = artifacts.lab_origin
    return {
        "meta": {
            "source": "layout_optimizer",
            "sourceScene": source_scene or artifacts.source_scene,
            "scale": meta.get("scale"),
            "transferCount": len(transfers_out),
            "makespanMin": int(meta.get("makespan_min") or 0),
            "coordinateFrame": coordinate_frame,
            "labOrigin": [origin[0], origin[1]],
            "timeUnit": "min",
        },
        "deviceWaypoints": device_waypoints,
        "transfers": transfers_out,
    }
