from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from unilabos.app.scheduler.api import create_scheduler_router
from unilabos.app.scheduler.backend import JobExecutionBackend
from unilabos.workflow.store import WorkflowStore


class _HostAdapter:
    def __init__(self):
        self.backend: JobExecutionBackend | None = None
        self.calls = 0

    def handle_action_error_decision(
        self,
        decision_id,
        job_id,
        decision,
        *,
        decision_target,
    ):
        self.calls += 1
        assert self.backend is not None
        self.backend.publish_action_event(
            "job_error_decision_resolved",
            {
                "decision_id": decision_id,
                "job_id": job_id,
                "device_id": decision["device_id"],
                "device_uuid": decision["device_uuid"],
                "selected_action": decision["action"],
                "reason": decision["reason"],
                "command_id": decision["command_id"],
                "resolved_at": "2026-08-15T10:01:00Z",
            },
        )
        return True


def _required() -> dict:
    return {
        "decision_id": "decision-rest-1",
        "job_id": "job-rest-1",
        "device_uuid": "device-uuid-1",
        "device_id": "device-route-1",
        "host_uuid": "host-1",
        "authority_epoch": "epoch-1",
        "attempt_id": "attempt-1",
        "attempt_no": 1,
        "attempt_kind": "original",
        "options": [{"action": "retry"}, {"action": "abort"}],
        "created_at": "2026-08-15T10:00:00Z",
        "expires_at": "2099-08-15T10:05:00Z",
    }


def _command(action: str = "retry") -> dict:
    return {
        "command_id": "command-rest-1",
        "decision_id": "decision-rest-1",
        "job_id": "job-rest-1",
        "device_id": "device-route-1",
        "device_uuid": "device-uuid-1",
        "host_uuid": "host-1",
        "authority_epoch": "epoch-1",
        "action": action,
        "reason": "operator selected",
    }


def test_durable_rest_command_executes_once_and_replays_first_snapshot(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.db")
    host = _HostAdapter()
    backend = JobExecutionBackend(
        host_node_getter=lambda: host,
        workflow_store=store,
    )
    host.backend = backend
    backend.publish_action_event("job_error_decision_required", _required())

    app = FastAPI()
    app.include_router(
        create_scheduler_router(lambda: None, get_backend=lambda: backend)
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/api/v1/error-decisions/decision-rest-1",
            json=_command(),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert response.json()["replayed"] is False
        assert host.calls == 1

        replay = client.post(
            "/api/v1/error-decisions/decision-rest-1",
            json=_command(),
        )
        assert replay.status_code == 200
        assert replay.json()["status"] == "completed"
        assert replay.json()["replayed"] is True
        assert host.calls == 1

        conflict = client.post(
            "/api/v1/error-decisions/decision-rest-1",
            json=_command(action="abort"),
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_conflict"
        assert host.calls == 1
    finally:
        store.close()


def test_durable_rest_requires_command_and_authority_identity(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.db")
    backend = JobExecutionBackend(
        host_node_getter=lambda: None,
        workflow_store=store,
    )
    app = FastAPI()
    app.include_router(
        create_scheduler_router(lambda: None, get_backend=lambda: backend)
    )
    client = TestClient(app)
    try:
        legacy_shape = {
            "decision_id": "decision-rest-1",
            "job_id": "job-rest-1",
            "device_id": "device-route-1",
            "action": "retry",
        }
        response = client.post(
            "/api/v1/error-decisions/decision-rest-1",
            json=legacy_shape,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "missing_command_identity"
    finally:
        store.close()
