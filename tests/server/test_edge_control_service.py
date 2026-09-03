"""runtime.v1 控制面服务端（--role backend 进程）与 Edge 客户端契约的往返验证。"""

from __future__ import annotations

from typing import Any

import pytest

from unilabos.protocol.base import canonical_hash
from unilabos.protocol.runtime.control import (
    BackendCommandDocument,
    BackendCommandNotice,
    BackendSessionNotice,
    CancelJobContent,
    ExecuteJobContent,
)
from unilabos.server.backend.edge_control import (
    EdgeControlService,
    get_edge_control_service,
    set_edge_control_service,
)
from unilabos.server.backend.scheduler.payloads import build_job_start_payload


class _FakePayloadClient:
    def __init__(self, payloads: dict[str, Any] | None = None) -> None:
        self.payloads = payloads or {}
        self.requested: list[str] = []

    def fetch_json(self, payload_uuid: str) -> Any:
        self.requested.append(payload_uuid)
        return self.payloads.get(payload_uuid)


def _service(payloads: dict[str, Any] | None = None) -> EdgeControlService:
    return EdgeControlService(payload_client=_FakePayloadClient(payloads))


def _dispatch_payload(job_id: str = "job-1", task_id: str = "task-1"):
    return build_job_start_payload(
        job_id=job_id,
        task_id=task_id,
        workflow_id="workflow-1",
        node_id="node-1",
        device_id="device-1",
        action_name="pick",
        action_type="UniAction",
        action_args={"volume": 5},
        materials_need_lock=["plate"],
        scheduler_revision=3,
    )


def _edge_change(
    sequence: int,
    *,
    event_type: str = "execution.succeeded",
    job_uuid: str = "job-1",
    detail_payload_uuid: str | None = "payload-1",
) -> dict[str, Any]:
    return {
        "session_uuid": "session-x",
        "event_uuid": f"event-{sequence}",
        "event_sequence": sequence,
        "event_type": event_type,
        "aggregate_type": "execution_job",
        "aggregate_uuid": job_uuid,
        "aggregate_version": sequence,
        "job_uuid": job_uuid,
        "detail_payload_uuid": detail_payload_uuid,
    }


def test_dispatch_issues_notice_and_document_that_pass_edge_validation() -> None:
    service = _service()
    epoch, session_message = service.attach_connection()

    session = BackendSessionNotice.model_validate(session_message["data"])
    assert session.session_uuid == service.session_uuid
    assert session.connection_epoch == epoch

    service.dispatch(_dispatch_payload())

    message = service.outgoing.get_nowait()
    assert message["action"] == "backend_change"
    notice = BackendCommandNotice.model_validate(message["data"])
    assert notice.command_type == "execute_job"
    assert notice.session_uuid == service.session_uuid
    assert notice.backend_sequence == 1

    # Edge 侧 handle_backend_notice 的三重校验：身份、通知哈希、命令哈希
    document = BackendCommandDocument.model_validate(
        service.get_command_document(notice.command_uuid)
    )
    assert document.command.command_uuid == notice.command_uuid
    assert canonical_hash(document.payload) == notice.content_sha256
    assert canonical_hash(document.payload) == document.command.payload_sha256

    content = ExecuteJobContent.model_validate(document.payload)
    assert content.job_uuid == "job-1"
    assert content.device_uuid == "device-1"
    assert content.action_args == {"volume": 5}
    assert content.materials_need_lock == ["plate"]
    assert content.scheduler_revision == 3
    assert service.active_job_ids() == ["job-1"]


def test_terminal_event_invokes_listener_with_local_semantics() -> None:
    detail = {
        "status": "success",
        "feedback_data": {},
        "return_info": {
            "error": "",
            "suc": True,
            "return_value": {"mass": 1.5},
            "suc_type": "normal",
        },
    }
    service = _service({"payload-1": detail})
    service.attach_connection()
    service.dispatch(_dispatch_payload())
    finished: list[tuple] = []
    service.add_job_finished_listener(
        lambda job_id, success, ret, suc_type: finished.append(
            (job_id, success, ret, suc_type)
        )
    )

    reply = service.handle_message("edge_change", _edge_change(1))

    assert reply is not None and reply["action"] == "edge_change_ack"
    assert reply["data"]["through_sequence"] == 1
    assert finished == [("job-1", True, {"mass": 1.5}, "normal")]
    assert service.active_job_ids() == []
    # 终态后命令存储被清理
    notice = service.outgoing.get_nowait()
    assert service.get_command_document(notice["data"]["command_uuid"]) is None


def test_duplicate_edge_events_ack_but_apply_once() -> None:
    service = _service({"payload-1": {"return_info": {"suc": False}}})
    service.attach_connection()
    finished: list[str] = []
    service.add_job_finished_listener(
        lambda job_id, *_args: finished.append(job_id)
    )

    first = service.handle_message(
        "edge_change", _edge_change(1, event_type="execution.failed")
    )
    second = service.handle_message(
        "edge_change", _edge_change(1, event_type="execution.failed")
    )

    assert first is not None and second is not None
    assert second["data"]["through_sequence"] == 1
    assert finished == ["job-1"]


def test_cancel_task_issues_cancel_commands_for_inflight_jobs() -> None:
    service = _service()
    service.attach_connection()
    service.dispatch(_dispatch_payload(job_id="job-1", task_id="task-9"))
    service.dispatch(_dispatch_payload(job_id="job-2", task_id="task-other"))
    service.outgoing.get_nowait()
    service.outgoing.get_nowait()

    service.cancel_task("task-9")

    message = service.outgoing.get_nowait()
    notice = BackendCommandNotice.model_validate(message["data"])
    assert notice.command_type == "cancel_job"
    document = BackendCommandDocument.model_validate(
        service.get_command_document(notice.command_uuid)
    )
    assert document.command.job_uuid == "job-1"
    CancelJobContent.model_validate(document.payload)
    with pytest.raises(Exception):
        service.outgoing.get_nowait()


def test_reconnect_replays_only_unfetched_commands() -> None:
    service = _service()
    # 未连接时 dispatch：通知被丢弃，命令仍权威保存
    service.dispatch(_dispatch_payload(job_id="job-1"))
    service.dispatch(_dispatch_payload(job_id="job-2", task_id="task-2"))
    assert service.outgoing.qsize() == 0

    epoch1, _ = service.attach_connection()
    replayed = [service.outgoing.get_nowait() for _ in range(2)]
    sequences = [item["data"]["backend_sequence"] for item in replayed]
    assert sequences == [1, 2]
    # 每条补发通知携带当前连接代际
    assert all(
        item["data"]["connection_epoch"] == epoch1 for item in replayed
    )

    # Edge 拉取第一条后再次断连重连：只补发未拉取的第二条
    service.get_command_document(replayed[0]["data"]["command_uuid"])
    service.detach_connection(epoch1)
    _epoch2, _ = service.attach_connection()
    resent = service.outgoing.get_nowait()
    assert resent["data"]["backend_sequence"] == 2
    assert service.outgoing.qsize() == 0


def test_stale_epoch_detach_does_not_break_new_connection() -> None:
    service = _service()
    old_epoch, _ = service.attach_connection()
    new_epoch, _ = service.attach_connection()

    service.detach_connection(old_epoch)
    assert service.connected

    service.detach_connection(new_epoch)
    assert not service.connected


def test_restart_coordinator_uses_edge_scope_and_inflight_view() -> None:
    from unilabos.server.backend.restart import RestartCoordinator

    service = _service()
    set_edge_control_service(service)
    try:
        coordinator = RestartCoordinator(lambda: None, lambda: None)
        assert get_edge_control_service() is service
        assert coordinator._resolve_scope() == "edge"

        service.attach_connection()
        service.dispatch(_dispatch_payload())
        assert coordinator.active_job_ids() == ["job-1"]

        with pytest.raises(ValueError):
            coordinator.request(scope="devices")
    finally:
        set_edge_control_service(None)


def test_ping_is_answered_with_pong() -> None:
    service = _service()
    reply = service.handle_message(
        "ping", {"ping_id": "p1", "client_timestamp": 1.0}
    )
    assert reply["action"] == "pong"
    # 回显 Edge 字段并补 server_timestamp，供 host test_latency 计算时钟偏差
    assert reply["data"]["ping_id"] == "p1"
    assert reply["data"]["client_timestamp"] == 1.0
    assert isinstance(reply["data"]["server_timestamp"], float)
