"""管理 API 的 uvicorn 装配：优雅停机必须有上限，否则浏览器的 SSE 长连接会卡死安静点重启；
访问日志按写/读分级，客户端中途断开不能被当成服务端异常。"""

from __future__ import annotations

import asyncio
import logging

import pytest
import uvicorn
from fastapi import FastAPI
from starlette.requests import ClientDisconnect

from unilabos.server.api import app as app_module
from unilabos.utils import log as unilab_log
from unilabos.utils.fastapi import log_adapter


def _access_record(method: str, status: int) -> logging.LogRecord:
    """复刻 uvicorn h11/httptools 两种实现共用的访问日志形状。"""

    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="h11_impl.py",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", method, "/api/v1/workflow-tasks", "1.1", status),
        exc_info=None,
    )


@pytest.mark.parametrize(
    ("method", "status", "expected"),
    [
        ("GET", 200, unilab_log.trace),  # 前端轮询
        ("HEAD", 200, unilab_log.trace),
        ("OPTIONS", 200, unilab_log.trace),  # CORS 预检
        ("POST", 201, unilab_log.info),  # 提交工作流等写操作才是事件
        ("PUT", 200, unilab_log.info),
        ("DELETE", 200, unilab_log.info),
        ("GET", 404, unilab_log.debug),
        ("POST", 422, unilab_log.debug),
        ("GET", 500, unilab_log.warning),
    ],
)
def test_access_log_level_follows_method_and_status(method, status, expected) -> None:
    assert log_adapter.access_log_func(_access_record(method, status)) is expected


def test_access_log_with_unexpected_shape_stays_info() -> None:
    record = _access_record("GET", 200)
    record.args = ("only", "two")
    assert log_adapter.access_log_func(record) is unilab_log.info


def test_uvicorn_log_config_does_not_colorize_messages() -> None:
    """着色由 ColoredFormatter 负责；uvicorn 自己染色会把 ANSI 转义写进日志文件。"""

    config = log_adapter.setup_fastapi_logging()
    assert config["formatters"]["default"]["use_colors"] is False


def test_client_disconnect_during_body_read_is_not_an_asgi_error() -> None:
    """浏览器刷新/取消请求时 Workflow 路由预读 body 会遇到 ClientDisconnect，
    必须被 app 级 handler 收尾，而不是冒到 uvicorn 打出整段异常堆栈。"""

    from unilabos.server.api.runtime.workflow import install_workflow_api
    from unilabos.server.services.runtime.workflow.service import WorkflowService

    assert app_module.app.exception_handlers[ClientDisconnect] is app_module._on_client_disconnect

    test_app = FastAPI()
    install_workflow_api(test_app, WorkflowService(":memory:"))
    test_app.add_exception_handler(ClientDisconnect, app_module._on_client_disconnect)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/workflows",
        "raw_path": b"/api/v1/workflows",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"content-length", b"32")],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8002),
    }
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(test_app(scope, receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 499


def test_start_server_bounds_graceful_shutdown(monkeypatch) -> None:
    captured: dict = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.should_exit = False

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(app_module, "setup_server", lambda: app_module.app)
    monkeypatch.setattr(app_module, "setup_fastapi_logging", lambda: None)

    app_module.start_server(host="127.0.0.1", port=18999, open_browser=False)

    assert captured["ran"] is True
    assert captured["timeout_graceful_shutdown"] == app_module.GRACEFUL_SHUTDOWN_TIMEOUT_S
    assert 0 < app_module.GRACEFUL_SHUTDOWN_TIMEOUT_S <= 30
    assert app_module.request_server_shutdown() is False  # run 返回后已清空引用
