"""Edge scheduler 与 unilab 主进程 / 云端 ws 链路的装配层（composition root）。

装配关系：

    云端 ──ws──▶ ws_client.MessageProcessor
                    │ workflow_start / workflow_cancel（整图下发，本模块注入 edge_scheduler）
                    ▼
                EdgeScheduler ──dispatch──▶ JobExecutionBackend ──send_goal──▶ HostNode
                    ▲                            │（注册进 HostNode.bridges 收执行回报）
                    └────── on_job_finished ─────┘
                    │
                    └ workflow_status 终态上报 ──▶ ws_client.send_message ──▶ 云端

main.py 在组装 bridges 时调用 ``setup_edge_scheduler``，把返回的 backend 追加进
bridges 列表即可（backend 的 ``publish_job_status`` 是 bridge 形状）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional, Tuple

from unilabos.app.scheduler.backend import JobExecutionBackend, create_edge_stack
from unilabos.app.scheduler.ordering import HttpSchedulerOrderer, StableLocalOrderer
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.utils.tracing import inject_trace_context

logger = logging.getLogger(__name__)

# 进程内单例（主进程装配一次，ws_client/api 层共享）
_scheduler: Optional[EdgeScheduler] = None
_backend: Optional[JobExecutionBackend] = None
_inventory: Optional[Any] = None
_outbox_worker: Optional[Any] = None


class CloudBusinessError(RuntimeError):
    """Cloud returned HTTP success but a non-zero ``common.Resp.code``."""

    def __init__(self, code: int, message: str, info: Optional[list[str]] = None):
        super().__init__(message)
        self.code = code
        self.info = info or []


def unwrap_cloud_response(body: object) -> Any:
    """Validate and unwrap the Go Cloud envelope without hiding business errors."""
    from unilabos.app.scheduler.inventory.schemas import CloudResponse

    envelope = CloudResponse.model_validate(body)
    if envelope.code != 0:
        message = (
            envelope.error.msg
            if envelope.error is not None
            else f"Cloud business error {envelope.code}"
        )
        info = envelope.error.info if envelope.error is not None else []
        raise CloudBusinessError(envelope.code, message, info)
    return envelope.data


def get_edge_scheduler() -> Optional[EdgeScheduler]:
    return _scheduler


def get_edge_backend() -> Optional[JobExecutionBackend]:
    return _backend


def get_inventory_service() -> Optional[Any]:
    return _inventory


def make_http_sync_sender() -> Any:
    """生产 outbox sender：批量 POST 云端 /edge/sync/events，返回 acked_sequence。

    复用 HTTPClient 的 remote_addr + Lab auth 会话；云端未部署该端点时请求会
    失败，OutboxWorker 按指数退避保留事件重试（不丢数据、自愈）。
    """
    from unilabos.app.scheduler.inventory.schemas import (
        CloudInventoryEventBatch,
        CloudSyncAck,
    )
    from unilabos.app.web.client import http_client

    def send(events: Any) -> int:
        if not events:
            raise ValueError("inventory event batch cannot be empty")
        batch = CloudInventoryEventBatch.model_validate(
            {"edge_id": events[0].get("edge_id", ""), "events": events}
        )
        trace_headers: dict[str, Any] = {}
        inject_trace_context(trace_headers)
        resp = http_client._session.post(
            f"{http_client.remote_addr}/edge/sync/events",
            json=batch.model_dump(mode="json", exclude_none=True),
            headers=trace_headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = unwrap_cloud_response(resp.json())
        return CloudSyncAck.model_validate(data).acked_sequence

    return send


def make_http_snapshot_sender(edge_id: str) -> Any:
    """Build a sender for the Cloud snapshot envelope (not the Local REST DTO)."""
    from unilabos.app.scheduler.inventory.schemas import (
        CloudInventorySnapshotRequest,
    )
    from unilabos.app.web.client import http_client

    def send(snapshot: Any) -> None:
        request = CloudInventorySnapshotRequest.from_edge_snapshot(edge_id, snapshot)
        trace_headers: dict[str, Any] = {}
        inject_trace_context(trace_headers)
        resp = http_client._session.post(
            f"{http_client.remote_addr}/edge/sync/snapshot",
            json=request.model_dump(mode="json", exclude_none=True),
            headers=trace_headers,
            timeout=30,
        )
        resp.raise_for_status()
        unwrap_cloud_response(resp.json())

    return send


def report_http_inventory_command_result(response: object) -> None:
    """POST a typed command result and validate the Cloud business envelope."""
    from unilabos.app.scheduler.inventory.schemas import (
        CloudInventoryCommandResultRequest,
        InventoryCommandResult,
    )
    from unilabos.app.web.client import http_client

    local = InventoryCommandResult.model_validate(response)
    request = CloudInventoryCommandResultRequest(
        command_id=local.command_id,
        status=local.status,
        result=local.result,
        error=local.error,
    )
    trace_headers: dict[str, Any] = {}
    inject_trace_context(trace_headers)
    resp = http_client._session.post(
        f"{http_client.remote_addr}/edge/inventory/command_result",
        json=request.model_dump(mode="json", exclude_none=True),
        headers=trace_headers,
        timeout=15,
    )
    resp.raise_for_status()
    unwrap_cloud_response(resp.json())


def _wire_inventory_ws_client(inventory: Any, ws_client: Any) -> None:
    """Expose the host-owned inventory command target without requiring scheduler."""

    message_processor = getattr(ws_client, "message_processor", None)
    if message_processor is None:
        logger.warning("[EdgeInventoryIntegration] ws_client has no message_processor")
        return
    message_processor.inventory_service = inventory


def setup_edge_inventory(
    inventory_db_path: str,
    *,
    edge_id: str = "edge-default",
    lab_id: str = "edge-lab",
    ws_client: Any = None,
    sync_sender: Any = None,
) -> Any:
    """Start the host-owned inventory service independently of EdgeScheduler.

    The SQLite connection remains private to the host process.  REST and
    HostLink expose service-level queries; slave processes never open this DB.
    """

    global _inventory, _outbox_worker
    path = str(inventory_db_path or "").strip()
    if not path:
        raise ValueError("inventory_db_path is required")
    if path != ":memory:":
        path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if _inventory is None:
        from unilabos.app.scheduler.inventory.service import InventoryService
        from unilabos.app.scheduler.inventory.store import InventoryStore

        from unilabos.app.scheduler.monitor import monitor_bus

        _inventory = InventoryService(
            InventoryStore(path),
            edge_id=edge_id,
            lab_id=lab_id,
            monitor=monitor_bus,
        )
        logger.info("[EdgeInventoryIntegration] inventory ready: %s", path)
    else:
        active_path = str(getattr(_inventory.store, "path", ""))
        if active_path and active_path != path:
            raise RuntimeError(
                f"inventory already initialized at {active_path}, cannot switch to {path}"
            )

    if ws_client is not None:
        _wire_inventory_ws_client(_inventory, ws_client)

    if sync_sender is not None and _outbox_worker is None:
        from unilabos.app.scheduler.inventory.sync import OutboxWorker

        _outbox_worker = OutboxWorker(_inventory.store, sync_sender)
        _outbox_worker.start()
    elif sync_sender is None:
        logger.info(
            "[EdgeInventoryIntegration] cloud sync disabled; outbox retained locally"
        )
    return _inventory


def setup_edge_scheduler(
    ws_client: Any = None,
    ordering_url: str = "",
    ordering_algorithm: str = "WeightedCriticalPath",
    lab_id: str = "edge-lab",
    host_node_getter: Any = None,
    inventory_db_path: str = "",
    edge_id: str = "edge-default",
    sync_sender: Any = None,
    device_state_db_path: str = "",
    workflow_history_db_path: str = "",
) -> Tuple[EdgeScheduler, JobExecutionBackend]:
    """装配 EdgeScheduler + 微后端，并接通云端 ws 链路（幂等）。

    Args:
        ws_client: WebSocketClient 实例。传入时：
            - 注入 message_processor.edge_scheduler（workflow_start/cancel 转发目标）
            - 注入 message_processor.inventory_service（inventory_command 执行目标）
            - 注册工作流终态上报（workflow_status 消息）
        ordering_url: uni-lab-scheduler 地址（空则本地稳定排序）
        inventory_db_path: Edge 仓储 SQLite 路径（空 = 不启用仓储/物料衔接）
        sync_sender: outbox 上报 callable（events → acked_sequence）；
            传入时启动 OutboxWorker，不传则事件保留在 outbox（云端端点就绪后再挂）
        device_state_db_path: 设备状态 SQLite 路径（独立于仓储/工作流库；
            空则用 ULAB_DEVICE_STATE_DB，默认 ~/.unilabos/device_state.db，
            "off" 关闭落盘）。微后端经 publish_device_status bridge 收
            HostNode 属性更新并串行写入。
        workflow_history_db_path: 工作流执行历史 SQLite 路径（第三个独立库；
            空则用 ULAB_WORKFLOW_HISTORY_DB，默认
            ~/.unilabos/workflow_history.db，"off" 关闭）。启动时把上一
            世代残留的非终态 run 标记 interrupted。
    Returns:
        (scheduler, backend)；backend 需由调用方追加进 HostNode bridges 列表。
    """
    global _scheduler, _backend, _inventory, _outbox_worker
    if _scheduler is not None and _backend is not None:
        logger.warning(
            "[EdgeSchedulerIntegration] already set up, reusing existing stack"
        )
        return _scheduler, _backend

    # 时长预估器：orderer（排序 duration）与 scheduler（泳道图/历史样本）共享
    from unilabos.app.scheduler.estimation import DurationEstimator

    estimator = DurationEstimator(
        mode=os.environ.get("ULAB_ESTIMATE_MODE", "auto").strip() or "auto",
        default_s=float(os.environ.get("ULAB_ESTIMATE_DEFAULT_S", "60")),
    )

    if ordering_url:
        orderer: Any = HttpSchedulerOrderer(
            base_url=ordering_url,
            lab_id=lab_id,
            algorithm=ordering_algorithm,
            estimator=estimator,
        )
    else:
        orderer = StableLocalOrderer()

    inventory = _inventory
    if inventory_db_path:
        inventory = setup_edge_inventory(
            inventory_db_path,
            edge_id=edge_id,
            lab_id=lab_id,
            ws_client=ws_client,
            sync_sender=sync_sender,
        )
    elif inventory is not None and ws_client is not None:
        _wire_inventory_ws_client(inventory, ws_client)

    from unilabos.app.scheduler.monitor import monitor_bus

    # 设备状态存储：独立 SQLite（与仓储/工作流库分开），归微后端管
    device_state_store = None
    state_db = device_state_db_path or os.environ.get(
        "ULAB_DEVICE_STATE_DB", "~/.unilabos/device_state.db"
    )
    state_db = state_db.strip()
    if state_db and state_db.lower() != "off":
        from unilabos.app.scheduler.device_state import DeviceStateStore

        state_db = os.path.abspath(os.path.expanduser(state_db))
        os.makedirs(os.path.dirname(state_db) or ".", exist_ok=True)
        device_state_store = DeviceStateStore(state_db)
        logger.info("[EdgeSchedulerIntegration] device state store: %s", state_db)

    # 工作流执行历史：第三个独立 SQLite（低频 append，审计/回放/跨重启）
    history_store = None
    history_db = workflow_history_db_path or os.environ.get(
        "ULAB_WORKFLOW_HISTORY_DB", "~/.unilabos/workflow_history.db"
    )
    history_db = history_db.strip()
    if history_db and history_db.lower() != "off":
        from unilabos.app.scheduler.history import WorkflowHistoryStore

        history_db = os.path.abspath(os.path.expanduser(history_db))
        os.makedirs(os.path.dirname(history_db) or ".", exist_ok=True)
        history_store = WorkflowHistoryStore(history_db)
        interrupted = history_store.mark_interrupted()
        if interrupted:
            logger.info(
                "[EdgeSchedulerIntegration] marked %d stale workflow runs interrupted",
                interrupted,
            )
        logger.info("[EdgeSchedulerIntegration] workflow history store: %s", history_db)

    scheduler, backend = create_edge_stack(
        orderer=orderer,
        host_node_getter=host_node_getter,
        inventory=inventory,
        estimator=estimator,
        monitor=monitor_bus,
        device_state_store=device_state_store,
        history=history_store,
    )
    _scheduler, _backend = scheduler, backend

    if ws_client is not None:
        _wire_ws_client(scheduler, ws_client)

    logger.info(
        "[EdgeSchedulerIntegration] edge scheduler ready (ordering=%s)",
        ordering_url or "local-stable",
    )
    return scheduler, backend


def _wire_ws_client(scheduler: EdgeScheduler, ws_client: Any) -> None:
    """把调度器接到 ws 链路：收整图消息 + 回报工作流终态。"""
    message_processor = getattr(ws_client, "message_processor", None)
    if message_processor is not None:
        message_processor.edge_scheduler = scheduler
        if _inventory is not None:
            message_processor.inventory_service = _inventory
    else:
        logger.warning("[EdgeSchedulerIntegration] ws_client has no message_processor")

    def _report_workflow_state(workflow_id: str, state: str) -> None:
        run_snapshot = scheduler.workflow_snapshot(workflow_id) or {}
        message = {
            "action": "workflow_status",
            "data": {
                "workflow_id": workflow_id,
                "task_id": run_snapshot.get("task_id", workflow_id),
                "status": state,
                "timestamp": time.time(),
            },
        }
        try:
            if message_processor is not None:
                message_processor.send_message(message)
        except Exception:  # noqa: BLE001 - 上报失败不影响调度
            logger.exception("[EdgeSchedulerIntegration] workflow_status report failed")

    scheduler.set_workflow_state_listener(_report_workflow_state)


def shutdown_edge_services() -> None:
    """Stop every process-owned Edge microbackend service and clear singletons."""

    global _scheduler, _backend, _inventory, _outbox_worker
    from unilabos.app.scheduler.host_network import shutdown_network_services

    # 先拒绝新 Slave/物料请求，再关闭请求会触达的调度与存储组件。
    shutdown_network_services()
    if _backend is not None:
        _backend.stop()
        device_state = getattr(_backend, "device_state", None)
        if device_state is not None:
            device_state.close()
    if _scheduler is not None:
        history = getattr(_scheduler, "_history", None)
        if history is not None:
            history.close()
    if _outbox_worker is not None:
        _outbox_worker.stop()
    if _inventory is not None:
        _inventory.store.close()
    _scheduler = None
    _backend = None
    _inventory = None
    _outbox_worker = None


def reset_for_test() -> None:
    """测试用：清掉进程内 Edge 微后端单例。"""

    shutdown_edge_services()


__all__ = [
    "CloudBusinessError",
    "get_edge_backend",
    "get_edge_scheduler",
    "get_inventory_service",
    "make_http_snapshot_sender",
    "make_http_sync_sender",
    "report_http_inventory_command_result",
    "reset_for_test",
    "shutdown_edge_services",
    "setup_edge_inventory",
    "setup_edge_scheduler",
    "unwrap_cloud_response",
]
