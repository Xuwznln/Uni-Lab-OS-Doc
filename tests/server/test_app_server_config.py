"""管理 API 的 uvicorn 装配：优雅停机必须有上限，否则浏览器的 SSE 长连接会卡死安静点重启。"""

from __future__ import annotations

import uvicorn

from unilabos.server.api import app as app_module


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
