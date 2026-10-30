"""超时不得永久停站；故障会话可在进程内恢复。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.scheduling.test_session import CASES
from unilabos.scheduling.protocol import EngineError, decode_message, encode_message
from unilabos.scheduling.queue import QUEUE_CONFIG, QueueEngine
from unilabos.workflow.scheduling_journal import SchedulingJournal
from unilabos.workflow.scheduling_session import SchedulingSession
from unilabos.workflow.store import WorkflowStore


class DeadlineOnce(QueueEngine):
    def __init__(self):
        super().__init__()
        self.decides = 0

    def call(self, request, *, timeout_ms):
        parsed = decode_message(request)
        if parsed["operation"] == "decide":
            self.decides += 1
            if self.decides == 1:
                return encode_message({
                    "protocol": parsed["protocol"], "schema_version": parsed["schema_version"],
                    "operation": parsed["operation"], "request_id": parsed["request_id"],
                    "ok": False,
                    "error": {
                        "code": "DEADLINE_EXCEEDED", "message": "故意超时",
                        "origin": "engine", "retryable": True, "field_path": None, "details": {},
                    },
                })
        return super().call(request, timeout_ms=timeout_ms)


def _session(tmp_path, engine, **kwargs):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    journal = SchedulingJournal(store)
    session = SchedulingSession(
        journal, engine, domain_id="station-1", engine_id=engine.engine_id,
        config=QUEUE_CONFIG if engine.engine_id == "unilab.queue" else {},
        **kwargs,
    )
    return store, session


def test_single_deadline_does_not_permanently_fault_and_recover_works(tmp_path):
    engine = DeadlineOnce()
    store, session = _session(tmp_path, engine, max_deadline_retries=2)
    try:
        session.start()
        session.ready()
        snapshot = deepcopy(CASES["decide_request"]["body"]["snapshot"])
        with pytest.raises(EngineError, match="DEADLINE_EXCEEDED"):
            session.decide(snapshot)
        assert session.last_error is None
        accepted = session.decide(snapshot)
        assert accepted["decision"]["status"] in {"advance", "wait", "sync"}
        session.fault(EngineError("DEADLINE_EXCEEDED", "耗尽重试", retryable=True))
        with pytest.raises(EngineError, match="ENGINE_UNAVAILABLE"):
            session.decide(snapshot)
        session.recover()
        accepted = session.decide(snapshot)
        assert accepted["decision"]["dispatch_now"] or accepted["decision"]["status"] in {"advance", "wait"}
    finally:
        session.close()
        store.close()


def test_repeated_deadlines_fault_then_rebuild_restores_in_process(tmp_path):
    engine = DeadlineOnce()
    store, session = _session(tmp_path, engine, max_deadline_retries=0)
    try:
        session.start()
        session.ready()
        snapshot = deepcopy(CASES["decide_request"]["body"]["snapshot"])
        with pytest.raises(EngineError, match="DEADLINE_EXCEEDED"):
            session.decide(snapshot)
        assert session.last_error is not None
        with pytest.raises(EngineError, match="ENGINE_UNAVAILABLE"):
            session.ready()
        created = session.rebuild()
        assert created["recovery_mode"] == "rebuild"
        assert session.last_error is None
        # rebuild 写入 snapshot_resync，须先 decide 追平才能解除恢复屏障。
        accepted = session.decide(snapshot)
        while accepted["decision"]["status"] == "sync":
            accepted = session.decide(snapshot)
        session.ready()
        session.decide(snapshot)
    finally:
        session.close()
        store.close()


def test_per_call_timeout_override_reaches_the_request(tmp_path):
    seen = []

    class Capture(QueueEngine):
        def call(self, request, *, timeout_ms):
            parsed = decode_message(request)
            if parsed["operation"] == "decide":
                seen.append((parsed["timeout_ms"], parsed["body"]["compute_budget_ms"]))
            return super().call(request, timeout_ms=timeout_ms)

    store, session = _session(tmp_path, Capture(), timeout_ms=4000, compute_budget_ms=400)
    try:
        session.start()
        session.ready()
        snapshot = deepcopy(CASES["decide_request"]["body"]["snapshot"])
        session.decide(snapshot, timeout_ms=8000, compute_budget_ms=1200)
        assert seen[-1][0] >= 7000
        assert seen[-1][1] == 1200
    finally:
        session.close()
        store.close()


def test_large_queue_snapshot_does_not_fault_the_session(tmp_path):
    from tests.scheduling.workload import gen_workload, snapshot_from_workload

    tasks, devices = gen_workload(200, 5, 8, alt=1, seed=1)
    snapshot = snapshot_from_workload(tasks, devices)
    store, session = _session(tmp_path, QueueEngine())
    try:
        session.start()
        session.ready()
        accepted = session.decide(snapshot)
        assert session.last_error is None
        assert accepted["decision"]["status"] in {"advance", "wait"}
    finally:
        session.close()
        store.close()


def test_session_timeout_includes_snapshot_capture(tmp_path):
    class Capture(QueueEngine):
        def __init__(self):
            super().__init__()
            self.decides = 0

        def call(self, request, *, timeout_ms):
            parsed = decode_message(request)
            if parsed["operation"] == "decide":
                self.decides += 1
            return super().call(request, timeout_ms=timeout_ms)

    engine = Capture()
    store, session = _session(tmp_path, engine, timeout_ms=4000, max_deadline_retries=2)
    try:
        session.start()
        session.ready()
        clock = [0.0]
        session._monotonic = lambda: clock[0]
        original = session.journal.capture

        def slow_capture(*args, **kwargs):
            clock[0] += 5.0
            return original(*args, **kwargs)

        session.journal.capture = slow_capture
        snapshot = deepcopy(CASES["decide_request"]["body"]["snapshot"])
        with pytest.raises(EngineError, match="DEADLINE_EXCEEDED"):
            session.decide(snapshot)
        assert engine.decides == 0
        assert session.last_error is None
    finally:
        session.close()
        store.close()
