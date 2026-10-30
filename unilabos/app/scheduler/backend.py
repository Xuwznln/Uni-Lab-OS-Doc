"""HostNode 侧微后端（job 执行管理层）。

定位：调度器与执行器之间的解耦层——

    本地调度器（EdgeScheduler，DAG 决策/排序）
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
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any
from uuid import uuid4

from unilabos.app.edge_control.execution_observation import ExecutionStartReporter
from unilabos.app.edge_control.execution_start import (
    ExecutionStartCanceled,
    ExecutionStartDeferred,
    ExecutionStartUnknown,
)
from unilabos.app.job_queue import (
    DeviceActionManager,
    JobInfo,
    JobStatus,
    QueueItem,
    format_job_log,
)
from unilabos.app.scheduler.dispatch import CancelDispatchState, DispatchPayload
from unilabos.app.scheduler.execution_outcome import normalize_executor_outcome
from unilabos.registry.material_lock_schema import (
    MaterialLockSchemaError,
    compile_material_lock_schema,
)
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

# 异常决策等待人工处理的保留时长（超时的设备侧早已按 default_on_decision_timeout 自决）
_ERROR_DECISION_TTL_SECONDS = 3600.0


class JobExecutionBackend:
    """job_start 生命周期微后端。"""

    execution_process_is_local = True

    def __init__(
        self,
        device_manager: DeviceActionManager | None = None,
        host_node_getter: Callable[[], Any] | None = None,
        device_state_store: Any = None,
        monitor: Any = None,
    ):
        self.device_manager = device_manager or DeviceActionManager()
        self._host_node_getter = host_node_getter or self._default_host_getter
        self._listeners: list[JobFinishedListener] = []
        # 设备状态存储（DeviceStateStore；None = 不落盘）与监控总线
        self.device_state = device_state_store
        self._monitor = monitor

        self._events: queue.Queue[tuple[Any, tuple]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = False
        self._pending = 0
        self._pending_lock = threading.Lock()
        self.execution_control: Any = None
        self._execution_started: Callable[[str], None] | None = None
        self.deferred_start = False
        self._deferred_jobs: dict[str, JobInfo] = {}
        self._execution_instance_id = str(uuid4())

        # 等待人工决策的 action 异常（decision_id -> report）。
        # 本地模式（无云端 WS）下由调度器 REST / 前端做审批入口。
        self._error_decisions: dict[str, dict[str, Any]] = {}
        self._error_decisions_lock = threading.Lock()
        self._error_decision_required_listeners: list[
            Callable[[dict[str, Any]], None]
        ] = []

    # ── 生命周期 ─────────────────────────────────────────────

    def bind_execution_control(self, control: Any, on_started: Callable[[str], None]) -> None:
        """复用工站权威，入执行队列不再被当作设备已经开始。"""
        self.execution_control = control
        self._execution_started = on_started
        self.deferred_start = True

    def restore_dispatch(self, payload: DispatchPayload) -> None:
        """仅恢复丢失的未开始队列项，已投递给 Host 的原命令不能重复发送。"""
        if self.execution_control is None:
            raise ValueError("恢复命令缺少工站执行控制")
        with self.execution_control.lock:
            job_id = str(payload["job_id"])
            if self.execution_control.execution_start_state(job_id) != "NOT_STARTED":
                return
            if self.device_manager.get_job_info(job_id) is None:
                self.dispatch(payload)

    def _begin_execution(self, job: JobInfo) -> str:
        """由设备在实际调用前确认；许可与开始投影共享工站短临界区。"""
        with self.execution_control.lock:
            result = self.execution_control.begin_execution(job.job_id, job.command_uuid)
            if result == "START" and self._execution_started is not None:
                self._execution_started(job.job_id)
            return result

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
        execution_source = payload.get("execution_source", "workflow")
        manual = execution_source == "error_manual"
        if manual and (
            self.execution_control is None
            or not self.execution_control.is_manual(str(payload["job_id"]))
        ):
            raise ValueError("手动执行来源未被工站控制器授权")
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
            always_free=manual or bool(payload.get("always_free", False)),
            sample_material=payload.get("sample_material", {}) or {},
            server_info=payload.get("server_info"),
            trace_context=None,
            execution_source=execution_source,
            command_uuid=str(payload.get("command_uuid") or ""),
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

    def cancel(
        self,
        job_id: str,
        on_accepted: Callable[[bool], None],
    ) -> CancelDispatchState:
        """请求本地执行器停止一个工作流节点作业（WorkflowNodeJob）。

        参数：``job_id`` 是稳定作业身份；``on_accepted`` 接收 ROS 执行器对取消
        请求的明确受理结果。返回：排队作业返回 ``NOT_SENT``；已启动作业成功发起
        异步取消返回 ``REQUESTED``；目标不存在或执行器不支持确认时返回
        ``UNAVAILABLE``。异常：HostNode 异常转为 ``UNAVAILABLE`` 并记录日志。

        受理只证明执行器开始停止，不证明设备已安全停止。本方法不会结束活动作业、
        不会提升同设备队列，也不会释放设备忙碌事实；这些操作只能由随后到达的设备
        终态回报触发。
        """

        job = self.device_manager.get_job_info(job_id)
        if job is None:
            return CancelDispatchState.UNAVAILABLE
        if self.execution_control is not None:
            with self.execution_control.lock:
                if self.execution_control.execution_start_state(job_id) == "NOT_STARTED":
                    canceled, next_job, _ = self.device_manager.cancel_job(job_id)
                    self._deferred_jobs.pop(job_id, None)
                    if next_job is not None:
                        self._put_event(("start", next_job), context=next_job.trace_context)
                    return CancelDispatchState.NOT_SENT if canceled else CancelDispatchState.UNAVAILABLE
        if job.status == JobStatus.QUEUE:
            canceled, next_job, _lock_became_free = self.device_manager.cancel_job(
                job_id
            )
            if next_job is not None:
                # 排队作业取消不应提升队列；若底层状态异常，保守恢复被提升作业。
                self._put_event(("start", next_job), context=next_job.trace_context)
            return (
                CancelDispatchState.NOT_SENT
                if canceled
                else CancelDispatchState.UNAVAILABLE
            )
        if job.status != JobStatus.STARTED:
            return CancelDispatchState.UNAVAILABLE

        host_node = self._host_node_getter()
        cancel_goal = getattr(host_node, "cancel_goal", None) if host_node else None
        if not callable(cancel_goal):
            return CancelDispatchState.UNAVAILABLE
        try:
            requested = bool(cancel_goal(job_id, on_response=on_accepted))
        except Exception:
            logger.exception(
                "[JobExecutionBackend] failed to request cancel for job %s",
                job_id,
            )
            return CancelDispatchState.UNAVAILABLE
        return (
            CancelDispatchState.REQUESTED
            if requested
            else CancelDispatchState.UNAVAILABLE
        )

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

    def busy_device_action_keys(self) -> set[str]:
        """当前被占用的 device_action_key（供调度器做锁视图合并）。"""
        busy: set[str] = set()
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
        return_info: dict | None = None,
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
        effective_success = (
            normalize_executor_outcome(status, return_info) == "succeeded"
        )
        parent = extract_trace_context(item.trace_context)
        self._put_event(
            ("finished", item.job_id, effective_success, ret_value, suc_type),
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

    def report_device_properties(self, device_id: str, properties: dict[str, Any]) -> dict[str, bool]:
        """直接上报入口（REST / 非 ROS 设备）：同步写入并发监控事件。"""
        if self.device_state is None:
            raise RuntimeError("device state store not enabled")
        results: dict[str, bool] = {}
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

    # ── 异常决策桥（bridge 形状：publish_job_error_decision_required） ──

    def add_error_decision_required_listener(
        self,
        listener: Callable[[dict[str, Any]], None],
    ) -> None:
        """注册设备异常报告持久化监听器。"""

        if listener not in self._error_decision_required_listeners:
            self._error_decision_required_listeners.append(listener)

    def remove_error_decision_required_listener(
        self,
        listener: Callable[[dict[str, Any]], None],
    ) -> None:
        """幂等注销设备异常报告持久化监听器。"""

        self._error_decision_required_listeners = [
            item
            for item in self._error_decision_required_listeners
            if item != listener
        ]

    def publish_job_error_decision_required(self, report: dict[str, Any]) -> bool:
        """接收设备侧异常决策请求（本地审批通道，云端 WS 不可用时的回退）。

        base_device_node._publish_error_decision_report 会先试云端 WS，失败后
        遍历 HostNode.bridges 找到本方法。报告存内存，等 REST / 前端审批。
        """
        decision_id = str(report.get("decision_id") or "")
        if not decision_id:
            return False
        # 先形成可恢复的工作流干预事实，再允许设备进入等待。任一监听器失败都
        # 关闭本地回退，设备按既有失败策略处理，不能只留下内存审批。
        try:
            for listener in tuple(self._error_decision_required_listeners):
                listener(dict(report))
        except Exception:
            logger.exception(
                "[JobExecutionBackend] failed to persist intervention %s",
                decision_id,
            )
            return False
        now = time.time()
        with self._error_decisions_lock:
            # 清理过期请求（设备侧超时后已按 default_on_decision_timeout 自决）
            expired = [
                did for did, r in self._error_decisions.items()
                if now - r.get("_received_at", now) > _ERROR_DECISION_TTL_SECONDS
            ]
            for did in expired:
                self._error_decisions.pop(did, None)
            self._error_decisions[decision_id] = {**report, "_received_at": now}
        logger.warning(
            "[JobExecutionBackend] action error awaiting local decision: "
            "decision=%s device=%s action=%s job=%s",
            decision_id,
            report.get("device_id", ""),
            report.get("action_name", ""),
            str(report.get("job_id", ""))[:8],
        )
        return True

    def list_error_decisions(self) -> list[dict[str, Any]]:
        """当前等待人工决策的异常（供 REST / 前端展示）。"""
        with self._error_decisions_lock:
            return [
                {k: v for k, v in report.items() if not k.startswith("_")}
                for report in self._error_decisions.values()
            ]

    def resolve_error_decision(self, decision_id: str, decision: dict[str, Any]) -> bool:
        """把人工审批结果路由回挂起的设备 action（与 ws job_error_decision 同语义）。"""
        with self._error_decisions_lock:
            report = self._error_decisions.pop(decision_id, None)
        if report is None:
            return False

        device_id = str(report.get("device_id") or "")
        host_node = self._host_node_getter()
        wrapper = getattr(host_node, "devices_instances", {}).get(device_id) if host_node else None
        base_node = getattr(wrapper, "_ros_node", None) if wrapper is not None else None
        if base_node is None or not hasattr(base_node, "handle_action_error_decision"):
            logger.error(
                "[JobExecutionBackend] device %s unavailable for error decision %s",
                device_id,
                decision_id,
            )
            # 放回去，避免审批结果丢失后设备一直挂到超时才自决
            with self._error_decisions_lock:
                self._error_decisions[decision_id] = report
            return False

        payload = {
            "decision_id": decision_id,
            "job_id": str(report.get("job_id") or ""),
            "device_id": device_id,
            **decision,
        }
        matched = bool(
            base_node.handle_action_error_decision(
                decision_id, payload["job_id"], payload
            )
        )
        if not matched:
            logger.warning(
                "[JobExecutionBackend] no pending action matched decision %s "
                "(likely already timed out)",
                decision_id,
            )
        return matched

    # ── worker ───────────────────────────────────────────────

    def _run(self) -> None:
        while self._running:
            try:
                event_context, event = self._events.get(timeout=0.1)
            except queue.Empty:
                # 只重试被实际开始屏障明确拒绝的未开始动作，已受理 Goal 不重发。
                for job in list(self._deferred_jobs.values()):
                    with use_context(job.trace_context):
                        self._start_goal(job)
                continue
            if event[0] == "__stop__":
                break
            try:
                with use_context(event_context), span(
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
            except Exception:
                logger.exception("[JobExecutionBackend] event %s failed", event[0])
            finally:
                with self._pending_lock:
                    self._pending -= 1

    def _start_goal(self, job: JobInfo) -> None:
        if self.device_manager.get_job_info(job.job_id) is None:
            self._deferred_jobs.pop(job.job_id, None)
            return
        if self.execution_control is not None:
            with self.execution_control.lock:
                self._deferred_jobs.pop(job.job_id, None)
                if self.execution_control.execution_start_state(job.job_id) != "NOT_STARTED":
                    return
                if self.execution_control.recovering or (
                    job.execution_source != "error_manual"
                    and not self.execution_control.allows_task(job.task_id)
                ):
                    self._deferred_jobs[job.job_id] = job
                    return
        job_log = format_job_log(job.job_id, job.task_id, job.device_id, job.action_name)
        reporter = None
        if self.execution_control is not None:
            attempt = self.execution_control.store.get_job(job.job_id)["attempt"]
            reporter = ExecutionStartReporter(
                job.job_id, job.command_uuid, attempt, self._execution_instance_id,
                partial(self.execution_control.report_execution_started, job.job_id),
            )
        queue_item = QueueItem(
            task_type="job_call_back_status",
            device_id=job.device_id,
            action_name=job.action_name,
            task_id=job.task_id,
            job_id=job.job_id,
            notebook_id=job.notebook_id,
            device_action_key=job.device_action_key,
            trace_context={},
            execution_source=job.execution_source,
            start_guard=(lambda: self._begin_execution(job)) if self.execution_control is not None else None,
            execution_start_reporter=reporter,
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
            logger.info("[JobExecutionBackend] goal sent for job %s", job_log)
        except ExecutionStartDeferred:
            with self.execution_control.lock:
                self._deferred_jobs[job.job_id] = job
        except ExecutionStartCanceled:
            # 工站权威已保存开始前撤销；清理队列，不伪造设备执行失败。
            _, next_job, _ = self.device_manager.cancel_job(job.job_id)
            if next_job is not None:
                self._put_event(("start", next_job), context=next_job.trace_context)
        except ExecutionStartUnknown as error:
            # 保留在途身份与占用，只报告未知事实，禁止自动重发。
            self.execution_control.report_error(job.job_id, phase="dispatch_unknown", message=str(error))
        except Exception:
            logger.exception("[JobExecutionBackend] send_goal failed for job %s", job_log)
            self._put_event(
                ("finished", job.job_id, False, None),
            )

    def _handle_finished(
        self, job_id: str, success: bool, ret_value: Any, suc_type: str = "normal"
    ) -> None:
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
            except Exception:
                logger.exception("[JobExecutionBackend] job finished listener failed")

    @staticmethod
    def _default_host_getter() -> Any:
        from unilabos.ros.nodes.presets.host_node import HostNode

        # 工作流任务（WorkflowTask）恢复可早于 ROS 设备树初始化；
        # 该窗口内作业仍未越过设备边界，等待 Host 就绪后再发送。
        return HostNode.get_instance(30)


def make_device_material_lock_resolver(
    host_node_getter: Callable[[], Any] | None = None,
) -> Callable[[str, str, Mapping[str, Any]], tuple[str, ...]]:
    """构造按动作 Schema 提取物料 UUID 的本地解析器。

    Args:
        host_node_getter: 返回当前 HostNode 的函数；测试可注入隔离实现。

    Returns:
        接受设备、动作和最终参数，并返回稳定物料 UUID 集合的解析函数。

    查找顺序（对齐「Slave 与 Host 共用注册表副本」机制）：

    1. HostNode._action_value_mappings[device_id] —— Host 侧权威副本，
       覆盖本地设备（装配时写入）与 **slave 远端设备**（main_slave_run /
       SYNC_SLAVE_NODE_INFO 上报 registry_config 时写入）；
    2. 本地设备实例 _ros_node._action_value_mappings —— Host 副本尚未
       建立时（如设备刚创建）的回退。
    """
    getter = host_node_getter or JobExecutionBackend._default_host_getter

    def _mapping_from(mappings: Any, action_name: str) -> dict[str, Any] | None:
        """从一个设备的动作映射中查找公开动作条目。

        Args:
            mappings: 单个设备的 ``action_value_mappings``。
            action_name: 工作流节点引用的公开动作名。

        Returns:
            找到时返回动作条目，否则返回 ``None``。
        """

        if not isinstance(mappings, dict):
            return None
        mapping = mappings.get(action_name) or mappings.get(f"auto-{action_name}")
        if not isinstance(mapping, dict):
            return None
        return mapping

    def resolve(
        device_id: str,
        action_name: str,
        final_param: Mapping[str, Any],
    ) -> tuple[str, ...]:
        """校验最终动作参数并解析需要申请物料锁的 UUID。

        Args:
            device_id: 执行动作的设备稳定身份。
            action_name: 注册表中的公开动作名。
            final_param: 合并静态参数与上游 Handle 输出后的最终参数。

        Returns:
            去重并稳定排序的物料 UUID。

        Raises:
            MaterialLockSchemaError: 设备、动作、规范合同或最终参数不合法。
        """

        host_node = getter()
        if host_node is None:
            raise MaterialLockSchemaError(
                "material_lock_schema_missing",
                "/device",
                "无法取得设备注册表，不能安全解析动作物料锁",
            )
        # ① Host 权威副本（含 slave 设备的注册表镜像）
        host_mappings = getattr(host_node, "_action_value_mappings", None) or {}
        action_mapping = _mapping_from(host_mappings.get(device_id), action_name)
        if action_mapping is None:
            # ② 本地设备实例回退只用于 Host 副本尚未建立的启动窗口。
            wrapper = getattr(host_node, "devices_instances", {}).get(device_id)
            base_node = getattr(wrapper, "_ros_node", None) if wrapper is not None else None
            action_mapping = _mapping_from(
                getattr(base_node, "_action_value_mappings", None),
                action_name,
            )
        if action_mapping is None:
            raise MaterialLockSchemaError(
                "material_lock_schema_missing",
                f"/devices/{device_id}/actions/{action_name}",
                "设备动作没有可用的注册表 Schema",
            )
        if action_mapping.get("contract_kind") == "invalid_typed":
            diagnostic = action_mapping.get("contract_diagnostic") or {}
            raise MaterialLockSchemaError(
                "invalid_action_contract",
                str(diagnostic.get("path") or f"/actions/{action_name}"),
                str(diagnostic.get("message") or "规范动作合同编译失败"),
            )
        action_schema = action_mapping.get("schema")
        compiled_schema = compile_material_lock_schema(action_schema)
        return compiled_schema.material_lock_uuids(final_param)

    return resolve


def create_edge_stack(
    orderer: Any = None,
    device_manager: DeviceActionManager | None = None,
    host_node_getter: Callable[[], Any] | None = None,
    execution_backend: Any = None,
    inventory: Any = None,
    estimator: Any = None,
    monitor: Any = None,
    device_state_store: Any = None,
    history: Any = None,
    time_planner: Any = None,
    engine_managed: bool = False,
) -> tuple[Any, Any]:
    """组装本地调度器（EdgeScheduler）与作业执行微后端（composition root）。

    返回 (scheduler, backend)；backend 已 start，并需由调用方注册进
    ``HostNode.bridges``（或在测试中手动回调 ``publish_job_status``）。
    ``inventory`` 传入本地库存服务（InventoryService）时启用物料预留/消费衔接。
    动作物料锁（Action Material Lock）解析器默认消费注册表中的规范动作 Schema。
    ``estimator`` 传入 DurationEstimator 时用于泳道图预估（与 orderer 共享）。
    ``monitor`` 传入 MonitorBus 时向实时监控面板推事件。
    ``device_state_store`` 传入 DeviceStateStore 时启用设备状态落盘
    （publish_device_status bridge + REST 上报，独立 SQLite）。
    ``history`` 传入工作流历史存储（WorkflowHistoryStore）时持久化工作流/作业执行历史
    （第三个独立 SQLite）。

    Args:
        orderer: 本地任务排序策略。
        device_manager: 复用设备动作互斥与排队的执行管理器。
        host_node_getter: 返回当前 HostNode 的函数。
        execution_backend: 可选的 durable Edge dispatcher；省略时保留历史
            进程内 ``JobExecutionBackend``，Workspace Backend 则注入 loopback
            Edge Authority，避免跨进程时回退到直接 ROS 派发。
        inventory: 本地库存（Inventory）预留、消费和释放服务。
        estimator: 动作预计时长计算器。
        monitor: 实时监控事件输出适配器。
        device_state_store: 设备遥测状态存储。
        history: 遗留工作流执行历史存储。

    Returns:
        已互相接线并启动的本地调度器与作业执行微后端。
    """
    from unilabos.app.scheduler.service import EdgeScheduler

    backend = execution_backend or JobExecutionBackend(
        device_manager=device_manager,
        host_node_getter=host_node_getter,
        device_state_store=device_state_store,
        monitor=monitor,
    )
    device_target_resolver = None
    station_resources = (
        getattr(inventory, "station_resources", None)
        if inventory is not None
        else None
    )
    registration_reader = getattr(
        getattr(backend, "store", None),
        "latest_registration",
        None,
    )
    if station_resources is not None and callable(registration_reader):
        from unilabos.app.scheduler.device_target import (
            make_registered_device_target_resolver,
        )

        device_target_resolver = make_registered_device_target_resolver(
            station_resources,
            registration_reader,
        )
    scheduler = EdgeScheduler(
        engine_managed=engine_managed,
        orderer=orderer,
        dispatcher=backend,
        busy_key_provider=backend.busy_device_action_keys,
        inventory=inventory,
        station_resources=station_resources,
        device_target_resolver=device_target_resolver,
        estimator=estimator,
        monitor=monitor,
        history=history,
        time_planner=time_planner,
    )
    add_outcome_listener = getattr(backend, "add_job_outcome_listener", None)
    if callable(add_outcome_listener):
        # Backend-shaped Edge HTTP 保留 outcome/error_info；旧四参数回调只作为
        # ROS/内存执行适配器的兼容路径，不能覆盖保真终态。
        add_outcome_listener(scheduler.on_job_outcome)
    else:
        backend.add_job_finished_listener(scheduler.on_job_finished)
    add_feedback_listener = getattr(backend, "add_job_feedback_listener", None)
    if callable(add_feedback_listener):
        add_feedback_listener(scheduler.on_job_feedback)
    add_restarted_listener = getattr(
        backend,
        "add_execution_process_restarted_listener",
        None,
    )
    if callable(add_restarted_listener):
        add_restarted_listener(scheduler.on_execution_process_restarted)
    add_disconnected_listener = getattr(
        backend, "add_execution_connection_lost_listener", None
    )
    if callable(add_disconnected_listener):
        add_disconnected_listener(scheduler.on_execution_connection_lost)
    start = getattr(backend, "start", None)
    if callable(start):
        start()
    return scheduler, backend


__all__ = [
    "JobExecutionBackend",
    "JobFinishedListener",
    "create_edge_stack",
    "make_device_material_lock_resolver",
]
