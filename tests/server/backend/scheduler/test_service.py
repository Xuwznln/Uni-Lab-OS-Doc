from __future__ import annotations

from typing import Any

from unilabos.server.backend.scheduler.dag.models import DagNode, NodeState
from unilabos.server.backend.scheduler.service import BackendScheduler


class _Workflow:
    def __init__(self) -> None:
        self.running: list[str] = []
        self.terminal: list[tuple[str, str, dict[str, Any]]] = []
        self.retries: list[str] = []
        self.jobs: dict[str, dict[str, Any]] = {}

    def mark_workflow_node_job_running(self, job_uuid: str) -> None:
        self.running.append(job_uuid)

    def record_workflow_node_job_terminal(
        self, job_uuid: str, *, status: str, return_info=None, error_info=None
    ) -> dict[str, Any]:
        self.terminal.append((job_uuid, status, dict(return_info or {})))
        self.jobs[job_uuid] = {
            "uuid": job_uuid,
            "status": status,
            "return_info": dict(return_info or {}),
        }
        return self.jobs[job_uuid]

    def retry_workflow_node_job(self, job_uuid: str) -> dict[str, Any]:
        self.retries.append(job_uuid)
        new_job = {"uuid": f"{job_uuid}-retry", "status": "pending", "attempt": 2}
        self.jobs[new_job["uuid"]] = new_job
        return new_job

    def get_workflow_node_job(self, job_uuid: str) -> dict[str, Any]:
        return self.jobs.get(
            job_uuid, {"uuid": job_uuid, "status": "running", "return_info": {}}
        )


class _Runner:
    def __init__(self) -> None:
        self.terminal: list[tuple[str, NodeState]] = []

    def notify_terminal(self, job_id: str, state: NodeState) -> None:
        self.terminal.append((job_id, state))


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


def _running_node(scheduler: BackendScheduler, task: dict[str, Any], node: DagNode) -> _Runner:
    """把一个节点接入调度器簿记并派发，返回其任务的 runner 桩。"""

    runner = _Runner()
    scheduler._job_specs[node.node_id] = _spec("material-x")  # noqa: SLF001
    scheduler._job_to_task[node.node_id] = task["uuid"]  # noqa: SLF001
    scheduler._node_attempts[node.node_id] = node.node_id  # noqa: SLF001
    scheduler._runners[task["uuid"]] = runner  # type: ignore[assignment]  # noqa: SLF001
    scheduler._start_node(task, node)  # noqa: SLF001
    return runner


def test_retry_decision_records_failed_attempt_and_redispatches_without_ending_the_node() -> None:
    """retry：当前 attempt 如实记 failed，同节点新 attempt 重新申请资源并下发，DAG 不中断。"""

    workflow = _Workflow()
    executor = _Executor()
    scheduler = BackendScheduler(workflow, executor)  # type: ignore[arg-type]
    task = {"uuid": "task-1", "workflow_uuid": "workflow-1"}
    node = DagNode(node_id="job-1", device_id="device-a", action="use")
    runner = _running_node(scheduler, task, node)
    assert [item["job_id"] for item in executor.dispatched] == ["job-1"]
    assert executor.dispatched[0]["retry_count"] == 0

    scheduler._on_executor_finished(  # noqa: SLF001
        "job-1",
        False,
        None,
        "normal",
        {"error_resolution": {"selected_action": "retry", "decision_id": "d-1"}},
    )

    failed_uuid, failed_status, failed_info = workflow.terminal[0]
    assert (failed_uuid, failed_status) == ("job-1", "failed")
    assert failed_info["error_resolution"]["selected_action"] == "retry"
    assert workflow.retries == ["job-1"]
    # 节点未终结：runner 没收到任何终态，任务继续等待新 attempt
    assert runner.terminal == []
    assert [item["job_id"] for item in executor.dispatched] == ["job-1", "job-1-retry"]
    assert executor.dispatched[1]["retry_count"] == 1
    assert executor.dispatched[1]["node_id"] == "workflow-node"
    assert workflow.running == ["job-1", "job-1-retry"]
    assert scheduler.resources.request_for_owner("job-1").status == "released"
    assert scheduler.resources.request_for_owner("job-1-retry").status == "held"

    # 新 attempt 成功：按原 DAG 节点键终结 SUCCESS，上游输出解析指向当前 attempt
    scheduler._on_executor_finished("job-1-retry", True, {"ok": True}, "normal", {})  # noqa: SLF001
    assert workflow.terminal[-1][:2] == ("job-1-retry", "succeeded")
    assert runner.terminal == [("job-1", NodeState.SUCCESS)]
    assert scheduler._current_job("job-1") == "job-1-retry"  # noqa: SLF001
    assert scheduler.resources.request_for_owner("job-1-retry").status == "released"


def test_abort_decision_fails_the_node_without_retry() -> None:
    workflow = _Workflow()
    executor = _Executor()
    scheduler = BackendScheduler(workflow, executor)  # type: ignore[arg-type]
    task = {"uuid": "task-1", "workflow_uuid": "workflow-1"}
    node = DagNode(node_id="job-1", device_id="device-a", action="use")
    runner = _running_node(scheduler, task, node)

    scheduler._on_executor_finished(  # noqa: SLF001
        "job-1", False, None, "normal", {"error_resolution": {"selected_action": "abort"}}
    )

    assert workflow.retries == []
    assert workflow.terminal[0][:2] == ("job-1", "failed")
    assert runner.terminal == [("job-1", NodeState.FAILED)]
    assert [item["job_id"] for item in executor.dispatched] == ["job-1"]

