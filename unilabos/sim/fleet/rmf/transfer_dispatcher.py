"""RmfTransferPlan → RMF delivery 任务信封（#18 §9.5 / #21 §5）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from unilabos.sim.fleet.rmf.task_dispatcher import build_delivery_request


def transfer_to_delivery_envelope(
    transfer: Dict[str, Any],
    *,
    epoch_ms: int,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
    requester: str = "unilab",
) -> Dict[str, Any]:
    """单条 RmfTransferPlan.transfers[] → dispatch_task_request delivery 信封。"""
    ready_min = int(transfer.get("readyTimeMin") or 0)
    earliest_ms = epoch_ms + ready_min * 60_000
    pickup_wp = str(transfer.get("fromWaypoint") or "")
    dropoff_wp = str(transfer.get("toWaypoint") or "")
    pickup_handler = f"d_{pickup_wp}"
    dropoff_handler = f"i_{dropoff_wp}"
    payload = list(transfer.get("payload") or [])

    env = build_delivery_request(
        pickup_wp,
        pickup_handler,
        dropoff_wp,
        dropoff_handler,
        payload=payload,
        fleet=fleet,
        robot=robot,
        requester=requester,
    )
    req = env["request"]
    req["unix_millis_earliest_start_time"] = earliest_ms
    labels = list(req.get("labels") or [])
    task_id = str(transfer.get("taskId") or "")
    if task_id and task_id not in labels:
        labels.append(task_id)
    labels.append("transfer_plan")
    req["labels"] = labels
    return env


def build_delivery_envelopes(
    transfer_plan: Dict[str, Any],
    *,
    epoch_ms: Optional[int] = None,
    ready_min_from: Optional[int] = None,
    ready_min_to: Optional[int] = None,
    max_count: Optional[int] = None,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """批量生成 delivery 信封；支持时间窗口与条数上限（#21 §5.2）。"""
    import time

    base_ms = int(epoch_ms if epoch_ms is not None else time.time() * 1000)
    out: List[Dict[str, Any]] = []
    for transfer in transfer_plan.get("transfers") or []:
        ready = int(transfer.get("readyTimeMin") or 0)
        if ready_min_from is not None and ready < ready_min_from:
            continue
        if ready_min_to is not None and ready > ready_min_to:
            continue
        out.append(
            transfer_to_delivery_envelope(transfer, epoch_ms=base_ms, fleet=fleet, robot=robot)
        )
        if max_count is not None and len(out) >= max_count:
            break
    return out


def build_patrol_from_flow_matrix(
    flow_matrix: Dict[str, Any],
    waypoint_map: Dict[str, str],
    *,
    top_n: int = 5,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
) -> Dict[str, Any]:
    """flow_matrix Top-N 边 → patrol 信封（路网验证，#21 §5.3）。"""
    from unilabos.sim.fleet.rmf.task_dispatcher import build_patrol_request

    edges = sorted(
        flow_matrix.get("flow_edges") or [],
        key=lambda e: int(e.get("weight") or 0),
        reverse=True,
    )[:top_n]
    places: List[str] = []
    seen: set[str] = set()
    for edge in edges:
        for key in ("from_instance", "to_instance"):
            iid = str(edge.get(key) or "")
            wp = waypoint_map.get(iid)
            if wp and wp not in seen:
                seen.add(wp)
                places.append(wp)
    return build_patrol_request(places, rounds=1, fleet=fleet, robot=robot)
