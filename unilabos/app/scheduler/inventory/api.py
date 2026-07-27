"""Edge 仓储本地 FastAPI 路由（薄层：解析请求 → service/commands → 序列化）.

可独立挂载，也可通过 create_router 接入现有 edge composition root 的 FastAPI app。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException

from unilabos.app.scheduler.inventory.commands import execute_command
from unilabos.app.scheduler.inventory.domain import InventoryError
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.sync import build_snapshot


def create_router(service: InventoryService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])

    @router.get("/health")
    def health() -> Dict[str, Any]:
        return {"status": "ok", "edge_id": service.edge_id, "lab_id": service.lab_id}

    @router.post("/commands")
    def post_command(command: Dict[str, Any]) -> Dict[str, Any]:
        """统一 command 入口（与 WS 下发同一执行路径，幂等）."""
        return execute_command(service, command)

    @router.get("/lots")
    def list_lots(limit: int = 500) -> Dict[str, Any]:
        return {
            "lots": service.store.query_all(
                "SELECT * FROM inventory_lot ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        }

    @router.get("/lots/{lot_id}")
    def get_lot(lot_id: str) -> Dict[str, Any]:
        lot = service.store.get_lot(lot_id)
        if lot is None:
            raise HTTPException(status_code=404, detail=f"lot {lot_id} not found")
        return lot

    @router.get("/instances")
    def list_instances(status: str = "", limit: int = 500) -> Dict[str, Any]:
        if status:
            rows = service.store.query_all(
                "SELECT * FROM material_instance WHERE status = ? ORDER BY rowid DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = service.store.query_all(
                "SELECT * FROM material_instance ORDER BY rowid DESC LIMIT ?", (limit,)
            )
        return {"instances": rows}

    @router.get("/reservations")
    def list_reservations(status: str = "", limit: int = 500) -> Dict[str, Any]:
        if status:
            rows = service.store.query_all(
                "SELECT * FROM inventory_reservation WHERE status = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = service.store.query_all(
                "SELECT * FROM inventory_reservation ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
        return {"reservations": rows}

    @router.get("/templates")
    def list_templates(limit: int = 500) -> Dict[str, Any]:
        return {
            "templates": service.store.query_all(
                "SELECT * FROM resource_template ORDER BY template_id LIMIT ?", (limit,)
            )
        }

    @router.get("/templates/{template_id}")
    def get_template(template_id: str) -> Dict[str, Any]:
        template = service.store.query_one(
            "SELECT * FROM resource_template WHERE template_id = ?", (template_id,)
        )
        if template is None:
            raise HTTPException(status_code=404, detail=f"template {template_id} not found")
        return template

    @router.get("/instances/{edge_uuid}")
    def get_instance(edge_uuid: str) -> Dict[str, Any]:
        inst = service.store.get_instance(edge_uuid)
        if inst is None:
            raise HTTPException(status_code=404, detail=f"instance {edge_uuid} not found")
        relation = service.store.get_relation(edge_uuid)
        content = service.store.get_content(edge_uuid)
        return {**inst, "relation": relation, "content": content}

    @router.get("/reservations/{reservation_id}")
    def get_reservation(reservation_id: str) -> Dict[str, Any]:
        reservation = service.store.query_one(
            "SELECT * FROM inventory_reservation WHERE reservation_id = ?",
            (reservation_id,),
        )
        if reservation is None:
            raise HTTPException(
                status_code=404, detail=f"reservation {reservation_id} not found"
            )
        return reservation

    @router.get("/workflows/{workflow_id}/reservations")
    def get_reservations(workflow_id: str) -> Dict[str, Any]:
        return {
            "workflow_id": workflow_id,
            "reservations": service.store.reservations_for_workflow(workflow_id),
        }

    @router.get("/snapshot")
    def snapshot() -> Dict[str, Any]:
        """全量状态导出（云端初次接入/缺口重建 projection 用）."""
        return build_snapshot(service.store)

    @router.get("/ledger")
    def ledger(limit: int = 200, after_id: int = 0) -> Dict[str, Any]:
        rows = service.store.query_all(
            "SELECT * FROM inventory_ledger WHERE ledger_id > ? ORDER BY ledger_id ASC LIMIT ?",
            (after_id, limit),
        )
        return {"entries": rows}

    @router.get("/outbox/backlog")
    def outbox_backlog() -> Dict[str, Any]:
        return {
            "max_sequence": service.store.max_outbox_sequence(),
            "acked_sequence": service.store.get_cursor(),
        }

    return router


def create_app(service: Optional[InventoryService] = None) -> FastAPI:
    """独立运行入口（测试/调试用；生产建议挂到现有 edge app）."""
    from unilabos.app.scheduler.inventory.store import InventoryStore

    if service is None:
        service = InventoryService(InventoryStore(":memory:"))
    app = FastAPI(title="Uni-Lab Edge Inventory", version="0.1.0")
    app.include_router(create_router(service))

    @app.exception_handler(InventoryError)
    def _domain_error(_request, exc: InventoryError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"error": str(exc), "code": exc.code})

    return app
