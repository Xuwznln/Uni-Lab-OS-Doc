"""微后端 HTTP Application 与 Uvicorn 生命周期。"""

import errno
import socket
import webbrowser

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from unilabos.config.config import HTTPConfig
from unilabos.utils.fastapi.log_adapter import setup_fastapi_logging
from unilabos.utils.log import info, error
from unilabos.utils.tracing import install_http_tracing

# 优雅停机上限（秒）：等在途 HTTP 请求收尾，但不为 SSE 长连接无限等待。
GRACEFUL_SHUTDOWN_TIMEOUT_S = 5

RECOMMENDED_FRONTENDS = (
    {
        "name": "OpenLab",
        "url": "https://xuwznln.github.io/OpenLab-site/",
        "description": "面向 Uni-Lab OS 微后端的社区实验室前端。",
    },
)

DEVELOPER_LINKS = (
    {
        "name": "OpenAPI Explorer",
        "url": "/api/docs",
        "description": "在浏览器中查看并调用当前微后端 API。",
    },
    {
        "name": "ReDoc",
        "url": "/api/redoc",
        "description": "适合阅读完整 HTTP 契约的只读 API 文档。",
    },
    {
        "name": "DB Debug",
        "url": "/api/docs#/debug",
        "description": "四库 SQLite 只读浏览：表清单、行数与最新行数据。",
    },
    {
        "name": "Uni-Lab OS Documentation",
        "url": "https://deepmodeling.github.io/Uni-Lab-OS/",
        "description": "GitHub Pages 上的官方接入、设备和部署文档。",
    },
)

app = FastAPI(
    title="Uni-Lab-OS Microbackend API",
    description="Backend-only API service for Uni-Lab frontends and schedulers.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)
install_http_tracing(app)

edge_routes_mounted = False
materials_routes_mounted = False
server_routes_mounted = False
workflow_routes_mounted = False
registry_routes_mounted = False
driver_packages_mounted = False

# noinspection PyTypeChecker
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Last-Event-ID"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    """执行 HTTP 请求链，为应用级请求中间件保留统一入口。"""

    return await call_next(request)


@app.middleware("http")
async def allow_private_network(request: Request, call_next) -> Response:
    """公网托管的前端（GitHub Pages）访问局域网微后端时，Chrome 的 Private Network Access
    预检会带 ``Access-Control-Request-Private-Network: true``，必须原样应答否则请求被丢弃。
    CORSMiddleware 不认识这个头，这里单独补上。"""

    response = await call_next(request)
    if request.headers.get("access-control-request-private-network", "").lower() == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


def _render_cards(items) -> str:
    return "".join(
        (
            '<a class="card" href="{url}" target="_blank" rel="noreferrer">'
            '<strong>{name}</strong><span>{description}</span>'
            '<code>{url}</code></a>'
        ).format(**item)
        for item in items
    )


def _render_catalog_page(title: str, intro: str, sections) -> str:
    body = "".join(
        f'<h2>{heading}</h2><section class="grid">{_render_cards(items)}</section>'
        for heading, items in sections
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ margin: 0; font: 16px/1.55 system-ui, sans-serif; color: #18212f; background: #f6f8fb; }}
    main {{ max-width: 760px; margin: 10vh auto; padding: 0 24px; }}
    h1 {{ margin-bottom: 8px; }}
    h2 {{ margin: 30px 0 10px; font-size: 18px; }}
    p {{ color: #526173; }}
    .grid {{ display: grid; gap: 14px; margin-top: 28px; }}
    .card {{ display: grid; gap: 5px; padding: 18px; color: inherit; text-decoration: none;
      background: white; border: 1px solid #dce3ec; border-radius: 10px; }}
    .card:hover {{ border-color: #4c78ff; box-shadow: 0 5px 18px #25385816; }}
    .card span {{ color: #526173; }}
    code {{ color: #3157c8; overflow-wrap: anywhere; }}
  </style>
</head>
<body><main>
  <h1>{title}</h1>
  <p>{intro}</p>
  {body}
</main></body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def frontend_catalog() -> str:
    """返回社区前端与开发工具的入口导航。

    前端只以独立静态站（GitHub Pages）形式部署，本进程不托管页面。
    Backend-controlled 模式（配置了 --address）下本进程只是 Edge 执行面，
    Workflow/调度 API 不在本端口；页面退化为指向调度权威的路标，
    避免用户把前端连到本进程。
    """

    remote = (HTTPConfig.remote_addr or "").rstrip("/")
    if remote:
        sections = (
            (
                "调度权威",
                (
                    {
                        "name": "Backend 管理页",
                        "url": f"{remote}/",
                        "description": "Workflow/调度 API 与前端接入地址；前端请连接这里。",
                    },
                ),
            ),
            ("本进程调试（仅设备侧 API）", DEVELOPER_LINKS),
        )
        return _render_catalog_page(
            "Uni-Lab-OS Edge 进程",
            f"本进程运行设备执行与数据面，调度权威位于 <code>{remote}</code>。"
            "工作流编排与前端请访问调度权威地址。",
            sections,
        )
    return _render_catalog_page(
        "Uni-Lab-OS Microbackend",
        "此进程提供 Uni-Lab-OS 后端 API。请选择 API 工具，或使用部署在 GitHub "
        "Pages 上的社区前端连接当前地址。",
        (("推荐前端", RECOMMENDED_FRONTENDS), ("开发与接入", DEVELOPER_LINKS)),
    )


def _has_execution_face() -> bool:
    """本进程是否带设备执行面（Host）；--role backend 为 False。"""
    try:
        from unilabos.server.backend.composition import get_execution_backend

        return get_execution_backend() is not None
    except Exception:  # noqa: BLE001
        return False


def setup_server() -> FastAPI:
    """幂等挂载当前运行角色所需的 API 路由。"""
    global edge_routes_mounted, materials_routes_mounted, server_routes_mounted
    global workflow_routes_mounted, registry_routes_mounted, driver_packages_mounted

    # 驱动包台账 / 受管设备进程：只在带执行面（加载注册表、跑 HostLink server）的
    # Host 进程有意义，--role backend 不挂载。
    if not driver_packages_mounted and _has_execution_face():
        try:
            from unilabos.server.api.device_processes import create_device_processes_router
            from unilabos.server.api.driver_package_graphs import (
                create_driver_package_graphs_router,
            )
            from unilabos.server.api.driver_packages import create_driver_packages_router

            app.include_router(create_driver_packages_router())
            app.include_router(create_driver_package_graphs_router())
            app.include_router(create_device_processes_router())
            driver_packages_mounted = True
        except Exception as exc:  # noqa: BLE001 - 保留基础管理 API
            error(f"[Microbackend] 挂载驱动包 / 设备进程路由失败: {exc}")

    # Backend 诊断面不复制 Runtime/History/Telemetry 数据 API。
    if not edge_routes_mounted:
        try:
            from unilabos.server.api.runtime import create_backend_router
            from unilabos.server.backend.composition import (
                get_execution_backend,
                get_scheduler,
            )

            app.include_router(
                create_backend_router(
                    get_scheduler,
                    get_execution_backend,
                )
            )
            edge_routes_mounted = True
        except Exception as exc:  # noqa: BLE001 - 保留基础管理 API
            error(f"[Microbackend] 挂载执行观测路由失败: {exc}")

    # 本机调度（默认）时挂载 Workflow Authority 写 API；接入云端后
    # get_workflow_service() 为 None，不挂载。
    if not workflow_routes_mounted:
        try:
            from unilabos.server.backend.composition import get_workflow_service
            from unilabos.server.api.runtime.workflow import install_workflow_api

            workflow_service = get_workflow_service()
            if workflow_service is not None:
                install_workflow_api(app, workflow_service)
                workflow_routes_mounted = True
        except Exception as exc:  # noqa: BLE001 - 保留基础管理 API
            error(f"[Microbackend] 挂载本机 Workflow Authority 失败: {exc}")

    # Registry Authority 与 Workflow Authority 同归属：本机持有调度权威（默认 Host
    # 或 --role backend）时挂载条目级注册表版本 API；接入云端后由远端持有，不挂载。
    if not registry_routes_mounted:
        try:
            from unilabos.server.api.runtime.registry import install_registry_api
            from unilabos.server.services.runtime.registry import get_registry_service

            if get_registry_service() is not None:
                install_registry_api(app)
                registry_routes_mounted = True
        except Exception as exc:  # noqa: BLE001 - 保留基础管理 API
            error(f"[Microbackend] 挂载 Registry Authority 失败: {exc}")

    if not server_routes_mounted:
        try:
            from unilabos.server.api import install_server_apis
            from unilabos.server.composition import get_server_services
            from unilabos.server.backend.composition import get_materials_service

            services = get_server_services()
            if services is not None:
                include_materials = get_materials_service() is not None
                install_server_apis(
                    app,
                    services,
                    include_materials=include_materials,
                )
                server_routes_mounted = True
                materials_routes_mounted = include_materials
        except Exception as exc:  # noqa: BLE001 - 保留基础管理 API
            error(f"[Microbackend] 挂载四库 API 失败: {exc}")

    # 支持只装配 MaterialsService 的测试或嵌入式运行方式。
    if not materials_routes_mounted:
        try:
            from unilabos.server.api.materials import install_materials_api
            from unilabos.server.backend.composition import get_materials_service

            materials_service = get_materials_service()
            if materials_service is not None:
                install_materials_api(app, materials_service)
                materials_routes_mounted = True
        except Exception as exc:  # noqa: BLE001 - 保留基础管理 API
            error(f"[Microbackend] 挂载 Materials Provider 失败: {exc}")

    return app


_uvicorn_server = None


def request_server_shutdown() -> bool:
    """请求管理 API 停机（安静点重启用）。

    uvicorn 主循环每个 tick 检查 ``should_exit``，置位后 ``start_server``
    返回，main 的正常退出链路（关库、停 backend）随之执行。

    Returns:
        bool: 服务器正在运行且已收到停机请求。
    """
    server = _uvicorn_server
    if server is None:
        return False
    server.should_exit = True
    return True


def browser_landing_url(host: str, port: int) -> str:
    """启动时浏览器应打开的页面。

    Backend-controlled 模式下直达调度权威的管理页（本进程 / 只是路标）；
    否则打开本进程页面。
    """

    remote = (HTTPConfig.remote_addr or "").rstrip("/")
    if remote:
        return f"{remote}/"
    # noinspection HttpUrlsUsage
    return f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}/"


class ManagementPortInUseError(OSError):
    """管理端 HTTP 端口已被其他进程占用。"""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        super().__init__(
            errno.EADDRINUSE,
            f"管理端 HTTP 端口 {host}:{port} 已被占用。"
            f"请关闭占用该端口的程序（可能是上一个未退出的 Uni-Lab 进程），"
            f"或用 --port <其他端口> 换一个端口重新启动，例如 --port {port + 1}",
        )


_ADDR_IN_USE_ERRNOS = {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", 10048)}


def _is_addr_in_use(exc: OSError) -> bool:
    return exc.errno in _ADDR_IN_USE_ERRNOS or getattr(exc, "winerror", None) == 10048


def ensure_port_available(host: str, port: int) -> None:
    """启动 Uvicorn 之前先试绑一次端口，把 WinError 10048 之类的原始错误换成可操作的提示。

    Raises:
        ManagementPortInUseError: 端口已被占用。
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        if _is_addr_in_use(exc):
            raise ManagementPortInUseError(host, port) from exc
        # 其他绑定失败（无权限、地址不存在）交给 uvicorn 报原始错误
    finally:
        probe.close()


def start_server(host: str = "0.0.0.0", port: int = 8002, open_browser: bool = True) -> None:
    """
    启动服务器

    Args:
        host: 服务器主机
        port: 服务器端口
        open_browser: 是否自动打开浏览器

    Raises:
        ManagementPortInUseError: 端口已被占用，消息中包含 --port 的修改建议。
    """
    from uvicorn import Config, Server

    ensure_port_available(host, port)

    # 设置服务器
    setup_server()

    # 配置日志
    log_config = setup_fastapi_logging()

    # 启动前打开浏览器
    if open_browser:
        url = browser_landing_url(host, port)
        info(f"[Web] 正在打开浏览器访问: {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            error(f"[Microbackend] 无法打开浏览器: {str(e)}")

    # 启动服务器
    info(f"[Microbackend] 启动 FastAPI: {host}:{port}")

    # 浏览器长期挂着 SSE（/events、/materials/events），uvicorn 默认优雅停机会一直
    # "Waiting for connections to close"，安静点重启就永远走不到拉起新进程那一步。
    # 给个上限：超过后强制关闭剩余连接（前端 EventSource 会自动重连）。
    config = Config(
        app=app,
        host=host,
        port=port,
        log_config=log_config,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_S,
    )
    server = Server(config)

    global _uvicorn_server
    _uvicorn_server = server
    try:
        server.run()
    except SystemExit:
        # uvicorn 绑定失败时自行 sys.exit(1)；预检和真正绑定之间端口仍可能被抢占，
        # 再探测一次即可区分「端口被占」与其他启动失败。
        if not server.started:
            ensure_port_available(host, port)
        raise
    finally:
        _uvicorn_server = None


# 当脚本直接运行时启动服务器
if __name__ == "__main__":
    start_server()
