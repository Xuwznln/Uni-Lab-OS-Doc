from __future__ import annotations

from typing import Any

from unilabos.server.backend.scheduler.dag.models import DagNode
from unilabos.server.backend.scheduler.service import BackendScheduler


class _Workflow:
    def __init__(self) -> None:
        self.running: list[str] = []

    def mark_workflow_node_job_running(self, job_uuid: str) -> None:
        self.running.append(job_uuid)


class _Executor:
    def __init__(self) -> None:
        self.dispatched: list[dict[str, Any]] = []

    def add_job_finished_listener(self, listener) -> None:
        self.listener = listener

    def dispatch(self, payload: dict[str, Any]) -> None:
        self.dispatched.append(payload)

    def cancel_task(self, _task_uuid: str) -> list[str]:
        return []


def _spec(material_uuid: str) -> dict[str, Any]:
    return {
        "workflow_node_uuid": "workflow-node",
        "base_param": {"plate": {"uuid": material_uuid}},
        "edges": [],
        "jobs_by_node": {},
        "inventory_requirements": [],
        "reserved_material_uuids": [],
        "scheduler_revision": 1,
    }


def test_one_scheduler_serializes_the_complete_action_and_material_set() -> None:
    workflow = _Workflow()
    executor = _Executor()
    scheduler = BackendScheduler(
        workflow,  # type: ignore[arg-type]
        executor,
        materials_need_lock_resolver=lambda _device, _action: ["plate"],
    )
    task = {"uuid": "task-1", "workflow_uuid": "workflow-1"}
    first = DagNode(
        node_id="job-1",
        device_id="device-a",
        action="use",
    )
    second = DagNode(
        node_id="job-2",
        device_id="device-b",
        action="use",
    )
    scheduler._job_specs.update(  # noqa: SLF001
        {
            "job-1": _spec("material-shared"),
            "job-2": _spec("material-shared"),
        }
    )

    scheduler._start_node(task, first)  # noqa: SLF001
    scheduler._start_node(task, second)  # noqa: SLF001

    assert [item["job_id"] for item in executor.dispatched] == ["job-1"]
    assert scheduler.resources.request_for_owner("job-1").status == "held"
    assert scheduler.resources.request_for_owner("job-2").status == "waiting"
    assert scheduler.resources.request_for_owner("job-2").blockers == ["job-1"]

    scheduler._release_job_resources("job-1", canceled=False)  # noqa: SLF001

    assert [item["job_id"] for item in executor.dispatched] == [
        "job-1",
        "job-2",
    ]
    assert workflow.running == ["job-1", "job-2"]
    assert scheduler.resources.request_for_owner("job-2").status == "held"


def test_reserved_warehouse_material_is_part_of_the_same_resource_request() -> None:
    workflow = _Workflow()
    executor = _Executor()
    scheduler = BackendScheduler(workflow, executor)  # type: ignore[arg-type]
    scheduler._job_specs["job-warehouse"] = {  # noqa: SLF001
        **_spec("unused"),
        "base_param": {},
        "reserved_material_uuids": ["allocated-material"],
        "inventory_reservation_uuid": "reservation-1",
    }
    task = {"uuid": "task-1", "workflow_uuid": "workflow-1"}
    node = DagNode(
        node_id="job-warehouse",
        device_id="device-a",
        action="use",
    )

    scheduler._start_node(task, node)  # noqa: SLF001

    identifiers = scheduler.resources.request_for_owner(
        "job-warehouse"
    ).identifiers
    assert [item.kind for item in identifiers] == ["action", "material"]
    assert identifiers[1].material_uuid == "allocated-material"
    assert executor.dispatched[0]["inventory_reservation_uuid"] == "reservation-1"

