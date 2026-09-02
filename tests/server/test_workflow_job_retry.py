"""工作流节点 job 的 retry attempt 落表契约。

失败的 attempt 保留为 failed 事实；retry 为同一任务、同一节点追加 attempt+1 的新
job；任务恢复/建图只看每个节点的最新 attempt，早先的 failed 不会把任务判成终态。
"""

from __future__ import annotations

import pytest

from unilabos.server.services.runtime.workflow.service import WorkflowService
from unilabos.server.services.runtime.workflow.store import StoreConflict


@pytest.fixture()
def service() -> WorkflowService:
    instance = WorkflowService(":memory:")
    try:
        yield instance
    finally:
        instance.close()


def _single_job_task(service: WorkflowService) -> tuple[dict, dict]:
    service.set_task_submitter(lambda _uuid: None)
    task = service.create_ad_hoc_device_action_task(
        device_id="fault_injector",
        action_name="run_step",
        action_type="UniLabJsonCommand",
        param={"step_name": "flaky", "fail": True},
        execution_policy={"always_free": True},
        description="retry contract",
        meta_data={},
    )
    prepared = service.prepare_workflow_task_execution(task["uuid"])
    assert prepared["state"] == "ready"
    (job,) = prepared["jobs"]
    return task, job


def test_retry_appends_next_attempt_and_keeps_failed_history(service: WorkflowService) -> None:
    task, job = _single_job_task(service)
    service.mark_workflow_node_job_running(job["uuid"])
    failed = service.record_workflow_node_job_terminal(
        job["uuid"],
        status="failed",
        return_info={"suc": False, "error_resolution": {"selected_action": "retry"}},
        error_info=[{"code": "action_failed"}],
    )
    assert failed["status"] == "failed" and failed["attempt"] == 1

    retried = service.retry_workflow_node_job(job["uuid"])
    assert retried["uuid"] != job["uuid"]
    assert retried["attempt"] == 2
    assert retried["status"] == "pending"
    assert retried["workflow_node_uuid"] == job["workflow_node_uuid"]
    assert retried["workflow_task_uuid"] == task["uuid"]
    assert retried["param"] == job["param"]
    assert retried["execution_policy"] == job["execution_policy"]
    assert retried["meta_data"]["retry_of"] == job["uuid"]

    jobs = service.list_workflow_node_jobs(task["uuid"])
    assert [(item["attempt"], item["status"]) for item in jobs] == [
        (1, "failed"),
        (2, "pending"),
    ]
    assert [item["uuid"] for item in service.latest_attempts(jobs)] == [retried["uuid"]]

    # 只有 failed 的 attempt 才能重试；pending 的新 attempt 不能再派生
    with pytest.raises(StoreConflict):
        service.retry_workflow_node_job(retried["uuid"])


def test_recovery_judges_task_by_latest_attempt_not_by_failed_history(
    service: WorkflowService,
) -> None:
    task, job = _single_job_task(service)
    service.mark_workflow_node_job_running(job["uuid"])
    service.record_workflow_node_job_terminal(
        job["uuid"], status="failed", return_info={}, error_info=[{"code": "action_failed"}]
    )
    service.retry_workflow_node_job(job["uuid"])

    # 进程重启后重新认领：节点最新 attempt 为 pending，任务仍可继续而不是判 failed
    prepared = service.prepare_workflow_task_execution(task["uuid"])
    assert prepared["state"] == "ready"
    assert service.get_workflow_task(task["uuid"])["status"] == "running"
