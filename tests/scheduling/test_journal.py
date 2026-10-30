"""真实 SQLite 事务上的决策提交与恢复，不模拟数据库内部方法。"""

import json
from copy import deepcopy
from uuid import uuid4

import pytest

from unilabos.scheduling.engine import reason
from unilabos.scheduling.protocol import CONTRACT_DIR, EngineError, decode_message, encode_message
from unilabos.scheduling.queue import QueueEngine
from unilabos.workflow.scheduling_journal import SchedulingJournal
from unilabos.workflow.store import WorkflowStore

CASES = {
    item["name"]: item["message"]
    for item in json.loads((CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_text(encoding="utf-8"))["cases"]
}


def _start(journal, engine):
    create = deepcopy(CASES["create_request"])
    view = journal.start(create["body"], engine_id="unilab.queue")
    create["body"]["restore"] = {
        "mode": "resume", "checkpoint": view["checkpoint"],
        "feedback_base_seq": view["checkpoint"]["feedback_cursor"], "allow_model_reset": False,
    }
    response = decode_message(engine.call(encode_message(create), timeout_ms=5000))
    assert response["ok"], response
    return create["body"]


def _decision(journal, engine, identity):
    request = deepcopy(CASES["decide_request"])
    request["request_id"] = str(uuid4())
    request["body"]["snapshot"] = journal.publish_snapshot(
        identity["domain_id"], identity["run_epoch"], request["body"]["snapshot"],
    )
    state = journal.read(identity["domain_id"])
    request["body"]["checkpoint"] = state["checkpoint"]
    request["body"]["feedback"] = journal.feedback(
        identity["domain_id"], after_seq=state["checkpoint"]["feedback_cursor"], limit=1,
    )
    response = decode_message(engine.call(encode_message(request), timeout_ms=5000))
    assert response["ok"], response
    return request, response


def test_sync_catchup_is_finite_and_checkpoint_survives_reopen(tmp_path):
    path = tmp_path / "workflow.sqlite"
    store = WorkflowStore(path)
    journal, engine = SchedulingJournal(store), QueueEngine()
    identity = _start(journal, engine)
    for i in range(3):
        journal.record_feedback(
            identity["domain_id"], event_id=str(uuid4()), occurred_at_ms=i,
            payload={"kind": "resources_changed", "resource_ids": [], "reason": {
                "code": "RESOURCE_CHANGED", "message": "测试资源变化",
                "task_id": None, "node": None, "resource_ids": [],
            }},
        )
    for expected in ("1", "2", "3"):
        request, response = _decision(journal, engine, identity)
        # 恢复的最后一批也用 sync，只消费事实，不更换旧 head。
        response["result"].update(status="sync", task_scope=None, dispatch_now=[], forecast=None)
        journal.accept(request, response, accepted_at_ms=1792400001000)
        view = journal.read(identity["domain_id"])
        assert view["checkpoint"]["feedback_cursor"] == expected
        assert view["feedback_watermark"] == "3"
    engine.shutdown()
    store.close()
    reopened = WorkflowStore(path)
    try:
        assert SchedulingJournal(reopened).read(identity["domain_id"])["checkpoint"]["feedback_cursor"] == "3"
    finally:
        reopened.close()


def test_epoch_replacement_revokes_old_candidates_but_keeps_pending_audit(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    try:
        journal, engine = SchedulingJournal(store), QueueEngine()
        identity = _start(journal, engine)
        journal.set_lifecycle(identity["domain_id"], identity["run_epoch"], "running")
        request, response = _decision(journal, engine, identity)
        receipt = journal.accept(request, response, accepted_at_ms=1792400001000)
        assert receipt["head"]["authorization_valid"]
        assert len(journal.intents(identity["domain_id"])) == 1
        replacement = {**identity, "run_epoch": str(uuid4()), "instance_id": str(uuid4())}
        journal.start(replacement, engine_id="unilab.queue")
        current = journal.read(identity["domain_id"])
        assert current["head"]["task_scope"] == ["task-A"]
        assert not current["head"]["authorization_valid"]
        assert journal.intents(identity["domain_id"])[0]["status"] == "recovery_replan"
        with pytest.raises(EngineError, match="STALE_DECISION"):
            journal.accept(request, response, accepted_at_ms=1792400001001)
    finally:
        store.close()


def test_admission_shares_transaction_and_survives_decision_expiry(tmp_path):
    now = [10.0]
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    try:
        journal = SchedulingJournal(store, monotonic=lambda: now[0])
        engine = QueueEngine()
        identity = _start(journal, engine)
        domain, epoch = identity["domain_id"], identity["run_epoch"]
        journal.set_lifecycle(domain, epoch, "running")
        request, response = _decision(journal, engine, identity)
        journal.accept(request, response, accepted_at_ms=1792400001000)
        decision = response["result"]["decision_id"]
        intent = response["result"]["dispatch_now"][0]["intent_id"]
        attempt = str(uuid4())
        binding = dict(
            run_epoch=epoch, decision_id=decision, intent_id=intent,
            attempt_id=attempt, command_ids=["command-original"], occurred_at_ms=1792400001001,
        )
        before = journal.read(domain)["feedback_watermark"]
        # 与 Job/资源准入共用的事务失败时，调度账不能单独留下已准入证据。
        with pytest.raises(RuntimeError, match="rollback"):
            with store.transaction() as connection:
                journal.admit(connection, domain, **binding)
                raise RuntimeError("rollback")
        assert journal.intents(domain)[0]["status"] == "pending"
        assert journal.read(domain)["feedback_watermark"] == before
        assert not journal.attempt_authorized(domain, epoch, attempt)
        with store.transaction() as connection:
            journal.admit(connection, domain, **binding)
        admitted_watermark = journal.read(domain)["feedback_watermark"]
        assert journal.attempt_authorized(domain, epoch, attempt)
        assert not journal.reject_intent(
            domain, intent, reason=reason("STALE_DECISION", "A 后不能撤销为未准入"),
            occurred_at_ms=1792400001002,
        )
        assert journal.read(domain)["feedback_watermark"] == admitted_watermark
        now[0] += 1000
        assert not journal.authorized(domain, epoch, decision, "task-A")
        # A 已提交，D 的有效期只约束尚未准入的候选；重复确认不重复发反馈。
        assert journal.attempt_authorized(domain, epoch, attempt)
        with store.transaction() as connection:
            journal.admit(connection, domain, **binding)
        assert journal.read(domain)["feedback_watermark"] == admitted_watermark
        journal.set_lifecycle(domain, epoch, "engine_fault")
        assert not journal.attempt_authorized(domain, epoch, attempt)
        replacement = {**identity, "run_epoch": str(uuid4()), "instance_id": str(uuid4())}
        journal.start(replacement, engine_id="unilab.queue")
        assert journal.intents(domain)[0]["attempt_id"] == attempt
        assert journal.intents(domain)[0]["command_ids"] == ["command-original"]
        assert not journal.attempt_authorized(domain, replacement["run_epoch"], attempt)
    finally:
        store.close()
