"""Backend-shaped Workflow Interface and local soft-delete coverage."""

from __future__ import annotations

from fastapi.testclient import TestClient

from unilabos.app.scheduler.api import create_scheduler_router
from unilabos.app.workflow_api import create_workflow_app
from unilabos.workflow.service import WorkflowService
from unilabos.workflow.store import WorkflowStore


def _client(tmp_path):
    store = WorkflowStore(tmp_path / "workflow_history.db")
    service = WorkflowService(store)
    return TestClient(create_workflow_app(service)), store


def test_workflow_definition_task_snapshot_and_soft_delete_match_backend(tmp_path):
    client, store = _client(tmp_path)

    created = client.post(
        "/api/v1/workflows",
        json={"name": "local workflow", "tags": [], "meta_data": {}},
    )
    assert created.status_code == 201
    assert created.json()["code"] == 0
    workflow = created.json()["data"]
    workflow_uuid = workflow["uuid"]
    assert "deleted_at" not in workflow
    assert workflow["revision"] == 1

    graph = client.put(
        f"/api/v1/workflows/{workflow_uuid}/graph",
        json={
            "revision": 1,
            "nodes": [
                {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "name": "approval",
                    "type": "manual_confirm",
                    "pose": {},
                    "param": {},
                    "execution_policy": {},
                    "disabled": False,
                    "minimized": False,
                    "meta_data": {},
                }
            ],
            "edges": [],
        },
    )
    assert graph.status_code == 200
    assert graph.json()["code"] == 0
    public_node = graph.json()["data"]["nodes"][0]
    assert "status" not in public_node

    task_response = client.post(
        "/api/v1/workflow-tasks",
        json={
            "workflow_uuid": workflow_uuid,
            "run_mode": "normal",
            "meta_data": {},
        },
    )
    assert task_response.status_code == 201
    task = task_response.json()["data"]
    assert task["status"] == "pending"
    assert "input" not in task
    assert "output" not in task
    assert "status" not in task["workflow_snapshot"]["nodes"][0]

    deleted = client.delete(f"/api/v1/workflows/{workflow_uuid}")
    assert deleted.status_code == 200
    assert deleted.json() == {"code": 0}
    row = store._conn.execute(
        "SELECT deleted_at FROM workflow WHERE uuid=?", (workflow_uuid,)
    ).fetchone()
    assert row["deleted_at"] is not None
    store.close()


def test_workflow_invalid_uuid_uses_backend_business_error(tmp_path):
    client, store = _client(tmp_path)
    response = client.get("/api/v1/workflows/not-a-uuid")
    assert response.status_code == 200
    assert response.json()["code"] == 1000
    assert response.json()["error"]["msg"]
    store.close()


def test_shared_workflow_routes_replace_execution_shaped_workflow_alias(tmp_path):
    router = create_scheduler_router(
        lambda: None,
        include_execution_shaped_workflow_routes=False,
    )
    assert not any(route.path == "/api/v1/workflows" for route in router.routes)

    client, store = _client(tmp_path)
    openapi = client.get("/openapi.json").json()
    assert "/api/v1/workflows" in openapi["paths"]
    edge_schema = openapi["components"]["schemas"]["WorkflowEdgeWrite"]
    assert "source_handle_uuid" in edge_schema["properties"]
    assert "target_handle_uuid" in edge_schema["properties"]
    assert "source_handle_key" not in edge_schema["properties"]
    store.close()
