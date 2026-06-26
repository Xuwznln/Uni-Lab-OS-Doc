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


def _catalog_transfer_to_delivery(
    transfer: Dict[str, Any],
    *,
    epoch_ms: int,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
    requester: str = "unilab",
) -> Dict[str, Any]:
    """catalog 版 transfer（含显式 pickupHandler/dropoffHandler）→ delivery 信封。"""
    ready_min = int(transfer.get("readyTimeMin") or 0)
    env = build_delivery_request(
        str(transfer.get("fromWaypoint") or ""),
        str(transfer.get("pickupHandler") or ""),
        str(transfer.get("toWaypoint") or ""),
        str(transfer.get("dropoffHandler") or ""),
        payload=list(transfer.get("payload") or []),
        fleet=fleet,
        robot=robot,
        requester=requester,
    )
    req = env["request"]
    req["unix_millis_earliest_start_time"] = epoch_ms + ready_min * 60_000
    labels = list(req.get("labels") or [])
    task_id = str(transfer.get("taskId") or "")
    if task_id and task_id not in labels:
        labels.append(task_id)
    labels.append("transfer_plan")
    req["labels"] = labels
    return env


def build_delivery_envelopes_from_catalog(
    catalog: Dict[str, Any],
    *,
    epoch_ms: Optional[int] = None,
    ready_min_from: Optional[int] = None,
    ready_min_to: Optional[int] = None,
    max_count: Optional[int] = None,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """RmfNavPathCatalog → 时间窗口内的 delivery 信封列表（path_studio「下发转运」）。"""
    import time

    from unilabos.sim.fleet.rmf.layout_optimizer.catalog_to_transfer_plan import (
        build_transfer_plan_from_catalog,
    )

    plan = build_transfer_plan_from_catalog(catalog)
    base_ms = int(epoch_ms if epoch_ms is not None else time.time() * 1000)
    out: List[Dict[str, Any]] = []
    for transfer in plan.get("transfers") or []:
        ready = int(transfer.get("readyTimeMin") or 0)
        if ready_min_from is not None and ready < ready_min_from:
            continue
        if ready_min_to is not None and ready > ready_min_to:
            continue
        out.append(_catalog_transfer_to_delivery(transfer, epoch_ms=base_ms, fleet=fleet, robot=robot))
        if max_count is not None and len(out) >= max_count:
            break
    return out


def build_delivery_envelopes_from_paths(
    transfer_paths: Dict[str, Any],
    *,
    epoch_ms: Optional[int] = None,
    ready_min_from: Optional[int] = None,
    ready_min_to: Optional[int] = None,
    max_count: Optional[int] = None,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
    skip_self_loop: bool = True,
) -> List[Dict[str, Any]]:
    """RmfTransferPaths（#18 §9.9）→ 时间窗口内的 delivery 信封列表（发布给 RMF）。

    每条 transfer 已含显式 `fromWaypoint/toWaypoint` + `pickupHandler/dropoffHandler`，直接复用。
    """
    import time

    base_ms = int(epoch_ms if epoch_ms is not None else time.time() * 1000)
    out: List[Dict[str, Any]] = []
    for transfer in transfer_paths.get("transfers") or []:
        ready = int(transfer.get("readyTimeMin") or 0)
        if ready_min_from is not None and ready < ready_min_from:
            continue
        if ready_min_to is not None and ready > ready_min_to:
            continue
        # 仅当真·同设备同 handler（取放是同一工位）才跳过；不同设备共用 dock 接驳点（from==to 但 handler 不同）是合法共址转运，保留。
        if skip_self_loop and str(transfer.get("pickupHandler")) == str(transfer.get("dropoffHandler")):
            continue
        out.append(_catalog_transfer_to_delivery(transfer, epoch_ms=base_ms, fleet=fleet, robot=robot))
        if max_count is not None and len(out) >= max_count:
            break
    return out


def build_patrol_envelopes_from_paths(
    transfer_paths: Dict[str, Any],
    *,
    ready_min_from: Optional[int] = None,
    ready_min_to: Optional[int] = None,
    max_count: Optional[int] = None,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
    rounds: int = 1,
) -> List[Dict[str, Any]]:
    """RmfTransferPaths → 每条 transfer 的 patrol 信封（places=[fromWaypoint, toWaypoint]）。

    sim 环境无真实 dispenser/ingestor，delivery 不出价；用 patrol 让 AGV **沿转运路线实际行驶**。
    """
    from unilabos.sim.fleet.rmf.task_dispatcher import build_patrol_request

    out: List[Dict[str, Any]] = []
    for transfer in transfer_paths.get("transfers") or []:
        ready = int(transfer.get("readyTimeMin") or 0)
        if ready_min_from is not None and ready < ready_min_from:
            continue
        if ready_min_to is not None and ready > ready_min_to:
            continue
        places = [p for p in (transfer.get("fromWaypoint"), transfer.get("toWaypoint")) if p]
        if len(places) < 2 or places[0] == places[1]:
            continue
        out.append(build_patrol_request(places, rounds=rounds, fleet=fleet, robot=robot))
        if max_count is not None and len(out) >= max_count:
            break
    return out


def build_patrol_from_catalog_path(
    catalog: Dict[str, Any],
    path_id: str,
    *,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
) -> Dict[str, Any]:
    """按 pathId 取一条路径的 navSequence → patrol 信封（path_studio「Patrol 验路」）。"""
    from unilabos.sim.fleet.rmf.task_dispatcher import build_patrol_request

    path = next((p for p in (catalog.get("paths") or []) if str(p.get("pathId")) == str(path_id)), None)
    if path is None:
        raise ValueError(f"catalog 中无 pathId={path_id}")
    places = [str(n) for n in (path.get("navSequence") or [])]
    if not places:
        raise ValueError(f"path {path_id} 的 navSequence 为空")
    return build_patrol_request(places, rounds=1, fleet=fleet, robot=robot)


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
