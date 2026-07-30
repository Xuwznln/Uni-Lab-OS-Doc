"""云端 command-to-edge 幂等执行.

统一 envelope：command_id / expected_version / warehouse_zone_id / type / actor / payload。
- processed_command 表保证 command_id 幂等（重放返回首次结果，不重复扣减）
- expected_version 过期直接 rejected（禁止 Last-Write-Wins）
- 返回 {"command_id", "status": accepted|rejected|completed, "result"|"error"}
  P0 全部同步执行：成功即 completed，领域错误即 rejected。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict

from unilabos.app.scheduler.inventory.domain import (
    CommandRejected,
    InventoryError,
    MaterialRequirement,
)
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.utils.tracing import add_event, set_error, span

CommandHandler = Callable[[InventoryService, Dict[str, Any]], Dict[str, Any]]


def _serializable(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    return {"value": value}


def _handle_template_upsert(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.upsert_template(
        template_id=p["template_id"],
        name=p.get("name", ""),
        category=p.get("category", ""),
        spec=p.get("spec"),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_template_delete(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.delete_template(
        template_id=p["template_id"],
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_inbound(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    if p.get("kind") == "instance":
        return svc.register_instance(
            template_id=p.get("template_id", ""),
            lot_id=p.get("lot_id", ""),
            barcode=p.get("barcode", ""),
            edge_uuid=p.get("edge_uuid", ""),
            legacy_cloud_id=p.get("legacy_cloud_id", "") or p.get("cloud_uuid", ""),
            parent_uuid=p.get("parent_uuid", ""),
            slot_id=p.get("slot_id", ""),
            actor=cmd.get("actor", ""),
            causation_id=cmd["command_id"],
        )
    return svc.inbound_lot(
        template_id=p.get("template_id", ""),
        quantity=float(p.get("quantity") or 0),
        unit=p.get("unit", ""),
        batch_no=p.get("batch_no", ""),
        expiry=p.get("expiry", ""),
        lot_id=p.get("lot_id", ""),
        warehouse_zone_id=cmd.get("warehouse_zone_id", ""),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
    )


def _handle_reserve(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    node_requirements = {
        node_id: [MaterialRequirement.from_dict(r) for r in reqs]
        for node_id, reqs in (p.get("node_requirements") or {}).items()
    }
    return svc.reserve_workflow(
        workflow_id=p["workflow_id"],
        node_requirements=node_requirements,
        attempt=int(p.get("attempt") or 1),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
    )


def _handle_release(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    if p.get("node_id"):
        return svc.release_reservation(
            workflow_id=p["workflow_id"],
            node_id=p["node_id"],
            attempt=int(p.get("attempt") or 1),
            reason=p.get("reason", "cloud_release"),
            actor=cmd.get("actor", ""),
            causation_id=cmd["command_id"],
        )
    return svc.release_workflow(
        workflow_id=p["workflow_id"],
        reason=p.get("reason", "cloud_release"),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
    )


def _handle_consume_reservation(
    svc: InventoryService, cmd: Dict[str, Any]
) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.consume_reservation(
        workflow_id=p["workflow_id"],
        node_id=p["node_id"],
        attempt=int(p.get("attempt") or 1),
        parent_uuid=p.get("parent_uuid", ""),
        slot_id=p.get("slot_id", ""),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
    )


def _handle_quarantine_reservation(
    svc: InventoryService, cmd: Dict[str, Any]
) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.quarantine_reservation(
        workflow_id=p["workflow_id"],
        node_id=p["node_id"],
        attempt=int(p.get("attempt") or 1),
        reason=p.get("reason", "command_quarantine"),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
    )


def _handle_deploy(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.deploy_instance(
        edge_uuid=p["edge_uuid"],
        parent_uuid=p.get("parent_uuid", ""),
        slot_id=p.get("slot_id", ""),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_move(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.move_instance(
        edge_uuid=p["edge_uuid"],
        parent_uuid=p["parent_uuid"],
        slot_id=p.get("slot_id", ""),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_detach(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.detach_instance(
        edge_uuid=p["edge_uuid"],
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_set_parent(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    """设父物料（parent_material_uuid ≡ 树父）；slot_id 为可选具名位（PLR site 名）。"""
    p = cmd.get("payload") or {}
    return svc.set_instance_parent(
        edge_uuid=p["edge_uuid"],
        parent_uuid=p.get("parent_uuid", ""),
        slot_id=p.get("slot_id"),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_content_set(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.update_content(
        instance_uuid=p["edge_uuid"],
        state=p.get("state") or {},
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_content_clear(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.clear_content(
        instance_uuid=p["edge_uuid"],
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_consume(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.consume_instance(
        edge_uuid=p["edge_uuid"],
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_discard(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.discard_instance(
        edge_uuid=p["edge_uuid"],
        reason=p.get("reason", ""),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


def _handle_adjust(svc: InventoryService, cmd: Dict[str, Any]) -> Dict[str, Any]:
    p = cmd.get("payload") or {}
    return svc.adjust_lot(
        lot_id=p["lot_id"],
        new_total=float(p["new_total"]),
        reason=p.get("reason", ""),
        actor=cmd.get("actor", ""),
        causation_id=cmd["command_id"],
        expected_version=cmd.get("expected_version"),
    )


COMMAND_HANDLERS: Dict[str, CommandHandler] = {
    "inventory.template.upsert": _handle_template_upsert,
    "inventory.template.delete": _handle_template_delete,
    "inventory.inbound": _handle_inbound,
    "inventory.reserve": _handle_reserve,
    "inventory.release": _handle_release,
    "inventory.consume": _handle_consume_reservation,
    "inventory.quarantine": _handle_quarantine_reservation,
    "material.deploy": _handle_deploy,
    "material.move": _handle_move,
    "material.detach": _handle_detach,
    "material.set_parent": _handle_set_parent,
    "material.content.set": _handle_content_set,
    "material.content.clear": _handle_content_clear,
    "material.consume": _handle_consume,
    "material.discard": _handle_discard,
    "material.adjust": _handle_adjust,
}


def _execute_command(service: InventoryService, command: Dict[str, Any]) -> Dict[str, Any]:
    """执行一条云端 command（幂等 + 版本校验）。永不抛领域异常，返回 status."""
    command_id = str(command.get("command_id") or "")
    if not command_id:
        return {"command_id": "", "status": "rejected", "error": "missing command_id"}

    # 幂等重放：直接返回首次处理结果
    processed = service.store.get_processed_command(command_id)
    if processed is not None:
        return {
            "command_id": command_id,
            "status": processed["status"],
            "result": json.loads(processed["result_json"]),
            "replayed": True,
        }

    cmd_type = str(command.get("type") or "")
    handler = COMMAND_HANDLERS.get(cmd_type)
    now_ms = int(time.time() * 1000)

    if handler is None:
        response = {"command_id": command_id, "status": "rejected",
                    "error": f"unknown command type: {cmd_type}"}
        _record(service, command_id, "rejected", {"error": response["error"]}, now_ms)
        return response

    try:
        result = handler(service, command)
    except (CommandRejected, InventoryError) as exc:
        response = {"command_id": command_id, "status": "rejected",
                    "error": str(exc), "error_code": getattr(exc, "code", "inventory_error")}
        _record(service, command_id, "rejected", {"error": str(exc)}, now_ms)
        return response
    except (KeyError, TypeError, ValueError) as exc:
        response = {"command_id": command_id, "status": "rejected",
                    "error": f"bad payload: {exc}"}
        _record(service, command_id, "rejected", {"error": str(exc)}, now_ms)
        return response

    _record(service, command_id, "completed", _serializable(result), now_ms)
    return {"command_id": command_id, "status": "completed", "result": result}


def execute_command(service: InventoryService, command: Dict[str, Any]) -> Dict[str, Any]:
    """带连续追踪的 command 入口；不记录 payload/actor 等原文。"""

    attributes = {
        "inventory.command.id": str(command.get("command_id") or ""),
        "inventory.command.type": str(command.get("type") or ""),
        "inventory.expected_version": command.get("expected_version"),
        "edge.uuid": service.edge_id,
        "lab.id": service.lab_id,
    }
    with span(
        "inventory.command",
        attributes=attributes,
        kind="consumer",
    ) as command_span:
        response = _execute_command(service, command)
        status = str(response.get("status") or "")
        add_event(
            "inventory.command.result",
            {
                "inventory.command.status": status,
                "inventory.command.replayed": bool(response.get("replayed")),
                "error.type": response.get("error_code", ""),
            },
            span=command_span,
        )
        if status == "rejected":
            set_error(str(response.get("error") or "command rejected"), span=command_span)
        return response


def _record(
    service: InventoryService, command_id: str, status: str, result: Dict[str, Any], now_ms: int
) -> None:
    with service.store.transaction() as conn:
        conn.execute(
            "INSERT INTO processed_command(command_id, result_json, status, processed_at) "
            "VALUES (?,?,?,?) ON CONFLICT(command_id) DO NOTHING",
            (command_id, json.dumps(result, ensure_ascii=False, default=str), status, now_ms),
        )
