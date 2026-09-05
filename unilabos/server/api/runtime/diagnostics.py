"""Backend Scheduler 与执行端的轻量诊断路由（含 status-incidents /
error-decisions / restart 干预入口），不复制 Runtime 数据面。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field


class StatusIncidentDecision(BaseModel):
    action: str = ""
    option: Optional[dict[str, Any]] = None
    reason: str = ""


class ErrorDecision(BaseModel):
    action: str = "abort"
    option: Optional[dict[str, Any]] = None
    reason: str = ""
    result: Any = None
    scheduler_updated: bool = True
    job_id: str = ""
    device_id: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class RestartRequest(BaseModel):
    """安静点重启请求。

    mode: quiescent 等执行端安静；immediate 跳过等待立即重启。
    scope: auto 按运行形态选择（调度权威进程 → edge，只重启 Host；其它 → process）；
    edge 只重启 Host 进程，调度权威与管理端口常驻；process 本进程整体重启。
    """

    mode: str = "quiescent"
    scope: str = "auto"


def _hostlink_snapshot() -> dict[str, Any]:
    from unilabos.backend.hostlink.client import get_hostlink_client
    from unilabos.backend.hostlink.server import get_hostlink_server

    link_server = get_hostlink_server()
    link_client = get_hostlink_client()
    role = "host" if link_server else ("slave" if link_client else "disabled")
    result: dict[str, Any] = {
        "role": role,
        "peers": link_server.peers() if link_server else [],
        "client": (
            {
                "online": link_client.online,
                "host": link_client.host,
                "port": link_client.port,
                "node_id": link_client.node_id,
                "device_ids": link_client.device_ids,
                "capabilities": link_client.capabilities,
            }
            if link_client
            else None
        ),
    }
    if role != "disabled":
        hello = link_server.hello_payload if link_server else link_client.hello_info
        result.update(
            {
                "owner": hello.get("owner"),
                "host_id": hello.get("host_id") or hello.get("host_name"),
                "host_node_id": hello.get("host_node_id"),
                "protocol_version": hello.get("protocol_version"),
                "ros": hello.get("ros"),
            }
        )
    return result


def create_backend_router(
    get_scheduler: Callable[[], Any],
    get_execution_backend: Callable[[], Any],
) -> APIRouter:
    """创建不复制 Runtime/History/Telemetry 数据面的诊断路由。"""

    router = APIRouter(prefix="/api/v1", tags=["backend"])

    def execution() -> Any:
        value = get_execution_backend()
        if value is None:
            raise HTTPException(
                status_code=503,
                detail="backend execution service is not ready",
            )
        return value

    def _execution_state() -> str:
        """ready：本进程带执行面，或调度权威的 Host 子进程已接入控制面；
        restarting：带 Host 子进程但它此刻不在线；disabled：纯调度权威。"""

        if get_execution_backend() is not None:
            return "ready"
        from unilabos.server.api.edge_proxy import edge_proxy_enabled
        from unilabos.server.backend.edge_control import get_edge_control_service

        if not edge_proxy_enabled():
            return "disabled"
        service = get_edge_control_service()
        return "ready" if service is not None and service.connected else "restarting"

    @router.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "scheduler": "local" if get_scheduler() is not None else "remote",
            "execution": _execution_state(),
        }

    @router.get("/ping")
    def ping(client_timestamp: Optional[float] = None) -> dict[str, Any]:
        """HTTP 版 ping-pong：回显客户端时间戳并附服务端时钟，供链路时延 / 时钟偏差诊断。

        与控制 WebSocket 的 PingNotice / PongNotice 字段同名；host_node 的 test_latency
        对它所连的 Backend（本机进程或分体部署的 --role backend / 云端）逐次调用。
        """

        import time

        return {
            "client_timestamp": client_timestamp,
            "server_timestamp": time.time(),
            "scheduler": "local" if get_scheduler() is not None else "remote",
        }

    @router.get("/hostlink/peers")
    def hostlink_peers() -> dict[str, Any]:
        return _hostlink_snapshot()

    @router.get("/scheduler/resources")
    def scheduler_resources() -> dict[str, Any]:
        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(
                status_code=503,
                detail="scheduler authority is remote",
            )
        return scheduler.resource_snapshot().model_dump(
            mode="json",
            exclude_none=True,
        )

    @router.get("/status-incidents")
    def status_incidents(
        device_id: str = "",
        include_terminal: bool = False,
    ) -> dict[str, Any]:
        backend = execution()
        manager = getattr(backend, "status_incidents", None)
        if manager is None:
            raise HTTPException(status_code=503, detail="status policy is disabled")
        return {
            "host_ready": bool(backend.host_ready()),
            "incidents": manager.list(
                device_id=device_id,
                include_terminal=include_terminal,
            ),
            "holds": manager.holds(),
        }

    @router.post("/status-incidents/{incident_id}")
    def decide_status_incident(
        incident_id: str,
        body: StatusIncidentDecision,
    ) -> dict[str, Any]:
        backend = execution()
        manager = getattr(backend, "status_incidents", None)
        if manager is None:
            raise HTTPException(status_code=503, detail="status policy is disabled")
        try:
            result = manager.decide(
                incident_id,
                action=body.action,
                option=body.option,
                reason=body.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="status incident not found")
        return result["ack"]

    @router.get("/error-decisions")
    def error_decisions() -> dict[str, Any]:
        """待人工决策的清单：执行面挂起的失败 attempt + 调度器在重启后接管的
        执行态未知 / 决策上下文丢失的 attempt，两者报告同形。"""

        from unilabos.server.backend.edge_control import get_edge_control_service

        backend = get_execution_backend()
        scheduler = get_scheduler()
        edge_control = get_edge_control_service()
        if backend is None and scheduler is None and edge_control is None:
            raise HTTPException(
                status_code=503,
                detail="backend execution service is not ready",
            )
        items: list[dict[str, Any]] = []
        if backend is not None:
            items.extend(backend.list_error_decisions())
        elif edge_control is not None:
            # 执行面在 Host 进程：它打开终态闸门、等本进程放行的失败登记在控制面服务里
            items.extend(edge_control.list_error_decisions())
        lister = getattr(scheduler, "list_reconciliation_decisions", None)
        if callable(lister):
            items.extend(lister())
        return {"items": items}

    @router.post("/error-decisions/{decision_id}")
    def resolve_error_decision(
        decision_id: str,
        body: ErrorDecision,
    ) -> dict[str, Any]:
        decision = body.model_dump(exclude_none=True)
        extra = decision.pop("extra", {})
        if isinstance(extra, Mapping):
            decision.update(extra)
        from unilabos.server.backend.edge_control import get_edge_control_service

        scheduler = get_scheduler()
        edge_control = get_edge_control_service()
        owns = getattr(scheduler, "has_reconciliation_decision", None)
        if callable(owns) and owns(decision_id):
            resolved = scheduler.resolve_reconciliation_decision(decision_id, decision)
        elif get_execution_backend() is None and edge_control is not None:
            # 执行面在 Host 进程：本进程是调度权威，经 runtime.v1 命令放行终态闸门
            resolved = edge_control.resolve_error_decision(decision_id, decision)
        else:
            resolved = execution().resolve_error_decision(decision_id, decision)
        if not resolved:
            raise HTTPException(
                status_code=409,
                detail="error decision was rejected or no longer pending",
            )
        return {"decision_id": decision_id, "status": "resolved"}

    # ── 安静点重启（调试用） ─────────────────────────────────

    @router.post("/restart")
    def request_restart(body: RestartRequest) -> dict[str, Any]:
        """登记重启：暂停新派发，active job 清空后按 scope 重启并自动恢复。"""
        from unilabos.server.backend.restart import get_restart_coordinator

        try:
            return get_restart_coordinator().request(mode=body.mode, scope=body.scope)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/restart")
    def restart_status() -> dict[str, Any]:
        from unilabos.server.backend.restart import get_restart_coordinator

        return get_restart_coordinator().status()

    @router.delete("/restart")
    def cancel_restart() -> dict[str, Any]:
        from unilabos.server.backend.restart import get_restart_coordinator

        return get_restart_coordinator().cancel()

    return router


def create_backend_app(
    get_scheduler: Callable[[], Any] = lambda: None,
    get_execution_backend: Callable[[], Any] = lambda: None,
) -> FastAPI:
    app = FastAPI(title="Uni-Lab-OS Backend Diagnostics")
    app.include_router(create_backend_router(get_scheduler, get_execution_backend))
    return app


__all__ = [
    "ErrorDecision",
    "StatusIncidentDecision",
    "create_backend_app",
    "create_backend_router",
]
