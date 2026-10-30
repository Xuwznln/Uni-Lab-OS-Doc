"""通过统一字节接口验证基础串行引擎，不调用实现私有方法。"""

import json
from copy import deepcopy
from uuid import uuid4

import pytest

from unilabos.scheduling.protocol import CONTRACT_DIR, decode_message, encode_message, validate_message
from unilabos.scheduling.queue import QueueEngine
from unilabos.scheduling.validation import validate_decision

CASES = {
    item["name"]: item["message"]
    for item in json.loads((CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_text(encoding="utf-8"))["cases"]
}


def call(engine, request):
    response = decode_message(engine.call(encode_message(request), timeout_ms=request["timeout_ms"]))
    validate_message(response)
    return response


def test_queue_describe_create_decide_close_through_public_protocol():
    engine = QueueEngine()
    description = call(engine, deepcopy(CASES["describe_request"]))
    assert description["result"]["engine_id"] == "unilab.queue"
    assert description["result"]["capabilities"] == ["core.v1", "unilab.resource_bindings.v1"]
    created = call(engine, deepcopy(CASES["create_request"]))
    assert created["ok"], created
    request = deepcopy(CASES["decide_request"])
    response = call(engine, request)
    assert response["ok"], response
    validate_decision(request, response, capabilities=["core.v1"])
    assert response["result"]["task_scope"] == ["task-A"]
    assert [item["node"] for item in response["result"]["dispatch_now"]] == [
        {"task_id": "task-A", "node_id": "node-A1"}
    ]
    close = deepcopy(CASES["close_request"])
    assert call(engine, close)["result"]["status"] == "closed"
    request["request_id"] = str(uuid4())
    assert call(engine, request)["error"]["code"] == "INSTANCE_CLOSED"


def ready_engine():
    engine = QueueEngine()
    assert call(engine, deepcopy(CASES["create_request"]))["ok"]
    return engine


def test_closed_instance_remains_idempotent_after_another_instance_is_created():
    engine = ready_engine()
    close = deepcopy(CASES["close_request"])
    assert call(engine, close)["ok"]
    other = deepcopy(CASES["create_request"])
    other["request_id"] = str(uuid4())
    other["body"]["instance_id"] = str(uuid4())
    assert call(engine, other)["ok"]
    close["request_id"] = str(uuid4())
    assert call(engine, close)["ok"]
    resurrect = deepcopy(CASES["create_request"])
    assert call(engine, resurrect)["error"]["code"] == "INSTANCE_CLOSED"


def two_task_request():
    request = deepcopy(CASES["decide_request"])
    snapshot = request["body"]["snapshot"]
    task = deepcopy(snapshot["task_view"]["tasks"][0])
    task.update(task_id="task-B", queue_seq="2", priority=100)
    node = deepcopy(snapshot["task_view"]["nodes"][0])
    node.update(ref={"task_id": "task-B", "node_id": "node-B1"}, job_id="job-B1")
    option = deepcopy(snapshot["resource_view"]["options"][0])
    option.update(option_id="option-B1", node=node["ref"])
    snapshot["task_view"]["tasks"].append(task)
    snapshot["task_view"]["nodes"].append(node)
    snapshot["resource_view"]["options"].append(option)
    return request


@pytest.mark.parametrize("status", ["running", "failed", "canceled", "succeeded"])
def test_next_task_waits_until_previous_task_is_settled(status):
    engine = ready_engine()
    request = two_task_request()
    snapshot = request["body"]["snapshot"]
    task, node = snapshot["task_view"]["tasks"][0], snapshot["task_view"]["nodes"][0]
    task["status"] = status
    node.update(status="failed", ready=False)
    response = call(engine, request)
    assert response["ok"], response
    assert response["result"]["task_scope"] == ["task-A"]
    assert response["result"]["dispatch_now"] == []
    if status != "running":
        request["request_id"] = str(uuid4())
        task["terminal_settled"] = True
        response = call(engine, request)
        assert response["ok"], response
        assert response["result"]["task_scope"] == ["task-B"]
        assert [item["node"]["task_id"] for item in response["result"]["dispatch_now"]] == ["task-B"]


def test_pause_holds_queue_even_when_other_task_has_higher_priority():
    engine = ready_engine()
    request = two_task_request()
    request["body"]["snapshot"]["task_view"]["tasks"][0].update(
        control="paused", normal_progression_allowed=False,
    )
    response = call(engine, request)
    assert response["ok"], response
    assert response["result"]["status"] == "wait"
    assert response["result"]["task_scope"] == ["task-A"]
    assert response["result"]["dispatch_now"] == []


@pytest.mark.parametrize(
    "name,status,kinds",
    [
        ("authorized_step_request", "advance", ["start"]),
        ("unauthorized_step_request", "wait", []),
        ("step_during_recovery_request", "wait", []),
        ("local_control_step_request", "advance", []),
        ("manual_admission_request", "advance", ["admit_manual"]),
        ("manual_continue_request", "advance", ["continue_attempt"]),
        ("manual_step_continuation_request", "advance", ["continue_attempt"]),
        ("execution_restart_unsettled_request", "wait", []),
        ("draining_existing_attempt_request", "wait", []),
        ("canceling_task_projection_request", "wait", []),
        ("error_waiting_projection_request", "wait", []),
        ("sync_first_single_event_request", "sync", []),
    ],
)
def test_control_and_recovery_cases(name, status, kinds):
    request = deepcopy(CASES[name])
    response = call(ready_engine(), request)
    assert response["ok"], response
    validate_decision(request, response, capabilities=["core.v1"])
    assert response["result"]["status"] == status
    assert [item["kind"] for item in response["result"]["dispatch_now"]] == kinds
    assert response["result"]["consumed_feedback_seq"] == request["body"]["feedback"]["through_seq"]


def test_rejected_candidate_does_not_advance_queue_memory():
    engine = ready_engine()
    request = two_task_request()
    first = call(engine, request)
    assert first["result"]["task_scope"] == ["task-A"]
    # 不接受候选：继续使用原 checkpoint 和原 head，不能偷偷切换队首。
    request["request_id"] = str(uuid4())
    again = call(engine, request)
    assert again["result"]["task_scope"] == ["task-A"]


def test_same_request_retries_identically_and_changed_content_conflicts():
    engine = ready_engine()
    request = deepcopy(CASES["decide_request"])
    raw = encode_message(request)
    first = engine.call(raw, timeout_ms=5000)
    assert engine.call(raw, timeout_ms=5000) == first
    request["body"]["snapshot"]["task_view"]["tasks"][0]["priority"] = 2
    response = call(engine, request)
    assert response["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_foreign_manual_inflight_can_settle_during_serial_migration():
    engine = ready_engine()
    request = deepcopy(CASES["manual_continue_request"])
    snapshot = request["body"]["snapshot"]
    task = deepcopy(snapshot["task_view"]["tasks"][0])
    task.update(task_id="earlier-task", queue_seq="0", status="pending")
    snapshot["task_view"]["tasks"].append(task)
    snapshot["head"] = None
    response = call(engine, request)
    assert response["ok"], response
    assert [item["kind"] for item in response["result"]["dispatch_now"]] == ["continue_attempt"]
    assert not any(item["kind"] in {"start", "admit_manual"} for item in response["result"]["dispatch_now"])
