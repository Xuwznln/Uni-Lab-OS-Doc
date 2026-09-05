"""调度权威进程把 Host 专有路由经控制面 WS 交给 Host 在进程内执行。

默认拓扑下 ``unilab`` 进程是调度权威（Scheduler / Workflow / Registry / Materials），
持有浏览器连接的管理端口；Host 作为子进程运行，**不监听任何端口**：它只主动发 HTTP
（物料 / 注册表 / 工作流上报、请求结果回送）和维持一条控制 WS。浏览器请求 Host 专有的
路由（设备、遥测、历史、HostLink、驱动包、受管进程）时，权威把请求作为 ``backend_http``
下发到 Host，Host 对自己的 ASGI 应用执行后用 HTTP 把结果 POST 回权威，这里再回给浏览器。
Host 不在线时返回 503，前端按能力面降级。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Request, Response

from unilabos.protocol.runtime.control import EdgeHttpResponse

logger = logging.getLogger(__name__)

#: ``/api/v1/<首段>`` 命中即交给 Host 执行。其余域由权威进程自己回答：workflow /
#: registry / materials / graphs（Graph Authority 在 materials.db）/ lab（布局在 runtime.db，
#: 跨 Host 重启保留）/ scheduler / restart / health / ping / error-decisions。
EDGE_ROUTE_PREFIXES = (
    "runtime",
    "telemetry",
    "history",
    "hostlink",
    "status-incidents",
    "driver-packages",
    "device-processes",
)

_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
# 逐跳头 / 由本进程重新计算的头 / CORS 由本进程中间件统一加
_DROP_REQUEST_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "keep-alive"}
_DROP_RESPONSE_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "content-encoding",
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-expose-headers",
    "vary",
}
#: 受管进程启动 / 驱动包卸载这类同步动作可能要几秒
PROXY_TIMEOUT_S = 60.0
_UNAVAILABLE_DETAIL = "host execution process is not available (starting or restarting)"

_enabled = False


def configure_edge_proxy(enabled: bool) -> None:
    """本进程是否把 Host 专有路由交给控制面上的 Host 执行。"""

    global _enabled
    _enabled = bool(enabled)


def edge_proxy_enabled() -> bool:
    return _enabled


def edge_http(
    method: str,
    path: str,
    *,
    headers: Optional[dict[str, str]] = None,
    body: bytes = b"",
    timeout: float = PROXY_TIMEOUT_S,
) -> Optional[EdgeHttpResponse]:
    """进程内调用：让 Host 执行一条请求；Host 不在线 / 超时返回 None。"""

    from unilabos.server.backend.edge_control import get_edge_control_service

    service = get_edge_control_service()
    if service is None or not service.connected:
        return None
    return service.http_request(method, path, headers=headers, body=body, timeout=timeout)


def _unavailable() -> Response:
    return Response(
        content=f'{{"detail": "{_UNAVAILABLE_DETAIL}"}}',
        status_code=503,
        media_type="application/json",
        headers={"Retry-After": "3"},
    )


async def _forward(request: Request) -> Response:
    if not _enabled:
        return _unavailable()
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _DROP_REQUEST_HEADERS
    }
    body = await request.body()
    # http_request 阻塞等 Host 回结果；放线程池，别占住事件循环
    upstream = await asyncio.to_thread(
        edge_http, request.method, path, headers=headers, body=body
    )
    if upstream is None:
        logger.debug("[EdgeProxy] %s %s 未得到 Host 响应", request.method, path)
        return _unavailable()
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _DROP_RESPONSE_HEADERS
    }
    return Response(
        content=upstream.body_bytes(),
        status_code=upstream.status_code,
        headers=response_headers,
    )


def create_edge_proxy_router() -> APIRouter:
    """Host 专有路由的透传路由器；必须先于本进程其它路由挂载才能优先匹配。"""

    router = APIRouter(include_in_schema=False)

    async def _proxy(request: Request, path: str = "") -> Response:
        return await _forward(request)

    for prefix in EDGE_ROUTE_PREFIXES:
        router.add_api_route(f"/api/v1/{prefix}", _proxy, methods=_ALL_METHODS)
        router.add_api_route(f"/api/v1/{prefix}/{{path:path}}", _proxy, methods=_ALL_METHODS)
    return router


__all__ = [
    "EDGE_ROUTE_PREFIXES",
    "configure_edge_proxy",
    "create_edge_proxy_router",
    "edge_http",
    "edge_proxy_enabled",
]
