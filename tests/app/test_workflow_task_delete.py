"""WorkflowTask 软删除的终态与资源门禁合同。"""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.intervention import ensure_intervention_schema
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore, utc_now


def _client(tmp_path):
    store = WorkflowStore(tmp_path / "workflow_history.db")
    return TestClient(create_workflow_app(WorkflowService(store))), store


def _task(client: TestClient, store: WorkflowStore) -> str:
    workflow = client.post(
        "/api/v1/workflows", json={"name": "delete test", "tags": [], "meta_data": {}}
    ).json()["data"]
    task = client.post(
        "/api/v1/workflow-tasks",
        json={"workflow_uuid": workflow["uuid"], "run_mode": "normal", "meta_data": {}},
    ).json()["data"]
    return task["uuid"]


def _mark_terminal(store: WorkflowStore, task_uuid: str, *, status: str = "succeeded", cleanup: str = "none") -> None:
    now = utc_now()
    with store.transaction() as connection:
        connection.execute(
            "UPDATE workflow_task SET status=?, cleanup_status=?, finished_at=?, update_time=? WHERE uuid=?",
            (status, cleanup, now, now, task_uuid),
        )


def test_terminal_task_with_no_resources_is_soft_deleted_and_idempotent(tmp_path):
    client, store = _client(tmp_path)
    task_uuid = _task(client, store)
    _mark_terminal(store, task_uuid)

    detail = client.get(f"/api/v1/workflow-tasks/{task_uuid}").json()["data"]
    assert detail["can_delete"] is True
    assert detail["delete_blockers"] == []

    deleted = client.delete(f"/api/v1/workflow-tasks/{task_uuid}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["status"] == "deleted"
    assert client.get(f"/api/v1/workflow-tasks/{task_uuid}").json()["code"] == 3002
    assert client.get("/api/v1/workflow-tasks").json()["data"]["total"] == 0
    assert store._conn.execute(
        "SELECT deleted_at FROM workflow_task WHERE uuid=?", (task_uuid,)
    ).fetchone()["deleted_at"]
    audit = store._conn.execute(
        """
        SELECT kind, from_status, to_status, data
        FROM workflow_runtime_journal
        WHERE workflow_task_uuid = ?
        ORDER BY sequence DESC LIMIT 1
        """,
        (task_uuid,),
    ).fetchone()
    assert audit["kind"] == "task_transition"
    assert audit["from_status"] == audit["to_status"] == "succeeded"
    assert json.loads(audit["data"])["event"] == "task_deleted"

    repeated = client.delete(f"/api/v1/workflow-tasks/{task_uuid}")
    assert repeated.status_code == 200
    assert repeated.json()["data"]["status"] == "already_deleted"
    store.close()


def test_non_terminal_task_exposes_blocker_and_delete_returns_conflict(tmp_path):
    client, store = _client(tmp_path)
    task_uuid = _task(client, store)

    detail = client.get(f"/api/v1/workflow-tasks/{task_uuid}").json()["data"]
    assert detail["can_delete"] is False
    assert any("尚未进入终态" in value for value in detail["delete_blockers"])

    response = client.delete(f"/api/v1/workflow-tasks/{task_uuid}")
    assert response.status_code == 200
    assert response.json()["code"] == 3003
    assert store._conn.execute(
        "SELECT deleted_at FROM workflow_task WHERE uuid=?", (task_uuid,)
    ).fetchone()["deleted_at"] is None
    store.close()


def test_cleanup_residue_blocks_delete_even_when_task_is_terminal(tmp_path):
    client, store = _client(tmp_path)
    task_uuid = _task(client, store)
    _mark_terminal(store, task_uuid, status="failed", cleanup="requires_attention")

    detail = client.get(f"/api/v1/workflow-tasks/{task_uuid}").json()["data"]
    assert detail["can_delete"] is False
    assert any("清理状态" in value for value in detail["delete_blockers"])
    response = client.delete(f"/api/v1/workflow-tasks/{task_uuid}")
    assert response.json()["code"] == 3003
    store.close()


def test_delete_eligibility_endpoint_reports_terminal_task_as_deletable(tmp_path):
    client, store = _client(tmp_path)
    task_uuid = _task(client, store)

    blocked = client.get(f"/api/v1/workflow-tasks/{task_uuid}/delete-eligibility")
    assert blocked.status_code == 200
    blocked_data = blocked.json()["data"]
    assert blocked_data["workflow_task_uuid"] == task_uuid
    assert blocked_data["can_delete"] is False
    assert blocked_data["already_deleted"] is False
    assert any("尚未进入终态" in value for value in blocked_data["delete_blockers"])

    _mark_terminal(store, task_uuid, status="canceled", cleanup="settled")
    ready = client.get(f"/api/v1/workflow-tasks/{task_uuid}/delete-eligibility").json()["data"]
    assert ready["can_delete"] is True
    assert ready["delete_blockers"] == []
    assert ready["status"] == "canceled"
    assert ready["cleanup_status"] == "settled"

    # 列表读契约保持紧凑：资格只在按任务的资格接口上提供。
    listed = client.get("/api/v1/workflow-tasks").json()["data"]["items"][0]
    assert "can_delete" not in listed
    store.close()


def test_delete_eligibility_endpoint_is_readable_after_soft_delete(tmp_path):
    client, store = _client(tmp_path)
    task_uuid = _task(client, store)
    _mark_terminal(store, task_uuid)
    assert client.delete(f"/api/v1/workflow-tasks/{task_uuid}").json()["data"]["status"] == "deleted"

    eligibility = client.get(f"/api/v1/workflow-tasks/{task_uuid}/delete-eligibility").json()["data"]
    assert eligibility["already_deleted"] is True
    assert eligibility["can_delete"] is False
    assert eligibility["delete_blockers"] == []
    assert eligibility["deleted_at"]
    store.close()


def test_delete_eligibility_endpoint_rejects_unknown_task(tmp_path):
    client, store = _client(tmp_path)
    missing = client.get(f"/api/v1/workflow-tasks/{uuid4()}/delete-eligibility")
    assert missing.json()["code"] == 3002
    invalid = client.get("/api/v1/workflow-tasks/not-a-uuid/delete-eligibility")
    assert invalid.json()["code"] != 0
    store.close()


def test_open_intervention_is_reported_as_delete_blocker(tmp_path):
    client, store = _client(tmp_path)
    task_uuid = _task(client, store)
    _mark_terminal(store, task_uuid, status="failed", cleanup="settled")
    now = utc_now()
    with store.transaction() as connection:
        ensure_intervention_schema(connection)
        job_uuid = str(uuid4())
        connection.execute(
            """
            INSERT INTO workflow_node_job(
                uuid, create_time, update_time, meta_data, workflow_task_uuid,
                workflow_node_uuid, feedback_sequence, topological_index,
                executor_kind, execution_policy, execution_timeout_seconds,
                status, attempt, param, feedback_data, return_info,
                control_data, error_info, finished_at
            ) VALUES (?, ?, ?, '{}', ?, 'node-1', 0, 0, 'robot', '{}', 0,
                      'succeeded', 1, '{}', '{}', '{}', '{}', '[]', ?)
            """,
            (job_uuid, now, now, task_uuid, now),
        )
        connection.execute(
            """
            INSERT INTO workflow_intervention(
                uuid, create_time, update_time, workflow_task_uuid,
                workflow_node_job_uuid, decision_id, revision, status,
                options, resume_control_status, opened_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 'open', '[]', 'paused', ?)
            """,
            (str(uuid4()), now, now, task_uuid, job_uuid, str(uuid4()), now),
        )

    detail = client.get(f"/api/v1/workflow-tasks/{task_uuid}").json()["data"]
    assert detail["can_delete"] is False
    assert any("人工干预" in value for value in detail["delete_blockers"])
    store.close()
