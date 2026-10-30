"""Uni-Lab-OS 内置 React 控制台的静态下发边界。"""

from pathlib import Path, PurePosixPath
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from unilabos.config.config import BasicConfig
from unilabos.utils.log import info

DEFAULT_CONSOLE_DIRECTORY = Path(__file__).parent / "static" / "console"
_CONSOLE_ROUTE_NAME = "unilab-console"


class ConsoleStaticFiles(StaticFiles):
    """只为无扩展名的浏览器路由回退 SPA，并设置确定的缓存策略。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """返回静态资源；缺失的前端路由回退 index，缺失资源保持 404。"""

        try:
            response = await super().get_response(path, scope)
        except HTTPException as exception:
            can_fallback = (
                exception.status_code == 404
                and scope.get("method") in {"GET", "HEAD"}
                and not PurePosixPath(path).suffix
            )
            if not can_fallback:
                raise
            response = await super().get_response("index.html", scope)
            response.headers["Cache-Control"] = "no-cache"
            return response

        requested_path = path.strip("/")
        if response.status_code == 200:
            if requested_path in {"", ".", "index.html"}:
                response.headers["Cache-Control"] = "no-cache"
            else:
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                )
        return response


def _normalise_host(value: str) -> str:
    """把配置或请求中的 hostname 归一为无方括号、无尾点的小写形式。"""

    return value.strip().lower().removeprefix("[").removesuffix("]").rstrip(".")


def _allowed_frontend_hosts() -> set[str]:
    return {
        _normalise_host(item)
        for item in str(BasicConfig.frontend_allowed_hosts).split(",")
        if item.strip()
    }


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return {"http": 80, "https": 443}.get(scheme)


def _is_same_origin(request: Request, origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        origin_hostname = parsed.hostname
        origin_port = parsed.port
        request_hostname = request.url.hostname
        request_port = request.url.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not origin_hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    return (
        parsed.scheme == request.url.scheme
        and _normalise_host(origin_hostname)
        == _normalise_host(request_hostname or "")
        and _effective_port(parsed.scheme, origin_port)
        == _effective_port(request.url.scheme, request_port)
    )


async def _enforce_console_security(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    allowed_hosts = _allowed_frontend_hosts()
    try:
        request_host = _normalise_host(request.url.hostname or "")
    except ValueError:
        request_host = ""
    if allowed_hosts and request_host not in allowed_hosts:
        return JSONResponse(
            status_code=403,
            content={"detail": "request host is not allowed"},
        )

    if BasicConfig.frontend_same_origin and request.url.path.startswith("/api/"):
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        origin = request.headers.get("origin")
        if fetch_site == "cross-site" or (
            origin is not None and not _is_same_origin(request, origin)
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "cross-site browser request is not allowed"},
            )
    return await call_next(request)


def install_console_security(app: FastAPI) -> bool:
    """安装可配置的 Host 与浏览器同源边界；重复调用保持幂等。"""

    if getattr(app.state, "unilab_console_security_installed", False):
        return False
    app.add_middleware(BaseHTTPMiddleware, dispatch=_enforce_console_security)
    app.state.unilab_console_security_installed = True
    return True


def install_console_ui(
    app: FastAPI,
    directory: Path | None = None,
) -> bool:
    """把已编译控制台挂到 ``/console``，并让根地址保留查询参数后跳转。

    参数：``app`` 是 Web 组合根；``directory`` 可在测试中注入临时构建产物。
    返回：本次是否完成首次安装。缺少 ``index.html`` 时保持 API 可用并返回
    ``False``；重复调用同样返回 ``False``。
    """

    console_directory = Path(directory or DEFAULT_CONSOLE_DIRECTORY)
    if not (console_directory / "index.html").is_file():
        info(f"[Web] React 控制台构建产物不存在，跳过挂载: {console_directory}")
        return False
    if any(
        getattr(route, "name", None) == _CONSOLE_ROUTE_NAME
        for route in app.routes
    ):
        return False

    @app.get("/", include_in_schema=False, name="unilab-console-root")
    async def redirect_to_console(request: Request) -> RedirectResponse:
        query = request.url.query
        destination = "/console/"
        if query:
            destination = f"{destination}?{query}"
        return RedirectResponse(destination)

    app.mount(
        "/console",
        ConsoleStaticFiles(directory=console_directory, html=True),
        name=_CONSOLE_ROUTE_NAME,
    )
    return True
