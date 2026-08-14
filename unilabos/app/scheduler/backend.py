"""HostNode 侧微后端（job 执行管理层）。

定位：调度器与执行器之间的解耦层——

    EdgeScheduler（DAG 决策/排序）
        │ dispatch(job_start payload)         ↑ on_job_finished(job_id, ...)
        ▼                                     │
    JobExecutionBackend（本模块：job_start 生命周期 + 设备锁队列 + 状态回报路由）
        │ HostNode.send_goal                  ↑ publish_job_status（bridge 形状）
        ▼                                     │
    HostNode / ROS 设备执行

- 对调度器：实现 ``Dispatcher`` 协议（``dispatch``），并以 listener 回推完成事件；
  调度器不感知 HostNode/DeviceActionManager。
- 对 HostNode：实现 bridge 形状（``publish_job_status`` / ``publish_device_status``），
  注册进 ``HostNode.bridges`` 即可接收执行回报与设备属性更新；与
  ws_client.WebSocketClient 同款接口，两条链路可并存。
- 设备锁队列直接复用 ws_client.DeviceActionManager（其不依赖 WS 连接）。
- 设备状态归本微后端管：属性更新经 worker 串行写入独立的
  DeviceStateStore（SQLite WAL，与物料/工作流库分开），并向监控总线
  device 通道发 device_property 事件。
- 所有 send_goal 与完成处理都在内部 worker 线程串行执行，避免在 ROS 回调线程里
  阻塞（与 QueueProcessor.pending_starts 同样的动机）。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from unilabos.app.scheduler.dispatch import DispatchPayload
from unilabos.app.ws_client import (
    DeviceActionManager,
    JobInfo,
    JobStatus,
    QueueItem,
    format_job_log,
)
from unilabos.registry.action_policy import ERROR_DECISION_TARGET_MICRO_BACKEND
from unilabos.utils.tracing import (
    add_event,
    capture_context,
    extract_trace_context,
    inject_trace_context,
    span,
    use_context,
)

logger = logging.getLogger(__name__)

# listener 签名：(job_id, success, ret_value, suc_type) -> None
# suc_type 取值 normal / skip / operator_intervention（见 registry.action_policy）
JobFinishedListener = Callable[[str, bool, Any, str], None]

class JobExecutionBackend:
    """job_start 生命周期微后端。"""

    def __init__(
        self,
        device_manager: Optional[DeviceActionManager] = None,
        host_node_getter: Optional[Callable[[], Any]] = None,
        device_state_store: Any = None,
        monitor: Any = None,
    ):
        self.device_manager = device_manager or DeviceActionManager()
        self._host_node_getter = host_node_getter or self._default_host_getter
        self._listeners: List[JobFinishedListener] = []
        # 设备状态存储（DeviceStateStore；None = 不落盘）与监控总线
        self.device_state = device_state_store
        self._monitor = monitor

        self._events: "queue.Queue[tuple[Any, tuple]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._pending = 0
        self._pending_lock = threading.Lock()
        # cancel 与 worker.start 可能并发；tombstone 防止已取消的排队事件晚到后仍发 Goal。
        self._canceled_job_ids: Set[str] = set()
        self._canceled_job_ids_lock = threading.Lock()

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True, name="JobExecutionBackend")
        self._worker.start()

    def stop(self) -> None:
        self._running = False
        self._events.put((None, ("__stop__",)))
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """等待全部已入队事件处理完（测试/关停用）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._pending_lock:
                if self._pending == 0:
                    return True
            time.sleep(0.01)
        return False

    def _put_event(self, event: tuple, context: Any = None) -> None:
        with self._pending_lock:
            self._pending += 1
        self._events.put(
            (context if context is not None else capture_context(), event)
        )

    # ── 调度器侧接口（Dispatcher 协议） ───────────────────────

    def dispatch(self, payload: DispatchPayload) -> None:
        """接收调度器下发的 job_start 载荷：入队/直发（同 _handle_job_start 语义）。"""
        with self._canceled_job_ids_lock:
            self._canceled_job_ids.discard(payload["job_id"])
        job_info = JobInfo(
            job_id=payload["job_id"],
            task_id=payload.get("task_id", ""),
            device_id=payload["device_id"],
            notebook_id=payload.get("notebook_id", "") or "",
            action_name=payload["action"],
            device_action_key=f"/devices/{payload['device_id']}/{payload['action']}",
            status=JobStatus.QUEUE,
            start_time=time.time(),
            action_type=payload.get("action_type", ""),
            action_args=payload.get("action_args", {}) or {},
            sample_material=payload.get("sample_material", {}) or {},
            server_info=payload.get("server_info"),
            trace_context=None,
        )
        with span(
            "action.queue",
            attributes={
                "workflow.job.uuid": job_info.job_id,
                "workflow.task.uuid": job_info.task_id,
                "device.name": job_info.device_id,
                "action.name": job_info.action_name,
            },
        ) as queue_span:
            # 后续 worker 以 queue span 为父；只保存 OTel context，不保存业务 payload。
            job_info.trace_context = capture_context()
            should_start_now, _lock_became_busy = self.device_manager.enqueue_job(job_info)
            add_event(
                "action.queued",
                {"action.queue.start_immediately": should_start_now},
                span=queue_span,
            )
        job_log = format_job_log(job_info.job_id, job_info.task_id, job_info.device_id, job_info.action_name)
        if should_start_now:
            logger.info("[JobExecutionBackend] job %s start now", job_log)
            self._put_event(("start", job_info), context=job_info.trace_context)
        else:
            logger.info("[JobExecutionBackend] job %s queued", job_log)

    def cancel_job(self, job_id: str) -> bool:
        """取消排队/运行 job，并把真实执行取消传递给 HostNode。"""

        job_info = self.device_manager.get_job_info(job_id)
        if job_info is None:
            return False

        with self._canceled_job_ids_lock:
            self._canceled_job_ids.add(job_id)

        was_started = job_info.status == JobStatus.STARTED
        success, next_job, _lock_became_free = self.device_manager.cancel_job(job_id)
        if not success:
            return False

        if was_started:
            host_node = self._host_node_getter()
            if host_node is not None:
                cancel = getattr(host_node, "cancel_job", None)
                if not callable(cancel):
                    cancel = getattr(host_node, "cancel_goal", None)
                if callable(cancel):
                    try:
                        if not cancel(job_id):
                            logger.warning(
                                "[JobExecutionBackend] Host did not find job %s to cancel",
                                job_id,
                            )
                    except Exception:  # noqa: BLE001 - 状态机已取消，物理取消失败仅记录
                        logger.exception(
                            "[JobExecutionBackend] Host cancel failed for job %s",
                            job_id,
                        )

        # 清理事件排在既有 start 事件之后；既防止晚到 start 发出 Goal，也避免 tombstone 累积。
        self._put_event(("cancel_cleanup", job_id))

        if next_job is not None:
            self._put_event(("start", next_job), context=next_job.trace_context)

        self._emit_cancel_event(job_info)
        return True

    def _emit_cancel_event(self, job: JobInfo) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.emit(
                "action",
                "job_canceled",
                {
                    "job_id": job.job_id,
                    "task_id": job.task_id,
                    "device_id": job.device_id,
                    "action_name": job.action_name,
                },
            )
        except Exception:  # noqa: BLE001 - 监控故障不影响取消
            pass

    def add_job_finished_listener(self, listener: Callable[..., None]) -> None:
        """注册完成回调；兼容 3 参 (job_id, success, ret_value) 旧签名。"""
        import inspect

        try:
            params = [
                p for p in inspect.signature(listener).parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
            ]
            accepts_suc_type = any(p.kind == p.VAR_POSITIONAL for p in params) or len(params) >= 4
        except (TypeError, ValueError):
            accepts_suc_type = True
        if accepts_suc_type:
            self._listeners.append(listener)
        else:
            self._listeners.append(
                lambda job_id, success, ret_value, _suc_type: listener(job_id, success, ret_value)
            )

    def busy_device_action_keys(self) -> Set[str]:
        """当前被占用的 device_action_key（供调度器做锁视图合并）。"""
        busy: Set[str] = set()
        for job in self.device_manager.get_active_jobs():
            busy.add(job.device_action_key)
        for job in self.device_manager.get_queued_jobs():
            busy.add(job.device_action_key)
        return busy

    # ── HostNode 侧接口（bridge 形状，duck-typing） ───────────

    def publish_job_status(
        self,
        feedback_data: dict,
        item: QueueItem,
        status: str,
        return_info: Optional[dict] = None,
    ) -> None:
        """HostNode 执行回报入口（与 ws_client.WebSocketClient 同形状）。

        只处理本微后端管理的 job（其余 job_id 直接忽略，允许与云端直发链路并存）。
        """
        if self.device_manager.get_job_info(item.job_id) is None:
            return
        if status not in ("success", "failed"):
            return  # running/feedback 不推进生命周期

        ret_value = None
        suc_type = "normal"
        if isinstance(return_info, dict):
            # serialize_result_info 形状 {"error","suc","return_value","suc_type"}；
            # 对齐 Go job.ReturnInfo.Data().ReturnValue；suc_type 来自异常决策
            # （normal / skip / operator_intervention，见 registry.action_policy）
            ret_value = return_info.get("return_value")
            suc_type = str(return_info.get("suc_type") or "normal")
        parent = extract_trace_context(item.trace_context)
        self._put_event(
            ("finished", item.job_id, status == "success", ret_value, suc_type),
            context=parent,
        )

    # ── 设备状态桥（bridge 形状：publish_device_status） ──────

    def publish_device_status(self, device_status: dict, device_id: str, property_name: str) -> None:
        """HostNode 设备属性更新入口（值变化时被调，与 ws_client 同形状）。

        ROS 回调线程里只做入队，SQLite 写入由 worker 串行执行。
        """
        if self.device_state is None:
            return
        value = device_status.get(device_id, {}).get(property_name)
        if not isinstance(value, (bool, int, float, str)):
            return  # 与 HostNode.property_callback 的标量过滤口径一致
        self._put_event(("device_status", device_id, property_name, value))

    def report_device_properties(self, device_id: str, properties: Dict[str, Any]) -> Dict[str, bool]:
        """直接上报入口（REST / 非 ROS 设备）：同步写入并发监控事件。"""
        if self.device_state is None:
            raise RuntimeError("device state store not enabled")
        results: Dict[str, bool] = {}
        for prop, value in properties.items():
            results[prop] = self._write_device_property(device_id, prop, value)
        return results

    def _write_device_property(self, device_id: str, prop: str, value: Any) -> bool:
        changed = self.device_state.set(device_id, prop, value)
        if changed and self._monitor is not None:
            try:
                self._monitor.emit(
                    "device",
                    "device_property",
                    {"device_id": device_id, "property": prop, "value": value},
                )
            except Exception:  # noqa: BLE001 - 监控故障不影响状态落盘
                pass
        return changed

    # ── Host 异常决策状态的 REST 适配 ────────────────────────

    def list_error_decisions(self) -> List[Dict[str, Any]]:
        """读取 HostNode 持有的本地异常决策权威列表。"""

        host_node = self._host_node_getter()
        if host_node is None:
            return []
        return host_node.get_pending_action_error_decisions(
            decision_target=ERROR_DECISION_TARGET_MICRO_BACKEND,
        )

    def host_ready(self) -> bool:
        """HostNode 是否已经可以承接本地执行与异常决策。"""

        return self._host_node_getter() is not None

    def resolve_error_decision(self, decision_id: str, decision: Dict[str, Any]) -> bool:
        """将人工选择提交给 HostNode，不再路由到设备节点等待器。"""

        host_node = self._host_node_getter()
        if host_node is None:
            return False
        payload = {
            "decision_id": decision_id,
            **decision,
        }
        return bool(
            host_node.handle_action_error_decision(
                decision_id,
                str(payload.get("job_id") or ""),
                payload,
                decision_target=ERROR_DECISION_TARGET_MICRO_BACKEND,
            )
        )

    # ── worker ───────────────────────────────────────────────

    def _run(self) -> None:
        while self._running:
            event_context, event = self._events.get()
            if event[0] == "__stop__":
                break
            try:
                with use_context(event_context):
                    with span(
                        "action.worker",
                        attributes={"action.worker.event": event[0]},
                    ):
                        if event[0] == "start":
                            self._start_goal(event[1])
                        elif event[0] == "finished":
                            suc_type = event[4] if len(event) > 4 else "normal"
                            self._handle_finished(event[1], event[2], event[3], suc_type)
                        elif event[0] == "device_status":
                            self._write_device_property(event[1], event[2], event[3])
                        elif event[0] == "cancel_cleanup":
                            with self._canceled_job_ids_lock:
                                self._canceled_job_ids.discard(event[1])
            except Exception:  # noqa: BLE001 - worker 不允许死
                logger.exception("[JobExecutionBackend] event %s failed", event[0])
            finally:
                with self._pending_lock:
                    self._pending -= 1

    def _start_goal(self, job: JobInfo) -> None:
        with self._canceled_job_ids_lock:
            canceled = job.job_id in self._canceled_job_ids
        current_job = self.device_manager.get_job_info(job.job_id)
        if canceled or current_job is None or current_job.status != JobStatus.STARTED:
            logger.info(
                "[JobExecutionBackend] skip canceled/stale start event for job %s",
                job.job_id,
            )
            return

        job_log = format_job_log(job.job_id, job.task_id, job.device_id, job.action_name)
        queue_item = QueueItem(
            task_type="job_call_back_status",
            device_id=job.device_id,
            action_name=job.action_name,
            task_id=job.task_id,
            job_id=job.job_id,
            notebook_id=job.notebook_id,
            device_action_key=job.device_action_key,
            trace_context={},
            error_decision_target=ERROR_DECISION_TARGET_MICRO_BACKEND,
        )
        inject_trace_context(queue_item.trace_context)
        host_node = self._host_node_getter()
        if host_node is None:
            logger.error(
                "[JobExecutionBackend] HostNode unavailable for job %s",
                job_log,
            )
            self._put_event(
                ("finished", job.job_id, False, None),
            )
            return
        try:
            host_node.send_goal(
                queue_item,
                action_type=job.action_type,
                action_kwargs=job.action_args,
                sample_material=job.sample_material,
                server_info=job.server_info,
            )
            with self._canceled_job_ids_lock:
                canceled_after_send = job.job_id in self._canceled_job_ids
            if canceled_after_send:
                cancel = getattr(host_node, "cancel_job", None)
                if not callable(cancel):
                    cancel = getattr(host_node, "cancel_goal", None)
                if callable(cancel):
                    if not cancel(job.job_id):
                        logger.warning(
                            "[JobExecutionBackend] Host did not find late-canceled job %s",
                            job.job_id,
                        )
            logger.info("[JobExecutionBackend] goal sent for job %s", job_log)
        except Exception:  # noqa: BLE001 - 启动失败必须走完结流程释放锁
            logger.exception("[JobExecutionBackend] send_goal failed for job %s", job_log)
            self._put_event(
                ("finished", job.job_id, False, None),
            )

    def _handle_finished(
        self, job_id: str, success: bool, ret_value: Any, suc_type: str = "normal"
    ) -> None:
        with self._canceled_job_ids_lock:
            self._canceled_job_ids.discard(job_id)
        finished_job = self.device_manager.get_job_info(job_id)
        # 出队下一个同设备 job 并启动（锁保持 busy）
        next_job, _lock_became_free = self.device_manager.end_job(job_id)
        if next_job is not None:
            self._put_event(("start", next_job), context=next_job.trace_context)

        add_event(
            "action.finished",
            {
                "workflow.job.uuid": job_id,
                "device.name": getattr(finished_job, "device_id", ""),
                "action.name": getattr(finished_job, "action_name", ""),
                "action.success": success,
                "action.success.type": suc_type,
            },
        )

        for listener in self._listeners:
            try:
                listener(job_id, success, ret_value, suc_type)
            except Exception:  # noqa: BLE001 - 单个 listener 异常不阻断其他
                logger.exception("[JobExecutionBackend] job finished listener failed")

    @staticmethod
    def _default_host_getter() -> Any:
        from unilabos.ros.nodes.presets.host_node import HostNode

        return HostNode.get_instance(0)


def make_device_lock_resource_resolver(
    host_node_getter: Optional[Callable[[], Any]] = None,
) -> Callable[[str, str], List[str]]:
    """生产 lock_resource resolver：读取 ``@action(lock_resource=[...])`` 声明。

    查找顺序（对齐「Slave 与 Host 同注册表副本」机制）：

    1. HostNode._action_value_mappings[device_id] —— Host 侧权威副本，
       覆盖本地设备（装配时写入）与 **slave 远端设备**（main_slave_run /
       SYNC_SLAVE_NODE_INFO 上报 registry_config 时写入）；
    2. 本地设备实例 _ros_node._action_value_mappings —— Host 副本尚未
       建立时（如设备刚创建）的回退。
    """
    getter = host_node_getter or JobExecutionBackend._default_host_getter

    def _lock_from(mappings: Any, action_name: str) -> Optional[List[str]]:
        if not isinstance(mappings, dict):
            return None
        mapping = mappings.get(action_name) or mappings.get(f"auto-{action_name}")
        if not isinstance(mapping, dict):
            return None
        return list(mapping.get("lock_resource") or [])

    def resolve(device_id: str, action_name: str) -> List[str]:
        host_node = getter()
        if host_node is None:
            return []
        # ① Host 权威副本（含 slave 设备的注册表镜像）
        host_mappings = getattr(host_node, "_action_value_mappings", None) or {}
        found = _lock_from(host_mappings.get(device_id), action_name)
        if found is not None:
            return found
        # ② 本地设备实例回退
        wrapper = getattr(host_node, "devices_instances", {}).get(device_id)
        base_node = getattr(wrapper, "_ros_node", None) if wrapper is not None else None
        found = _lock_from(getattr(base_node, "_action_value_mappings", None), action_name)
        return found if found is not None else []

    return resolve


def create_edge_stack(
    orderer: Any = None,
    device_manager: Optional[DeviceActionManager] = None,
    host_node_getter: Optional[Callable[[], Any]] = None,
    inventory: Any = None,
    estimator: Any = None,
    monitor: Any = None,
    device_state_store: Any = None,
    history: Any = None,
) -> "tuple[Any, JobExecutionBackend]":
    """组装 EdgeScheduler + 微后端（composition root）。

    返回 (scheduler, backend)；backend 已 start，并需由调用方注册进
    ``HostNode.bridges``（或在测试中手动回调 ``publish_job_status``）。
    ``inventory`` 传入 InventoryService 时启用物料预留/消费衔接。
    物料锁 resolver 默认接设备 action_value_mappings 的 lock_resource 声明。
    ``estimator`` 传入 DurationEstimator 时用于泳道图预估（与 orderer 共享）。
    ``monitor`` 传入 MonitorBus 时向实时监控面板推事件。
    ``device_state_store`` 传入 DeviceStateStore 时启用设备状态落盘
    （publish_device_status bridge + REST 上报，独立 SQLite）。
    ``history`` 传入 WorkflowHistoryStore 时持久化工作流/job 执行历史
    （第三个独立 SQLite）。
    """
    from unilabos.app.scheduler.service import EdgeScheduler

    backend = JobExecutionBackend(
        device_manager=device_manager,
        host_node_getter=host_node_getter,
        device_state_store=device_state_store,
        monitor=monitor,
    )
    scheduler = EdgeScheduler(
        orderer=orderer,
        dispatcher=backend,
        busy_key_provider=backend.busy_device_action_keys,
        inventory=inventory,
        lock_resource_resolver=make_device_lock_resource_resolver(host_node_getter),
        estimator=estimator,
        monitor=monitor,
        history=history,
    )
    backend.add_job_finished_listener(scheduler.on_job_finished)
    backend.start()
    return scheduler, backend


__all__ = [
    "JobExecutionBackend",
    "JobFinishedListener",
    "create_edge_stack",
    "make_device_lock_resource_resolver",
]
