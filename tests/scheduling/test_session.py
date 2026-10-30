"""真实队列引擎与持久宿主之间的恢复、接受和故障边界。"""

import json
from copy import deepcopy
from uuid import uuid4

import pytest

from unilabos.scheduling.protocol import CONTRACT_DIR, EngineError, decode_message, encode_message, pack_state
from unilabos.scheduling.queue import QUEUE_CONFIG, QueueEngine
from unilabos.workflow.scheduling_journal import SchedulingJournal
from unilabos.workflow.scheduling_session import SchedulingSession
from unilabos.workflow.store import WorkflowStore

CASES = {
    item["name"]: item["message"]
    for item in json.loads((CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_text(encoding="utf-8"))["cases"]
}


def test_recovery_catches_up_finitely_before_first_dispatch(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    try:
        journal = SchedulingJournal(store)
        session = SchedulingSession(
            journal, QueueEngine(), domain_id="station-1", engine_id="unilab.queue",
            config=QUEUE_CONFIG, limits={"max_feedback_events": 1},
        )
        session.start()
        snapshot = deepcopy(CASES["decide_request"]["body"]["snapshot"])
        for index in range(3):
            journal.record_feedback(
                "station-1", event_id=str(uuid4()), occurred_at_ms=index,
                payload={"kind": "snapshot_resync", "model_reset": False, "reason": {
                    "code": "RECOVERY_REQUIRED", "message": "恢复测试",
                    "task_id": None, "node": None, "resource_ids": [],
                }},
            )
        for expected in ("1", "2", "3"):
            accepted = session.decide(snapshot)
            assert accepted["decision"]["dispatch_now"] == []
            assert accepted["checkpoint"]["feedback_cursor"] == expected
        assert journal.read("station-1")["feedback_watermark"] == "3"
        session.ready()
        accepted = session.decide(snapshot)
        assert accepted["decision"]["task_scope"] == ["task-A"]
        assert len(accepted["decision"]["dispatch_now"]) == 1
        assert accepted["head"]["authorization_valid"]
        session.close()
        assert journal.read("station-1")["lifecycle"] == "draining"
        assert journal.intents("station-1")[0]["status"] == "pending"
    finally:
        store.close()


class WirePeer:
    """用真实协议对端构造不可信响应，宿主不得信任对端自报已校验。"""

    def __init__(self, mode):
        self.engine, self.mode = QueueEngine(), mode
        self.calls = []

    def call(self, request, *, timeout_ms):
        parsed = decode_message(request)
        self.calls.append(parsed["operation"])
        response = decode_message(self.engine.call(request, timeout_ms=timeout_ms))
        if parsed["operation"] == "decide":
            if self.mode == "identity":
                response["request_id"] = str(uuid4())
            elif self.mode == "state_format":
                response["result"]["next_state"] = pack_state({}, format="undeclared", max_bytes=100)
            elif self.mode == "transport":
                raise BrokenPipeError("worker stopped")
        return encode_message(response)

    def shutdown(self):
        self.engine.shutdown()


@pytest.mark.parametrize("mode,code", [
    ("identity", "INVALID_DECISION"),
    ("state_format", "INVALID_DECISION"),
    ("transport", "ENGINE_UNAVAILABLE"),
])
def test_untrusted_response_cannot_commit_checkpoint_or_leave_dispatch_open(tmp_path, mode, code):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    try:
        journal, peer = SchedulingJournal(store), WirePeer(mode)
        session = SchedulingSession(
            journal, peer, domain_id="station-1", engine_id="unilab.queue", config=QUEUE_CONFIG,
        )
        session.start()
        session.ready()
        before = journal.read("station-1")["checkpoint"]
        snapshot = deepcopy(CASES["decide_request"]["body"]["snapshot"])
        with pytest.raises(EngineError, match=code):
            session.decide(snapshot)
        assert journal.read("station-1")["checkpoint"] == before
        assert journal.read("station-1")["lifecycle"] == "engine_fault"
        assert journal.intents("station-1") == []
        calls = len(peer.calls)
        with pytest.raises(EngineError, match="ENGINE_UNAVAILABLE"):
            session.decide(snapshot)
        assert len(peer.calls) == calls
        # 故障只关闭普通推进；真实执行结果仍可进入同一持久反馈入口。
        journal.record_feedback(
            "station-1", event_id=str(uuid4()), occurred_at_ms=1,
            payload={"kind": "execution_result", "node": {"task_id": "task-old", "node_id": "node-old"},
                     "attempt_id": str(uuid4()), "outcome": "success", "terminal_confirmed": True,
                     "resources_settled": True, "unknown_command_ids": [], "result": {}, "errors": []},
        )
        assert journal.read("station-1")["feedback_watermark"] == "1"
        session.close()
        assert journal.read("station-1")["lifecycle"] == "engine_fault"
    finally:
        store.close()
