from __future__ import annotations

from fastapi.testclient import TestClient

from unilabos.config.config import HTTPConfig
from unilabos.server.api.app import app, browser_landing_url


def test_web_root_is_a_backend_frontend_catalog() -> None:
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Uni-Lab-OS Microbackend" in response.text
        assert "https://xuwznln.github.io/OpenLab-site/" in response.text
        assert "/api/docs" in response.text
        assert "https://deepmodeling.github.io/Uni-Lab-OS/" in response.text


def test_web_root_signposts_backend_when_edge_is_backend_controlled(monkeypatch) -> None:
    monkeypatch.setattr(HTTPConfig, "remote_addr", "http://127.0.0.1:8081")
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Uni-Lab-OS Edge 进程" in response.text
        assert "http://127.0.0.1:8081/" in response.text
        # Edge 侧不再宣传社区前端，前端应连接调度权威地址。
        assert "https://xuwznln.github.io/OpenLab-site/" not in response.text
        # 设备侧调试入口仍保留。
        assert "/api/docs" in response.text


def test_browser_opens_backend_page_when_backend_controlled(monkeypatch) -> None:
    monkeypatch.setattr(HTTPConfig, "remote_addr", "http://127.0.0.1:8081/")
    assert browser_landing_url("0.0.0.0", 8002) == "http://127.0.0.1:8081/"

    monkeypatch.setattr(HTTPConfig, "remote_addr", "")
    assert browser_landing_url("0.0.0.0", 8002) == "http://localhost:8002/"


def test_non_api_display_routes_are_not_exposed() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/status" not in paths
    assert "/registry-editor" not in paths
    assert "/open-folder" not in paths
    assert "/api/v1/job/add" not in paths
    assert "/api/v1/online-devices" not in paths
