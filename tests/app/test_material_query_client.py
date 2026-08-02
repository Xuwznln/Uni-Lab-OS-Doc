"""Switchable formal-backend / Edge-microbackend material HTTP client."""

from __future__ import annotations

from typing import Any, Dict, List

from unilabos.app.web.client import HTTPClient
from unilabos.config.config import BasicConfig


class _Response:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = repr(payload)

    def json(self) -> Any:
        return self._payload


class _Session:
    def __init__(self, responses: List[_Response]):
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.responses.pop(0)


def _client(source: str, responses: List[_Response]) -> tuple[HTTPClient, _Session]:
    client = HTTPClient(
        remote_addr="https://formal.example/api/v1",
        auth="test-token",
        material_source=source,
        material_microbackend_addr="http://127.0.0.1:8092",
    )
    session = _Session(responses)
    client._session = session  # type: ignore[assignment]
    return client, session


def test_microbackend_uses_local_legacy_compat_endpoint() -> None:
    client, session = _client(
        "microbackend",
        [_Response({"code": 0, "data": {"nodes": [{"uuid": "edge-a"}]}})],
    )

    nodes = client.material_query(uuids=["edge-a"], with_children=False)

    assert nodes == [{"uuid": "edge-a"}]
    assert session.calls == [
        {
            "method": "POST",
            "url": "http://127.0.0.1:8092/api/v1/edge/material/query",
            "json": {"uuids": ["edge-a"], "with_children": False},
            "timeout": 10,
        }
    ]


def test_backend_switch_preserves_original_uuid_query() -> None:
    client, session = _client(
        "backend",
        [_Response({"code": 0, "data": {"nodes": [{"uuid": "cloud-a"}]}})],
    )

    assert client.resource_tree_get(["cloud-a"], True) == [{"uuid": "cloud-a"}]
    assert (
        session.calls[0]["url"] == "https://formal.example/api/v1/edge/material/query"
    )
    assert session.calls[0]["json"] == {
        "uuids": ["cloud-a"],
        "with_children": True,
    }


def test_backend_id_query_and_legacy_resource_get_envelope() -> None:
    client, session = _client(
        "backend",
        [_Response({"code": 0, "data": [{"id": "rack-a", "uuid": "u-a"}]})],
    )

    result = client.resource_get("rack-a", with_children=True)

    assert result == {"code": 0, "data": [{"id": "rack-a", "uuid": "u-a"}]}
    assert session.calls[0] == {
        "method": "GET",
        "url": "https://formal.example/api/v1/lab/material",
        "params": {"id": "rack-a", "with_children": True},
        "timeout": 10,
    }


def test_auto_falls_through_empty_microbackend_to_formal_backend() -> None:
    client, session = _client(
        "auto",
        [
            _Response({"nodes": []}),
            _Response([{"uuid": "cloud-a"}]),
        ],
    )

    nodes = client.material_query(uuids=["cloud-a"])

    assert nodes == [{"uuid": "cloud-a"}]
    assert [call["url"] for call in session.calls] == [
        "http://127.0.0.1:8092/api/v1/edge/material/query",
        "https://formal.example/api/v1/edge/material/query",
    ]


def test_microbackend_failure_returns_empty_for_host_memory_fallback() -> None:
    client, _session = _client(
        "microbackend",
        [_Response({"detail": "inventory disabled"}, status_code=503)],
    )

    assert client.material_query(resource_id="local-rack") == []


def test_default_slave_client_cannot_open_a_direct_material_channel(
    monkeypatch,
) -> None:
    monkeypatch.setattr(BasicConfig, "is_host_mode", False)
    client = HTTPClient(
        auth="test-token",
        material_source="microbackend",
        material_microbackend_addr="http://127.0.0.1:8092",
    )
    session = _Session(
        [_Response({"code": 0, "data": {"nodes": [{"uuid": "forbidden"}]}})]
    )
    client._session = session  # type: ignore[assignment]

    assert client.material_query(uuids=["forbidden"]) == []
    assert session.calls == []
