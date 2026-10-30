"""可信设备开始回执独立于 S；迟到和重放只补事实，不执行动作。"""

import sqlite3
from uuid import uuid4

import pytest

from tests.scheduler_core.conftest import build_core_runtime
from unilabos.scheduling.protocol import EngineError
from unilabos.scheduling.queue import QueueEngine
from unilabos.workflow.execution_started import ExecutionStartedStore
from unilabos.workflow.store import StoreConflict


def receipt(job, *, started_at_ms=1800000000250):
    return {
        "receipt_id": str(uuid4()), "job_uuid": job["uuid"],
        "command_uuid": job["edge_command_uuid"], "attempt": job["attempt"],
        "execution_instance_id": str(uuid4()), "started_at_ms": started_at_ms,
        "source_ref": "device:reactor-a:command-running-ack:42",
    }


def started_events(runtime):
    events = runtime.scheduler._engine_runtime.journal.feedback("station", after_seq="0", limit=1000)["events"]
    return [event for event in events if event["payload"]["kind"] == "execution_started"]


def test_actual_start_receipt_projects_time_once_without_replaying_execution(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        runtime.dispatcher.deferred_start = True
        task = runtime.submit(task_name="actual-start", devices=["reactor-a"])
        job_id = task["jobs"][0]["uuid"]
        control = runtime.bridge.error_control
        job = runtime.workflow_store.get_job(job_id)
        assert control.begin_execution(job_id, job["edge_command_uuid"]) == "START"
        runtime.bridge.reschedule()
        engine = runtime.scheduler._engine_runtime
        assert engine.projection.capture()["inflight"][0]["started_at_ms"] is None
        assert started_events(runtime) == []

        observed = receipt(job)
        assert control.report_execution_started(job_id, observed)["created"]
        assert not control.report_execution_started(job_id, observed)["created"]
        runtime.clock.advance(1)
        runtime.bridge.reschedule()
        current = engine.projection.capture()["inflight"][0]
        assert current["started_at_ms"] == 1800000000250
        events = started_events(runtime)
        assert len(events) == 1
        assert events[0]["payload"]["attempt_id"] == job_id
        assert events[0]["payload"]["started_at_ms"] == 1800000000250
        assert len(runtime.dispatcher.dispatched) == 1
    finally:
        runtime.close()


def test_late_start_receipt_preserves_settled_job_and_original_time(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        task = runtime.submit(task_name="late-start", devices=["reactor-a"])
        job_id = task["jobs"][0]["uuid"]
        before = runtime.workflow_store.get_job(job_id)
        observed = receipt(before)
        runtime.scheduler.on_job_finished(job_id, True, {})
        settled = runtime.workflow_store.get_job(job_id)
        runtime.bridge.error_control.report_execution_started(job_id, observed)
        runtime.bridge.reschedule()
        assert runtime.workflow_store.get_job(job_id) == settled
        assert runtime.bridge.active_or_uncertain_job_ids() == set()
        assert len(runtime.dispatcher.dispatched) == 1
        assert started_events(runtime)[0]["payload"]["started_at_ms"] == observed["started_at_ms"]
        with pytest.raises(StoreConflict, match="冲突"):
            runtime.bridge.error_control.report_execution_started(job_id, {**observed, "started_at_ms": 1800000000400})
        assert len(started_events(runtime)) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("invalid", ["not_started", "command", "attempt", "job", "time", "source"])
def test_unproven_or_mismatched_receipt_is_rejected_without_execution(tmp_path, invalid):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        runtime.dispatcher.deferred_start = True
        task = runtime.submit(task_name="invalid-start", devices=["reactor-a"])
        job_id = task["jobs"][0]["uuid"]
        job = runtime.workflow_store.get_job(job_id)
        control = runtime.bridge.error_control
        value = receipt(job)
        if invalid != "not_started":
            assert control.begin_execution(job_id, job["edge_command_uuid"]) == "START"
        if invalid == "command":
            value["command_uuid"] = str(uuid4())
        elif invalid == "attempt":
            value["attempt"] += 1
        elif invalid == "job":
            value["job_uuid"] = str(uuid4())
        elif invalid == "time":
            value["started_at_ms"] = True
        elif invalid == "source":
            value["source_ref"] = ""
        before = runtime.workflow_store.get_job(job_id)
        with pytest.raises((StoreConflict, ValueError)):
            control.report_execution_started(job_id, value)
        runtime.bridge.reschedule()
        assert runtime.workflow_store.get_job(job_id) == before
        assert started_events(runtime) == []
        assert len(runtime.dispatcher.dispatched) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("boundary", ["receipt", "feedback"])
def test_start_transaction_failure_keeps_original_receipt_replayable(tmp_path, boundary):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        task = runtime.submit(task_name="start-write-failure", devices=["reactor-a"])
        job_id = task["jobs"][0]["uuid"]
        value = receipt(runtime.workflow_store.get_job(job_id))
        control = runtime.bridge.error_control
        table = "workflow_execution_started" if boundary == "receipt" else "scheduler_engine_feedback"
        predicate = "" if boundary == "receipt" else "WHEN json_extract(NEW.event_json,'$.payload.kind')='execution_started'"
        with runtime.workflow_store.transaction() as connection:
            connection.execute(
                f"CREATE TRIGGER reject_start BEFORE INSERT ON {table} {predicate} "
                "BEGIN SELECT RAISE(ABORT,'start_write_failed'); END"
            )
        if boundary == "receipt":
            with pytest.raises(sqlite3.Error, match="start_write_failed"):
                control.report_execution_started(job_id, value)
        else:
            assert control.report_execution_started(job_id, value)["created"]
            runtime.bridge.reschedule()
        assert started_events(runtime) == []
        with runtime.workflow_store.transaction() as connection:
            connection.execute("DROP TRIGGER reject_start")
        control.report_execution_started(job_id, value)
        runtime.bridge.reschedule()
        assert len(started_events(runtime)) == 1
        assert started_events(runtime)[0]["payload"]["started_at_ms"] == value["started_at_ms"]
        assert len(runtime.dispatcher.dispatched) == 1
    finally:
        runtime.close()


def test_scheduler_restart_preserves_start_receipt_and_does_not_replay_command(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        task = runtime.submit(task_name="start-recovery", devices=["reactor-a"])
        job_id = task["jobs"][0]["uuid"]
        value = receipt(runtime.workflow_store.get_job(job_id))
        runtime.bridge.error_control.report_execution_started(job_id, value)
        runtime.bridge.reschedule()
        original = started_events(runtime)
        runtime.restart_scheduler(scheduling_engine=QueueEngine())
        assert not runtime.bridge.error_control.report_execution_started(job_id, value)["created"]
        runtime.bridge.reschedule()
        assert started_events(runtime) == original
        assert len(runtime.dispatcher.dispatched) == 1
    finally:
        runtime.close()


def test_recovery_barrier_rechecks_start_receipt_committed_after_feedback_capture(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        task = runtime.submit(task_name="start-ready-race", devices=["reactor-a"])
        job = runtime.workflow_store.get_job(task["jobs"][0]["uuid"])
        engine = runtime.scheduler._engine_runtime
        engine.journal.set_lifecycle("station", engine.session.run_epoch, "recovering")
        assert engine.refresh()
        checkpoint = engine.journal.read("station")
        assert checkpoint["checkpoint"]["feedback_cursor"] == checkpoint["feedback_watermark"]
        # 注入源事实已持久提交、异步唤醒尚未投影的切面，ready 必须独立复验。
        ExecutionStartedStore(runtime.workflow_store).record(job["uuid"], receipt(job))
        with pytest.raises(EngineError, match="FEEDBACK_GAP"):
            engine.session.ready()
        assert engine.journal.read("station")["lifecycle"] == "recovering"
        assert engine.refresh()
        engine.session.ready()
        assert engine.journal.read("station")["lifecycle"] == "running"
        assert len(started_events(runtime)) == 1
        assert len(runtime.dispatcher.dispatched) == 1
    finally:
        runtime.close()


def test_start_receipt_during_unknown_recovery_preserves_occupancy_and_start_fence(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine(), max_in_flight_jobs=1)
    try:
        task = runtime.submit(task_name="unknown-start", devices=["reactor-a"])
        job_id = task["jobs"][0]["uuid"]
        value = receipt(runtime.workflow_store.get_job(job_id))
        # 注入执行端开始确认缺失的持久切面，再走真实进程重启收敛。
        with runtime.workflow_store.transaction() as connection:
            connection.execute("UPDATE workflow_node_job SET start_state='UNKNOWN' WHERE uuid=?", (job_id,))
        runtime.scheduler.on_execution_process_restarted((job_id,))
        before = runtime.workflow_store.get_job(job_id)
        runtime.bridge.error_control.report_execution_started(job_id, value)
        runtime.bridge.reschedule()
        assert runtime.workflow_store.get_job(job_id) == before
        assert before["start_state"] == "UNKNOWN"
        assert runtime.bridge.active_or_uncertain_job_ids() == {job_id}
        assert len(started_events(runtime)) == 1
        runtime.submit(task_name="blocked-by-unknown", devices=["reactor-b"])
        assert len(runtime.dispatcher.dispatched) == 1
    finally:
        runtime.close()


def test_local_execution_backend_binds_reporter_to_original_job(tmp_path):
    from tests.app.test_edge_scheduler_backend import FakeHostNode
    from unilabos.app.scheduler.backend import JobExecutionBackend

    host = FakeHostNode({}, auto_complete=False)
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    backend.start()
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine(), execution_backend=backend)
    try:
        task = runtime.submit(task_name="local-start", devices=["reactor-a"])
        assert backend.wait_idle()
        item = host.sent_goals[0]
        assert item.job_id == task["jobs"][0]["uuid"]
        assert item.start_guard() == "START"
        assert started_events(runtime) == []
        item.execution_start_reporter(
            receipt_id=str(uuid4()), started_at_ms=1800000000100, source_ref="device:reactor-a:ack:44",
        )
        runtime.bridge.reschedule()
        assert len(started_events(runtime)) == 1
        assert len(host.sent_goals) == 1
    finally:
        backend.stop()
        runtime.close()
