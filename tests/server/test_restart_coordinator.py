"""安静点重启：派发闸门、安静检测与 API 行为。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import unilabos.server.backend.restart as restart_module
from unilabos.server.api.runtime import create_backend_app
from unilabos.server.backend.restart import RestartCoordinator
from unilabos.server.backend.scheduler.service import BackendScheduler


class _FakeExecutionBackend:
    """可控 active job 集合的执行端替身。"""

    def __init__(self) -> None:
        self._jobs: list[Any] = []
        self.wait_idle_called = False
        self.device_manager = SimpleNamespace(get_active_jobs=lambda: list(self._jobs))

    def set_active(self, job_ids: list[str]) -> None:
        self._jobs = [SimpleNamespace(job_id=job_id) for job_id in job_ids]

    def wait_idle(self, timeout: float = 5.0) -> bool:
        self.wait_idle_called = True
        return True


class _FakeScheduler:
    def __init__(self) -> None:
        self.dispatch_paused = False

    def pause_dispatch(self) -> None:
        self.dispatch_paused = True

    def resume_dispatch(self) -> None:
        self.dispatch_paused = False


@pytest.fixture()
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restart_module, "_POLL_INTERVAL_SECONDS", 0.01)


@pytest.fixture(autouse=True)
def clean_restart_flag() -> Any:
    restart_module._restart_requested.clear()
    yield
    restart_module._restart_requested.clear()


def _make_coordinator(
    backend: Optional[_FakeExecutionBackend] = None,
    scheduler: Optional[_FakeScheduler] = None,
) -> tuple[RestartCoordinator, _FakeExecutionBackend, _FakeScheduler]:
    backend = backend or _FakeExecutionBackend()
    scheduler = scheduler if scheduler is not None else _FakeScheduler()
    coordinator = RestartCoordinator(lambda: backend, lambda: scheduler)
    return coordinator, backend, scheduler


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _capture_shutdown(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    called = threading.Event()

    def fake_shutdown() -> bool:
        called.set()
        return True

    monkeypatch.setattr(
        "unilabos.server.api.app.request_server_shutdown",
        fake_shutdown,
    )
    return called


def test_restart_waits_for_active_jobs_then_triggers(
    fast_poll: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown_called = _capture_shutdown(monkeypatch)
    coordinator, backend, scheduler = _make_coordinator()
    backend.set_active(["job-1"])

    status = coordinator.request()

    assert status["pending"] is True
    assert scheduler.dispatch_paused is True
    time.sleep(0.1)
    assert not shutdown_called.is_set(), "active job 存在时不得重启"

    backend.set_active([])

    assert _wait_until(shutdown_called.is_set)
    assert restart_module.is_restart_requested()
    assert backend.wait_idle_called


def test_cancel_resumes_dispatch_without_restart(
    fast_poll: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown_called = _capture_shutdown(monkeypatch)
    coordinator, backend, scheduler = _make_coordinator()
    backend.set_active(["job-1"])

    coordinator.request()
    status = coordinator.cancel()

    assert status["pending"] is False
    assert scheduler.dispatch_paused is False
    backend.set_active([])
    time.sleep(0.1)
    assert not shutdown_called.is_set()
    assert not restart_module.is_restart_requested()


def test_immediate_mode_skips_quiescence_wait(
    fast_poll: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown_called = _capture_shutdown(monkeypatch)
    coordinator, backend, _ = _make_coordinator()
    backend.set_active(["job-1", "job-2"])

    coordinator.request(mode="immediate")

    assert _wait_until(shutdown_called.is_set)


def test_request_rejects_unknown_mode() -> None:
    coordinator, _, _ = _make_coordinator()
    with pytest.raises(ValueError, match="unsupported restart mode"):
        coordinator.request(mode="soft")


def test_request_rejects_devices_scope() -> None:
    # 设备与 Host 共用进程，因此 devices 不是可独立重启的作用域。
    coordinator, _, _ = _make_coordinator()
    with pytest.raises(ValueError, match="unsupported restart scope"):
        coordinator.request(scope="devices")


def test_scheduler_dispatch_gate_keeps_waiting_jobs() -> None:
    executor = MagicMock()
    scheduler = BackendScheduler(workflow=MagicMock(), executor=executor)
    scheduler.resources = MagicMock()

    scheduler.pause_dispatch()
    assert scheduler.dispatch_paused is True

    node = SimpleNamespace(node_id="job-1", device_id="d", action="a", always_free=False)
    task = {"uuid": "task-1"}
    scheduler._waiting_resource_jobs["job-1"] = (task, node)

    scheduler._dispatch_held_node(task, node)

    # 闸门生效：未查询资源、未派发、job 保持等待
    scheduler.resources.request_for_owner.assert_not_called()
    assert "job-1" in scheduler._waiting_resource_jobs
    assert "job-1" not in scheduler._dispatched_jobs
    executor.dispatch.assert_not_called()

    # resume 触发 _reconcile_resources 重算等待集合
    reconciled = threading.Event()
    scheduler._reconcile_resources = lambda: reconciled.set()  # type: ignore[method-assign]
    scheduler.resume_dispatch()
    assert scheduler.dispatch_paused is False
    assert reconciled.is_set()


def test_restart_api_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    coordinator, backend, scheduler = _make_coordinator()
    backend.set_active(["job-1"])
    monkeypatch.setattr(restart_module, "_coordinator", coordinator)

    client = TestClient(create_backend_app())

    response = client.post("/api/v1/restart", json={"mode": "quiescent"})
    assert response.status_code == 200
    assert response.json()["pending"] is True
    assert response.json()["active_jobs"] == ["job-1"]

    assert client.get("/api/v1/restart").json()["dispatch_paused"] is True

    response = client.delete("/api/v1/restart")
    assert response.status_code == 200
    assert response.json()["pending"] is False

    response = client.post("/api/v1/restart", json={"mode": "bogus"})
    assert response.status_code == 422
    coordinator.cancel()
