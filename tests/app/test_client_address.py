from __future__ import annotations

from unilabos.client.workflow import (
    derive_workflow_websocket_url,
    normalize_workflow_api_url,
)
from unilabos.config.config import HTTPConfig
from unilabos.server.backend.url import build_backend_websocket_url
from unilabos.utils.address import resolve_address


def test_address_normalization_has_no_builtin_default() -> None:
    # 不再内置云端默认地址或环境别名；地址原样规范化（去尾斜杠）
    assert resolve_address("http://edge:8002/") == "http://edge:8002"
    assert resolve_address("") == ""
    assert resolve_address(None) == ""
    assert normalize_workflow_api_url("http://edge:8002") == (
        "http://edge:8002/api/v1"
    )


def test_backend_and_workflow_derive_same_websocket_address(monkeypatch) -> None:
    monkeypatch.setattr(HTTPConfig, "remote_addr", "http://backend:8002/api/v1")
    monkeypatch.setattr(HTTPConfig, "schedule_addr", "")

    expected = "ws://backend:8003/api/v1/ws/schedule"
    assert build_backend_websocket_url() == expected
    assert derive_workflow_websocket_url(
        "http://backend:8002/api/v1"
    ) == expected


def test_low_level_schedule_override_preserves_explicit_path(monkeypatch) -> None:
    monkeypatch.setattr(HTTPConfig, "remote_addr", "https://backend/api/v1")
    monkeypatch.setattr(
        HTTPConfig,
        "schedule_addr",
        "wss://notices.example/control/ws?tenant=lab-1",
    )

    assert build_backend_websocket_url() == (
        "wss://notices.example/control/ws?tenant=lab-1"
    )
