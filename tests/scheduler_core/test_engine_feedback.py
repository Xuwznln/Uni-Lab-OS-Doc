"""平台结果经持久日志进入统一反馈，消费重试不得复制执行或丢失事实。"""

import sqlite3

import pytest

from tests.scheduler_core.conftest import build_core_runtime
from unilabos.scheduling.queue import QueueEngine


def test_execution_result_and_task_settlement_are_replayed_once(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        aggregate = runtime.submit(task_name="feedback-result", devices=["reactor-a"])
        job_id = aggregate["jobs"][0]["uuid"]
        runtime.scheduler.on_job_finished(job_id, True, {"volume": 7})
        runtime.bridge.reschedule()
        journal = runtime.scheduler._engine_runtime.journal
        events = journal.feedback("station", after_seq="0", limit=1000)["events"]
        results = [event["payload"] for event in events if event["payload"]["kind"] == "execution_result"]
        assert len(results) == 1
        assert results[0]["attempt_id"] == job_id
        assert results[0]["result"] == {"volume": 7}
        assert results[0]["terminal_confirmed"] and results[0]["resources_settled"]
        tasks = [event["payload"]["task"] for event in events if event["payload"]["kind"] == "task_changed"]
        assert tasks[-1]["terminal_settled"]
        runtime.bridge.reschedule()
        again = journal.feedback("station", after_seq="0", limit=1000)["events"]
        assert [event for event in again if event["payload"]["kind"] == "execution_result"] == [
            event for event in events if event["payload"]["kind"] == "execution_result"
        ]
    finally:
        runtime.close()


def test_feedback_transaction_failure_keeps_source_replayable_and_execution_settled(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        aggregate = runtime.submit(task_name="feedback-rollback", devices=["reactor-a"])
        job_id = aggregate["jobs"][0]["uuid"]
        engine = runtime.scheduler._engine_runtime
        with runtime.workflow_store.transaction() as connection:
            connection.execute(
                "CREATE TRIGGER reject_result_feedback BEFORE INSERT ON scheduler_engine_feedback "
                "WHEN json_extract(NEW.event_json,'$.payload.kind')='execution_result' "
                "BEGIN SELECT RAISE(ABORT,'feedback_fault'); END"
            )
            cursor = connection.execute("SELECT source_cursor FROM scheduler_engine_domain").fetchone()[0]
        runtime.scheduler.on_job_finished(job_id, True, {"proved": True})
        assert runtime.workflow_store.get_job(job_id)["status"] == "succeeded"
        assert engine.session.last_error.code == "PERSISTENCE_FAILED"
        with runtime.workflow_store.transaction() as connection:
            assert connection.execute("SELECT source_cursor FROM scheduler_engine_domain").fetchone()[0] == cursor
            connection.execute("DROP TRIGGER reject_result_feedback")
        runtime.bridge.reschedule()
        results = [event["payload"] for event in engine.journal.feedback("station", after_seq="0", limit=1000)["events"] if event["payload"]["kind"] == "execution_result"]
        assert len(results) == 1 and results[0]["result"] == {"proved": True}
        assert engine.journal.read("station")["lifecycle"] == "engine_fault"
    finally:
        runtime.close()


def test_raw_duration_is_committed_with_result_and_replay_does_not_remeasure(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        aggregate = runtime.submit(task_name="raw-duration", devices=["reactor-a"])
        job_id = aggregate["jobs"][0]["uuid"]
        runtime.clock.advance(1.25)
        runtime.scheduler.on_job_finished(job_id, True, {"volume": 7})
        journal = runtime.scheduler._engine_runtime.journal
        events = journal.feedback("station", after_seq="0", limit=1000)["events"]
        samples = [event["payload"]["observation"] for event in events if event["payload"]["kind"] == "observation_recorded"]
        assert len(samples) == 1
        assert samples[0]["observation_id"] == job_id
        assert samples[0]["elapsed_ms"] == 1250
        assert samples[0]["parameters"] == {}
        assert samples[0]["timing_boundary"] == "dispatch_to_observed_completion"
        assert samples[0]["timing_spec_id"] is None and samples[0]["timing_spec_version"] is None
        runtime.clock.advance(100)
        runtime.scheduler.on_job_finished(job_id, True, {"volume": 7})
        runtime.bridge.reschedule()
        again = journal.feedback("station", after_seq="0", limit=1000)["events"]
        assert [event["payload"]["observation"] for event in again if event["payload"]["kind"] == "observation_recorded"] == samples
    finally:
        runtime.close()


def test_failed_sample_transaction_rolls_back_result_and_retry_keeps_observed_duration(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        aggregate = runtime.submit(task_name="raw-duration-rollback", devices=["reactor-a"])
        job_id = aggregate["jobs"][0]["uuid"]
        with runtime.workflow_store.transaction() as connection:
            connection.execute(
                "CREATE TRIGGER reject_observation BEFORE INSERT ON scheduler_engine_observation "
                "BEGIN SELECT RAISE(ABORT,'sample_fault'); END"
            )
        runtime.clock.advance(1.25)
        with pytest.raises(sqlite3.Error, match="sample_fault"):
            runtime.scheduler.on_job_finished(job_id, True, {"volume": 7})
        assert runtime.workflow_store.get_job(job_id)["status"] == "running"
        with runtime.workflow_store.transaction() as connection:
            assert connection.execute("SELECT COUNT(*) FROM scheduler_engine_observation").fetchone()[0] == 0
            connection.execute("DROP TRIGGER reject_observation")
        runtime.clock.advance(100)
        runtime.scheduler.on_job_finished(job_id, True, {"volume": 7})
        events = runtime.scheduler._engine_runtime.journal.feedback("station", after_seq="0", limit=1000)["events"]
        samples = [event["payload"]["observation"] for event in events if event["payload"]["kind"] == "observation_recorded"]
        assert len(samples) == 1 and samples[0]["elapsed_ms"] == 1250
    finally:
        runtime.close()


@pytest.mark.parametrize("excluded", ["failed", "reconstructed_start", "paused"])
def test_non_complete_execution_observations_do_not_become_training_samples(tmp_path, excluded):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        aggregate = runtime.submit(task_name="excluded-observation", devices=["reactor-a"])
        job_id = aggregate["jobs"][0]["uuid"]
        if excluded == "reconstructed_start":
            runtime.scheduler._inflight[job_id].timing_origin_observed = False
        elif excluded == "paused":
            runtime.bridge.pause(aggregate["task"]["uuid"])
        runtime.clock.advance(2)
        runtime.scheduler.on_job_finished(job_id, excluded != "failed", {})
        with runtime.workflow_store.read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM scheduler_engine_observation").fetchone()[0] == 0
    finally:
        runtime.close()
