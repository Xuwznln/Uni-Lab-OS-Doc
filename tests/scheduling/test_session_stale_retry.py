"""引擎自报 STALE_DECISION 必须退避并封顶，不能无限重试。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.scheduling.test_session import CASES
from tests.scheduling.test_session_timeout_recovery import _session
from unilabos.app.scheduler.engine_runtime import stale_wakeup_delay_ms
from unilabos.scheduling.protocol import EngineError, decode_message, encode_message
from unilabos.scheduling.queue import QueueEngine
from unilabos.workflow.scheduling_session import stale_retry_delay_ms


class EngineStaleAlways(QueueEngine):
    def __init__(self):
        super().__init__()
        self.decides = 0

    def call(self, request, *, timeout_ms):
        parsed = decode_message(request)
        if parsed["operation"] == "decide":
            self.decides += 1
            return encode_message({
                "protocol": parsed["protocol"], "schema_version": parsed["schema_version"],
                "operation": parsed["operation"], "request_id": parsed["request_id"],
                "ok": False,
                "error": {
                    "code": "STALE_DECISION", "message": "引擎仍持有过期快照",
                    "origin": "engine", "retryable": True, "field_path": None, "details": {},
                },
            })
        return super().call(request, timeout_ms=timeout_ms)


def test_engine_reported_stale_faults_after_retry_cap(tmp_path):
    engine = EngineStaleAlways()
    store, session = _session(tmp_path, engine, max_stale_retries=2)
    try:
        session.start()
        session.ready()
        snapshot = deepcopy(CASES["decide_request"]["body"]["snapshot"])
        delays = []
        for _ in range(2):
            with pytest.raises(EngineError, match="STALE_DECISION") as caught:
                session.decide(snapshot)
            assert session.last_error is None
            assert caught.value.origin == "engine"
            assert caught.value.details["retry_after_ms"] >= 100
            delays.append(caught.value.details["retry_after_ms"])
        assert delays[1] >= delays[0]
        with pytest.raises(EngineError, match="STALE_DECISION"):
            session.decide(snapshot)
        assert session.last_error is not None
        assert session.last_error.code == "STALE_DECISION"
        assert session.journal.read("station-1")["lifecycle"] == "engine_fault"
        with pytest.raises(EngineError, match="ENGINE_UNAVAILABLE"):
            session.decide(snapshot)
        assert engine.decides == 3
    finally:
        session.close()
        store.close()


def test_host_detected_stale_does_not_fault_or_count_toward_cap(tmp_path):
    store, session = _session(tmp_path, QueueEngine(), max_stale_retries=0)
    try:
        session.start()
        session.ready()
        snapshot = deepcopy(CASES["decide_request"]["body"]["snapshot"])
        original = session.journal.capture

        def host_stale(*args, **kwargs):
            raise EngineError("STALE_DECISION", "事实或检查点已发生变化")

        session.journal.capture = host_stale
        with pytest.raises(EngineError, match="STALE_DECISION") as caught:
            session.decide(snapshot)
        assert caught.value.origin == "host"
        assert session.last_error is None
        session.journal.capture = original
        accepted = session.decide(snapshot)
        assert accepted["decision"]["status"] in {"advance", "wait", "sync"}
    finally:
        session.close()
        store.close()


def test_engine_stale_wakeup_backs_off_then_stops_after_fault():
    host = EngineError("STALE_DECISION", "事实已变")
    assert stale_wakeup_delay_ms(host, last_error=None) == 0
    engine = EngineError(
        "STALE_DECISION", "引擎过期", origin="engine",
        details={"retry_after_ms": stale_retry_delay_ms(2)},
    )
    assert stale_wakeup_delay_ms(engine, last_error=None) == 200
    assert stale_wakeup_delay_ms(engine, last_error=engine) is None
    assert stale_retry_delay_ms(1) == 100
    assert stale_retry_delay_ms(5) == 1600
    assert stale_retry_delay_ms(8) == 2000
