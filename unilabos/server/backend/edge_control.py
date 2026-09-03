"""runtime.v1 控制面服务端：向 Edge 下发命令并接收执行事件。

该服务用于 ``--role backend`` 的进程分离模式。它实现
:class:`BackendScheduler` 的 executor 契约（``dispatch`` / ``cancel_task`` /
``add_job_finished_listener``），通过 WebSocket 发送轻量通知，并通过 HTTP
提供权威命令文档。Edge 重连后可继续处理尚未拉取的命令。
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import requests

from unilabos.protocol.base import canonical_hash
from unilabos.protocol.runtime.control import (
    BackendCommandDocument,
    BackendCommandNotice,
    BackendSessionNotice,
    CancelJobContent,
    EdgeChangeAck,
    EdgeChangeNotice,
    ExecuteJobContent,
)
from unilabos.protocol.runtime import CommandEnvelope
from unilabos.server.backend.scheduler.payloads import DispatchPayload

logger = logging.getLogger(__name__)

# listener 签名与 JobExecutionBackend 一致：(job_id, success, ret_value, suc_type)
JobFinishedListener = Callable[[str, bool, Any, str], None]

_TERMINAL_EVENT_STATUS = {
    "execution.succeeded": ("succeeded", True),
    "execution.failed": ("failed", False),
    "execution.canceled": ("canceled", False),
}


def _now_ms() -> int:
    return int(time.time() * 1000)


class EdgePayloadClient:
    """从 Edge 管理 API 拉取 durable 事件正文。"""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def fetch_json(self, payload_uuid: str) -> Optional[Any]:
        response = self.session.get(
            f"{self.base_url}/api/v1/history/payloads/{payload_uuid}",
            timeout=self.timeout,
        )
        if response.status_code != 200:
            logger.warning(
                "[EdgeControl] 拉取 payload %s 失败: HTTP %s",
                payload_uuid,
                response.status_code,
            )
            return None
        body = response.json()
        inline = body.get("inline_payload")
        if inline is None:
            return None
        raw = base64.b64decode(inline)
        encoding = str(body.get("encoding") or "utf-8")
        if encoding == "binary":
            encoding = "utf-8"
        return json.loads(raw.decode(encoding))


class EdgeControlService:
    """单 Edge 的 runtime.v1 控制面服务端与调度执行代理。"""

    def __init__(
        self,
        *,
        edge_uuid: str = "local-edge",
        edge_data_addr: str = "http://127.0.0.1:8002",
        payload_client: Optional[EdgePayloadClient] = None,
        registry_service: Optional[Any] = None,
    ) -> None:
        self.edge_uuid = edge_uuid
        self.authority_epoch = f"backend:{uuid.uuid4()}"
        # session 跨 WS 重连稳定，Edge durable outbox 依赖它恢复重放
        self.session_uuid = str(uuid.uuid4())
        self.connection_epoch = str(uuid.uuid4())
        self._payloads = payload_client or EdgePayloadClient(edge_data_addr)
        # Edge 上报的 Registry Authority 快照用于解析调度预占参数。
        self._registry_service = registry_service
        self._lock = threading.RLock()
        self._sequence = 0
        self._commands: Dict[str, BackendCommandDocument] = {}
        self._notices: Dict[str, BackendCommandNotice] = {}
        self._fetched: set[str] = set()
        self._inflight: Dict[str, Dict[str, Any]] = {}
        self._listeners: List[JobFinishedListener] = []
        self._last_edge_sequence = 0
        # WS 下行通道：线程安全队列，由 API 层的发送协程消费
        self.outgoing: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._connected = False

    # ── executor 契约（BackendScheduler 消费） ────────────────

    def add_job_finished_listener(self, listener: JobFinishedListener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_job_finished_listener(self, listener: JobFinishedListener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def dispatch(self, payload: DispatchPayload) -> None:
        """把调度器 execute_job 载荷转为 runtime.v1 控制面命令并通知 Edge。"""

        job_uuid = str(payload["job_id"])
        content = ExecuteJobContent(
            job_uuid=job_uuid,
            task_uuid=str(payload["task_id"]),
            node_uuid=str(payload["node_id"]),
            attempt_group_uuid=job_uuid,
            retry_of_job_uuid=None,
            attempt_no=1,
            device_uuid=str(payload["device_id"]),
            action_name=str(payload["action"]),
            action_type=str(payload.get("action_type") or ""),
            action_args=dict(payload.get("action_args") or {}),
            materials_need_lock=list(payload.get("materials_need_lock") or []),
            sample_material=dict(payload.get("sample_material") or {}),
            server_info=payload.get("server_info"),
            notebook_uuid=str(payload.get("notebook_id") or ""),
            inventory_requirements=list(payload.get("inventory_requirements") or []),
            inventory_reservation_uuid=payload.get("inventory_reservation_uuid"),
            scheduler_revision=int(payload.get("scheduler_revision") or 0),
        )
        with self._lock:
            self._inflight[job_uuid] = {
                "task_uuid": content.task_uuid,
                "dispatched_at_ms": _now_ms(),
            }
        self._issue_command(
            command_type="execute_job",
            job_uuid=job_uuid,
            payload=content.model_dump(mode="json", exclude_none=True),
        )

    def cancel_task(self, task_uuid: str) -> None:
        with self._lock:
            job_uuids = [
                job_uuid
                for job_uuid, info in self._inflight.items()
                if info["task_uuid"] == task_uuid
            ]
        for job_uuid in job_uuids:
            self.cancel_job(job_uuid, reason="task_canceled")

    def cancel_job(self, job_uuid: str, reason: str = "") -> None:
        content = CancelJobContent(
            adapter_command_uuid=str(uuid.uuid4()),
            reason=reason,
        )
        self._issue_command(
            command_type="cancel_job",
            job_uuid=job_uuid,
            payload=content.model_dump(mode="json", exclude_none=True),
        )

    def resolve_material_lock_parameters(
        self, device_id: str, action_name: str
    ) -> list[str]:
        """从 Edge 上报的 Registry Authority 快照解析动作锁参数。

        Edge 首次上报前镜像为空，此时返回空集也不会削弱执行侧契约：Edge
        coordinator 在 ``_dispatch`` 时会与本地注册表声明取并集，损失的只是
        Backend 预占阶段的互斥精度。
        """

        registry_service = self._registry_service
        if registry_service is None:
            # 未显式注入时取进程内 Registry Authority（随本机调度权威一起装配）。
            from unilabos.server.services.runtime.registry import get_registry_service

            registry_service = get_registry_service()
        if registry_service is None:
            return []
        try:
            return registry_service.material_lock_parameters(device_id, action_name)
        except Exception:  # noqa: BLE001 - 预占精度问题不能阻断调度
            logger.exception("[EdgeControl] 解析物料锁参数失败: %s/%s", device_id, action_name)
            return []

    def wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self._inflight:
                    return True
            time.sleep(0.05)
        return False

    def active_job_ids(self) -> list[str]:
        with self._lock:
            return list(self._inflight)

    # ── 命令签发与文档服务 ───────────────────────────────────

    def _issue_command(
        self,
        *,
        command_type: str,
        job_uuid: str,
        payload: dict[str, Any],
    ) -> str:
        payload_sha256 = canonical_hash(payload)
        command_uuid = str(uuid.uuid4())
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            envelope = CommandEnvelope(
                command_uuid=command_uuid,
                session_uuid=self.session_uuid,
                backend_sequence=sequence,
                command_type=command_type,  # type: ignore[arg-type]
                job_uuid=job_uuid,
                payload_uuid=str(uuid.uuid4()),
                payload_sha256=payload_sha256,
                received_at_ms=_now_ms(),
            )
            self._commands[command_uuid] = BackendCommandDocument(
                command=envelope,
                payload=payload,
            )
            notice = BackendCommandNotice(
                notice_uuid=str(uuid.uuid4()),
                command_uuid=command_uuid,
                command_type=command_type,  # type: ignore[arg-type]
                session_uuid=self.session_uuid,
                backend_sequence=sequence,
                edge_uuid=self.edge_uuid,
                authority_epoch=self.authority_epoch,
                connection_epoch=self.connection_epoch,
                content_sha256=payload_sha256,
                occurred_at_ms=_now_ms(),
            )
            self._notices[command_uuid] = notice
        self._queue_send(
            {
                "action": "backend_change",
                "data": notice.model_dump(mode="json", exclude_none=True),
            }
        )
        logger.info(
            "[EdgeControl] 命令已签发 %s (%s) job=%s seq=%d",
            command_uuid,
            command_type,
            job_uuid,
            sequence,
        )
        return command_uuid

    def get_command_document(self, command_uuid: str) -> Optional[dict[str, Any]]:
        with self._lock:
            document = self._commands.get(command_uuid)
            if document is not None:
                self._fetched.add(command_uuid)
        if document is None:
            return None
        return document.model_dump(mode="json", exclude_none=True)

    # ── WS 连接生命周期（API 层调用） ─────────────────────────

    def attach_connection(self) -> tuple[str, dict[str, Any]]:
        """新 WS 连接：刷新 connection_epoch，返回 (epoch, backend_session 消息)。

        后来者赢：旧连接的发送协程发现 epoch 不匹配后自行退出。
        """

        with self._lock:
            self.connection_epoch = str(uuid.uuid4())
            epoch = self.connection_epoch
            self._connected = True
            self._drain_outgoing()
            notice = BackendSessionNotice(
                session_uuid=self.session_uuid,
                edge_uuid=self.edge_uuid,
                authority_epoch=self.authority_epoch,
                connection_epoch=epoch,
                occurred_at_ms=_now_ms(),
            )
            # 断线期间签发但 Edge 尚未拉取的命令：重发轻通知
            pending = [
                self._notices[command_uuid]
                for command_uuid in self._commands
                if command_uuid not in self._fetched
            ]
        logger.info(
            "[EdgeControl] Edge 已连接 (session=%s, connection=%s, 补发命令=%d)",
            self.session_uuid,
            epoch,
            len(pending),
        )
        for notice_item in sorted(pending, key=lambda item: item.backend_sequence):
            resend = notice_item.model_copy(update={"connection_epoch": epoch})
            self.outgoing.put(
                {
                    "action": "backend_change",
                    "data": resend.model_dump(mode="json", exclude_none=True),
                }
            )
        return epoch, {
            "action": "backend_session",
            "data": notice.model_dump(mode="json", exclude_none=True),
        }

    def detach_connection(self, epoch: str) -> None:
        """断开指定代际的连接；旧代际的 detach 不影响新连接。"""

        with self._lock:
            if epoch != self.connection_epoch:
                return
            self._connected = False
            # 未消费的下行通知随连接作废：命令权威仍在 _commands，
            # 重连 attach 时按未拉取集合补发。
            self._drain_outgoing()
        logger.info("[EdgeControl] Edge 连接已断开")

    def _drain_outgoing(self) -> None:
        while True:
            try:
                self.outgoing.get_nowait()
            except queue.Empty:
                break

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def _queue_send(self, message: dict[str, Any]) -> None:
        with self._lock:
            if not self._connected:
                logger.warning(
                    "[EdgeControl] Edge 未连接，丢弃下行 %s（命令权威仍在存储中）",
                    message.get("action"),
                )
                return
        self.outgoing.put(message)

    # ── Edge 上行处理 ────────────────────────────────────────

    def handle_message(self, action: str, data: dict[str, Any]) -> Optional[dict[str, Any]]:
        """处理一条 Edge 上行消息，返回需要回发的消息（如 ack/pong）。"""

        if action == "ping":
            # Edge 的 test_latency 用 server_timestamp 估算时钟偏差；回显原字段
            # 的同时补上服务端时刻。
            return {
                "action": "pong",
                "data": {**data, "server_timestamp": time.time()},
            }
        if action == "pong":
            return None
        if action == "edge_change":
            return self._handle_edge_change(data)
        logger.warning("[EdgeControl] 忽略不支持的上行 action: %s", action)
        return None

    def _handle_edge_change(self, data: dict[str, Any]) -> dict[str, Any]:
        notice = EdgeChangeNotice.model_validate(data)
        with self._lock:
            duplicate = notice.event_sequence <= self._last_edge_sequence
            if not duplicate:
                self._last_edge_sequence = notice.event_sequence
        if not duplicate:
            try:
                self._apply_edge_event(notice)
            except Exception:  # noqa: BLE001 - 单条事件失败不阻塞 ack 推进
                logger.exception(
                    "[EdgeControl] 处理 edge 事件 %s (%s) 失败",
                    notice.event_uuid,
                    notice.event_type,
                )
        ack = EdgeChangeAck(
            session_uuid=self.session_uuid,
            through_sequence=notice.event_sequence,
            acknowledged_at_ms=_now_ms(),
        )
        return {
            "action": "edge_change_ack",
            "data": ack.model_dump(mode="json", exclude_none=True),
        }

    def _apply_edge_event(self, notice: EdgeChangeNotice) -> None:
        terminal = _TERMINAL_EVENT_STATUS.get(notice.event_type)
        if terminal is None:
            logger.debug(
                "[EdgeControl] 记录非终态事件 %s (%s)",
                notice.event_type,
                notice.aggregate_uuid,
            )
            return
        status, success = terminal
        job_uuid = notice.job_uuid or notice.aggregate_uuid
        detail: dict[str, Any] = {}
        if notice.detail_payload_uuid:
            fetched = self._payloads.fetch_json(notice.detail_payload_uuid)
            if isinstance(fetched, dict):
                detail = fetched
        # detail 形状与 coordinator.publish_job_status 存储的一致：
        # {"status", "feedback_data", "return_info"}；return_info 出自
        # serialize_result_info，与本地执行端 listener 的取值口径对齐。
        return_info = detail.get("return_info")
        if not isinstance(return_info, dict):
            return_info = {}
        ret_value = return_info.get("return_value")
        suc_type = str(return_info.get("suc_type") or "normal")
        with self._lock:
            self._inflight.pop(job_uuid, None)
            finished_commands = [
                command_uuid
                for command_uuid, document in self._commands.items()
                if document.command.job_uuid == job_uuid
            ]
            for command_uuid in finished_commands:
                self._commands.pop(command_uuid, None)
                self._notices.pop(command_uuid, None)
                self._fetched.discard(command_uuid)
            listeners = list(self._listeners)
        logger.info(
            "[EdgeControl] job %s 终态 %s (success=%s, suc_type=%s)",
            job_uuid,
            status,
            success,
            suc_type,
        )
        for listener in listeners:
            try:
                listener(job_uuid, success, ret_value, suc_type)
            except Exception:  # noqa: BLE001 - 与本地执行端一致，回调互不影响
                logger.exception("[EdgeControl] job finished listener 失败")


_service: Optional[EdgeControlService] = None
_service_lock = threading.Lock()


def set_edge_control_service(service: Optional[EdgeControlService]) -> None:
    global _service
    with _service_lock:
        _service = service


def get_edge_control_service() -> Optional[EdgeControlService]:
    return _service


__all__ = [
    "EdgeControlService",
    "EdgePayloadClient",
    "get_edge_control_service",
    "set_edge_control_service",
]
