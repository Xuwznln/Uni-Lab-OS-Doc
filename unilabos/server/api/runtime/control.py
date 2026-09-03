"""runtime.v1 控制面服务端 API：Edge 接入微后端的 WS 通知通道与 HTTP 命令文档。

Edge 侧对接方式与云端 Backend 完全一致：

- ``WS /api/v1/ws/schedule``：短通知通道（backend_session / backend_change /
  edge_change / edge_change_ack / ping / pong）
- ``GET /edge/commands/{command_uuid}``：命令权威正文
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from unilabos.server.backend.edge_control import (
    EdgeControlService,
    get_edge_control_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["EdgeControl"])


def _require_service() -> EdgeControlService:
    service = get_edge_control_service()
    if service is None:
        raise HTTPException(status_code=503, detail="edge control service not ready")
    return service


@router.get("/edge/commands/{command_uuid}")
def get_edge_command(command_uuid: str) -> dict[str, Any]:
    """Edge 收到 backend_change 通知后拉取的权威命令文档。"""

    document = _require_service().get_command_document(command_uuid)
    if document is None:
        raise HTTPException(status_code=404, detail="command not found")
    return document


async def _pump_outgoing(
    websocket: WebSocket, service: EdgeControlService, epoch: str
) -> None:
    """把服务的线程安全下行队列泵到当前 WS 连接。"""

    while service.connection_epoch == epoch:
        try:
            message = await asyncio.to_thread(service.outgoing.get, True, 0.5)
        except queue.Empty:
            continue
        await websocket.send_text(json.dumps(message, ensure_ascii=False))


async def _pump_incoming(
    websocket: WebSocket, service: EdgeControlService, epoch: str
) -> None:
    while service.connection_epoch == epoch:
        raw = await websocket.receive_text()
        try:
            envelope = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("[EdgeControl] 忽略无效 JSON 上行消息")
            continue
        if not isinstance(envelope, dict):
            continue
        action = str(envelope.get("action") or "")
        data = envelope.get("data", {})
        if not isinstance(data, dict):
            continue
        # handle_message 内部可能同步拉取 Edge HTTP payload，放线程池
        reply: Optional[dict[str, Any]] = await asyncio.to_thread(
            service.handle_message, action, data
        )
        if reply is not None and service.connection_epoch == epoch:
            await websocket.send_text(json.dumps(reply, ensure_ascii=False))


@router.websocket("/api/v1/ws/schedule")
async def edge_schedule_socket(websocket: WebSocket) -> None:
    service = get_edge_control_service()
    if service is None:
        await websocket.close(code=1013, reason="edge control service not ready")
        return
    await websocket.accept()
    epoch, session_message = service.attach_connection()
    try:
        await websocket.send_text(json.dumps(session_message, ensure_ascii=False))
        incoming = asyncio.create_task(
            _pump_incoming(websocket, service, epoch), name="edge-control-incoming"
        )
        outgoing = asyncio.create_task(
            _pump_outgoing(websocket, service, epoch), name="edge-control-outgoing"
        )
        done, pending = await asyncio.wait(
            {incoming, outgoing}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except asyncio.CancelledError:
                pass
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.exception("[EdgeControl] WS 通道异常", exc_info=exc)
    except WebSocketDisconnect:
        pass
    finally:
        service.detach_connection(epoch)


def install_edge_control_api(app: FastAPI) -> None:
    app.include_router(router)


__all__ = ["install_edge_control_api", "router"]
