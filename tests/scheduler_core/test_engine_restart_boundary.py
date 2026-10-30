"""r3 的设备进程重启边界：业务失败与物理结算不能合并。"""

import pytest

from tests.scheduler_core.test_failed_transfer_retry import _failed_transfer
from unilabos.workflow.scheduler_capacity import admit_job_dispatch
from unilabos.workflow.store import StoreConflict
from unilabos.workflow.task_runtime_projection import TaskRuntimeProjection

from .conftest import build_core_runtime


@pytest.mark.parametrize("start_state", ["START_COMMITTED", "UNKNOWN"])
@pytest.mark.parametrize("persisted_status", ["running", "pending"])
def test_restart_keeps_started_attempt_and_capacity_until_real_result(tmp_path, start_state, persisted_status):
    runtime = build_core_runtime(tmp_path, max_in_flight_jobs=1)
    try:
        aggregate = runtime.submit(
            task_name="restart-protected", devices=["reactor-a", "reactor-a"], edges=[(0, 1)],
        )
        task_id = aggregate["task"]["uuid"]
        original = runtime.dispatcher.dispatched[0]
        job_id = original["job_id"]
        projection = TaskRuntimeProjection(runtime.workflow_store)
        original_claim = projection.get_execution_claim(job_id)["claim_uuid"]
        if start_state == "UNKNOWN" or persisted_status == "pending":
            # 注入开始确认缺失的持久故障切面；不根据时长猜测物理动作是否开始。
            with runtime.workflow_store.transaction() as connection:
                connection.execute(
                    "UPDATE workflow_node_job SET start_state=?,status=? WHERE uuid=?",
                    (start_state, persisted_status, job_id),
                )
        runtime.scheduler.on_execution_process_restarted((job_id,))
        task = runtime.workflow_store.get_task(task_id)
        assert task["status"] == "failed"
        assert task["cleanup_status"] == "requires_attention"
        job = runtime.workflow_store.get_job(job_id)
        assert job["status"] == persisted_status
        assert job["start_state"] == start_state
        assert job["uncertainty_reason"] == "execution_restart_reconciliation_required"
        assert projection.get_execution_claim(job_id)["claim_uuid"] == original_claim
        assert {item["state"] for item in projection.list_execution_locks(job_id)} == {"uncertain"}
        assert runtime.bridge.active_or_uncertain_job_ids() == {job_id}
        assert runtime.scheduler.has_inflight_job(job_id)
        runtime.scheduler.on_execution_process_restarted((job_id,))
        assert projection.get_execution_claim(job_id)["claim_uuid"] == original_claim
        if persisted_status == "pending":
            runtime.restart_scheduler()
            assert runtime.scheduler.has_inflight_job(job_id)
            assert len(runtime.dispatcher.dispatched) == 1
        runtime.submit(task_name="restart-waiting", devices=["reactor-b"])
        # 即便设备不同，未结算的原 Job 仍占全局容量。
        assert len(runtime.dispatcher.dispatched) == 1
        runtime.scheduler.on_job_finished(job_id, True, {"value": "physical-result"})
        assert runtime.workflow_store.get_task(task_id)["status"] == "failed"
        assert runtime.workflow_store.get_task(task_id)["cleanup_status"] == "settled"
        assert runtime.workflow_store.get_job(job_id)["status"] == "succeeded"
        assert len(runtime.dispatcher.dispatched) == 2
        assert runtime.dispatcher.dispatched[1]["device_id"] == "reactor-b"
        assert runtime.dispatcher.dispatched[1]["job_id"] != job_id
    finally:
        runtime.close()


def test_restart_cancels_manual_admission_proven_not_started(tmp_path):
    runtime = build_core_runtime(tmp_path, max_in_flight_jobs=1)
    try:
        aggregate, job_id = runtime.submit_manual(task_name="restart-not-started")
        task_id = aggregate["task"]["uuid"]
        assert runtime.workflow_store.get_job(job_id)["start_state"] == "NOT_STARTED"
        assert runtime.dispatcher.dispatched == []
        runtime.scheduler.on_execution_process_restarted((job_id,))
        assert runtime.workflow_store.get_job(job_id)["status"] == "canceled"
        assert runtime.workflow_store.get_task(task_id)["cleanup_status"] == "settled"
        assert runtime.bridge.active_or_uncertain_job_ids() == set()
        runtime.submit(task_name="after-safe-cancel", devices=["reactor-a"])
        assert len(runtime.dispatcher.dispatched) == 1
        assert runtime.dispatcher.dispatched[0]["job_id"] != job_id
    finally:
        runtime.close()


def test_scheduler_rebuild_keeps_failed_task_result_route_without_replaying(tmp_path):
    runtime = build_core_runtime(tmp_path)
    try:
        aggregate = runtime.submit(task_name="restart-route", devices=["reactor-a"])
        task_id = aggregate["task"]["uuid"]
        job_id = runtime.dispatcher.dispatched[0]["job_id"]
        runtime.scheduler.on_execution_process_restarted((job_id,))
        runtime.restart_scheduler()
        assert len(runtime.dispatcher.dispatched) == 1
        assert runtime.scheduler.has_inflight_job(job_id)
        assert runtime.scheduler.workflow_snapshot(task_id)["state"] == "failed"
        assert runtime.workflow_store.get_task(task_id)["cleanup_status"] == "requires_attention"
        runtime.scheduler.on_job_finished(job_id, True, {"value": "recovered-result"})
        assert len(runtime.dispatcher.dispatched) == 1
        assert runtime.workflow_store.get_job(job_id)["status"] == "succeeded"
        assert runtime.workflow_store.get_task(task_id)["status"] == "failed"
        assert runtime.scheduler.workflow_snapshot(task_id)["state"] == "failed"
        assert runtime.workflow_store.get_task(task_id)["cleanup_status"] == "settled"
    finally:
        runtime.close()


def test_failed_unsettled_attempt_still_occupies_job_capacity(core_runtime):
    runtime = core_runtime
    _task_id, failed = _failed_transfer(runtime, "failed-capacity")
    assert runtime.workflow_store.get_job(failed)["status"] == "failed"
    waiting = runtime.persist(task_name="failed-capacity-waiter", devices=["reactor-b"])
    job_id = runtime.workflow_store.list_jobs(waiting["uuid"])[0]["uuid"]
    with runtime.workflow_store.transaction() as connection:
        decision = admit_job_dispatch(
            connection, task_uuid=waiting["uuid"], job_uuid=job_id, max_in_flight_jobs=1,
        )
    assert not decision.available
    assert decision.reason == "job_dispatch_capacity"


@pytest.mark.parametrize("start_state", ["START_COMMITTED", "UNKNOWN"])
def test_pending_uncertainty_without_original_claim_cannot_accept_execution_result(tmp_path, start_state):
    runtime = build_core_runtime(tmp_path)
    try:
        task = runtime.persist(task_name="missing-claim-result", devices=["reactor-a"])
        job_id = runtime.workflow_store.list_jobs(task["uuid"])[0]["uuid"]
        with runtime.workflow_store.transaction() as connection:
            connection.execute(
                "UPDATE workflow_node_job SET start_state=?,uncertainty_reason=? WHERE uuid=?",
                (start_state, "execution_restart_reconciliation_required", job_id),
            )
        with pytest.raises(StoreConflict, match="尚未派发"):
            TaskRuntimeProjection(runtime.workflow_store).project_job_finished(
                job_uuid=job_id, scheduler_state="success", return_info={"result": "untrusted"},
            )
        assert runtime.workflow_store.get_job(job_id)["status"] == "pending"
        assert runtime.workflow_store.get_job(job_id)["return_info"] == {}
    finally:
        runtime.close()
