"""统一 Backend Scheduler。

WorkflowService 持有 Workflow/Task/节点运行/attempt 事实，本服务在每轮 reconcile 中
同时计算 DAG 就绪性、完整动作/物料锁集合和库存 reservation。只有资源请求进入
``held`` 后才会下发执行；attempt 终态先落 Workflow 事实，再释放资源并重算。

两级身份：DAG 节点键 = 节点运行（``workflow_node_run.uuid``，稳定）；执行器
``job_id``、资源申请 owner、库存 reservation 都以当前 attempt（``workflow_node_job.uuid``）
为键。``retry`` 决策由 store 在同一事务里追加新 attempt，调度器拿到 ``next_job`` 后
为它重新申请资源并下发，DAG 节点不终结。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any, Dict, Optional
from uuid import UUID, uuid5

from unilabos.client.materials.core import MaterialsHTTPError
from unilabos.protocol.materials import InventoryMutation
from unilabos.protocol.materials import (
    InventoryReservationCreate,
    InventoryReservationTransition,
    InventoryTaskReservationCreate,
)
from unilabos.server.services.materials.core import MaterialsServiceError
from unilabos.server.backend.execution_queue import JOB_ORIGIN_LOCAL_SCHEDULER
from unilabos.server.backend.scheduler.payloads import build_job_start_payload
from unilabos.server.backend.scheduler.materials import (
    material_uuids_for_parameters,
)
from unilabos.server.backend.scheduler.models import (
    ActionLockClaim,
    MaterialLockClaim,
    SchedulerResourceRequest,
)
from unilabos.server.backend.scheduler.parameters import (
    ParamResolveError,
    json_get_exists,
    json_set,
)
from unilabos.server.backend.scheduler.dag.executor import DagWalk
from unilabos.server.backend.scheduler.dag.models import DagEdge, DagNode, NodeState, TaskDag
from unilabos.server.backend.scheduler.dag.runner import TaskDagRunner
from unilabos.server.backend.scheduler.resource_manager import (
    ResourceNotFound,
    SchedulerResourceManager,
)
from unilabos.server.services.runtime.workflow.service import WorkflowService

logger = logging.getLogger(__name__)

_RUN_TERMINAL = {"succeeded", "failed", "skipped", "canceled", "timeout"}


class BackendSchedulingError(RuntimeError):
    """A persisted execution plan cannot be mapped to the local executor."""


def allocation_arguments(items: Any) -> Dict[str, Dict[str, Any]]:
    """把权威的 InventoryAllocation 列表按需求 key 归并成动作参数值。

    - ``material``：一个需求恰好选出一个物料实例，值是 ResourceSlot 引用形态
      ``{"uuid": material_uuid, ...}``，框架在 send_goal 解析为 PLR 实例；
    - ``reagent``（按量计量的 lot 库存，不限于试剂）：同一需求可能按 FIFO 拆到多个
      lot，值给出合计数量与 lot 明细 ``{"quantity", "unit", "lots": [{"lot_uuid", "quantity"}]}``。
    """

    arguments: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if item.kind == "material":
            arguments[item.key] = {
                "key": item.key,
                "kind": "material",
                "uuid": item.material_uuid,
                "template_uuid": item.template_uuid,
            }
            continue
        current = arguments.setdefault(
            item.key,
            {
                "key": item.key,
                "kind": "reagent",
                "template_uuid": item.template_uuid,
                "unit": item.unit,
                "quantity": 0.0,
                "lots": [],
            },
        )
        current["quantity"] = float(current["quantity"]) + float(item.quantity or 0)
        current["lots"].append({"lot_uuid": item.lot_uuid, "quantity": float(item.quantity or 0)})
    return arguments


class BackendScheduler:
    """持久化 WorkflowTask 的唯一 DAG、资源和库存调度权威。

    同时是本机派发 job 的生命周期 owner（``job_origins``）：执行面把失败 attempt 挂起
    等待决策时通知本调度器，attempt 与节点运行随之进入 ``intervention_required``；
    终态经 finished 监听回到 :meth:`_on_executor_finished`。
    """

    job_origins = frozenset({JOB_ORIGIN_LOCAL_SCHEDULER})

    def __init__(
        self,
        workflow: WorkflowService,
        executor: Any,
        *,
        materials_gateway: Any = None,
        resource_manager: Optional[SchedulerResourceManager] = None,
        materials_need_lock_resolver: Optional[
            Callable[[str, str], list[str]]
        ] = None,
    ) -> None:
        self.workflow = workflow
        self.executor = executor
        self.materials_gateway = materials_gateway
        self.resources = resource_manager or SchedulerResourceManager()
        self._materials_need_lock_resolver = materials_need_lock_resolver
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()
        self._guard = threading.RLock()
        self._runners: Dict[str, TaskDagRunner] = {}
        self._scheduled: set[str] = set()
        # 以下均以节点运行 uuid（DAG 节点键）为键
        self._run_to_task: Dict[str, str] = {}
        self._run_specs: Dict[str, Dict[str, Any]] = {}
        self._run_context: Dict[str, tuple[Dict[str, Any], DagNode]] = {}
        # 以下以当前 attempt 的 job uuid 为键（资源 owner / 执行器 job_id）
        self._job_runs: Dict[str, str] = {}
        self._waiting_resource_jobs: Dict[str, tuple[Dict[str, Any], DagNode]] = {}
        self._dispatched_jobs: set[str] = set()
        # 已建立 durable 人工确认单、但尚未收到决策的 attempt。
        self._manual_confirmation_jobs: set[str] = set()
        self._dispatch_paused = False
        self.executor.add_job_finished_listener(self._on_executor_finished)
        resolver = getattr(self.workflow, "set_manual_confirmation_resolver", None)
        if callable(resolver):
            resolver(self._on_manual_confirmation_decided)

    @property
    def dispatch_paused(self) -> bool:
        return self._dispatch_paused

    def pause_dispatch(self) -> None:
        """暂停新 Job 派发（安静点重启用）；已派发的 Job 不受影响。"""
        with self._guard:
            self._dispatch_paused = True

    def resume_dispatch(self) -> None:
        """恢复派发并立即重算等待集合，被闸门拦下的 Job 原样继续。"""
        with self._guard:
            if not self._dispatch_paused:
                return
            self._dispatch_paused = False
        self._reconcile_resources()

    def start(self, *, recover: bool = True) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self._started.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="BackendScheduler",
                daemon=True,
            )
            self._thread.start()
        if not self._started.wait(timeout=5):
            raise RuntimeError("backend scheduler event loop did not start")
        if recover:
            for task in self.workflow.list_recoverable_workflow_tasks():
                self.submit(str(task["uuid"]))

    def submit(self, task_uuid: str) -> None:
        """Queue a persisted task; duplicate submissions share one active runner."""

        self.start(recover=False)
        assert self._loop is not None
        with self._guard:
            if task_uuid in self._runners or task_uuid in self._scheduled:
                return
            self._scheduled.add(task_uuid)
        future = asyncio.run_coroutine_threadsafe(self.run_task(task_uuid), self._loop)

        def report(done: Any) -> None:
            with self._guard:
                self._scheduled.discard(task_uuid)
            try:
                done.result()
            except Exception:  # noqa: BLE001 - task state is persisted by run_task
                logger.exception("workflow task %s execution failed", task_uuid)

        future.add_done_callback(report)

    async def run_task(self, task_uuid: str) -> Dict[str, NodeState]:
        prepared = self.workflow.prepare_workflow_task_execution(task_uuid)
        if prepared["state"] != "ready":
            return {}
        task = prepared["task"]
        runs = prepared["runs"]
        try:
            dag, specs = self._build_dag(task, runs)
            self._reserve_task_inventory(task, specs)
        except Exception as exc:
            # 计划不可执行（设备/动作缺失、库存不足…）是调度的正常业务终态：
            # 任务落 failed + plan_not_executable，不当成进程异常向上抛。
            self._fail_unstarted_task(task_uuid, runs, exc)
            self._release_unconsumed_task_inventory(task_uuid)
            if isinstance(exc, (BackendSchedulingError, MaterialsServiceError, MaterialsHTTPError)):
                logger.warning("workflow task %s cannot start: %s", task_uuid, exc)
            else:
                logger.exception("workflow task %s failed while planning", task_uuid)
            return {}

        completed = [
            str(run["uuid"])
            for run in runs
            if run["status"] in {"succeeded", "skipped"}
        ]
        walk = DagWalk(dag, completed=completed)
        runner = TaskDagRunner(
            dag,
            lambda node: self._start_node(task, node),
            on_node_terminal=self._on_node_terminal,
            on_cancel_remaining=lambda: self._cancel_task(task_uuid),
            loop=asyncio.get_running_loop(),
            walk=walk,
        )
        with self._guard:
            if task_uuid in self._runners:
                return {}
            self._runners[task_uuid] = runner
            self._run_specs.update(specs)
            for run_uuid in specs:
                self._run_to_task[run_uuid] = task_uuid
        try:
            result = await runner.run()
            for run_uuid, state in result.items():
                self._persist_terminal_if_needed(run_uuid, state)
            task_status = (
                "succeeded"
                if result and all(state == NodeState.SUCCESS for state in result.values())
                else (
                    "failed"
                    if any(state == NodeState.FAILED for state in result.values())
                    else "canceled"
                )
            )
            # 节点输出取节点运行投影 = 当前（重试后的）attempt 结果
            output = {
                str(run["workflow_node_uuid"]): dict(run.get("return_info") or {})
                for run in self.workflow.list_workflow_node_runs(task_uuid)
                if run["status"] in {"succeeded", "skipped"}
            }
            self.workflow.finish_workflow_task(
                task_uuid,
                status=task_status,
                output=output,
                error_info=(
                    []
                    if task_status == "succeeded"
                    else [{"code": "node_execution_failed"}]
                ),
            )
            return result
        finally:
            self._cleanup_task_resources(task_uuid)
            self._release_unconsumed_task_inventory(task_uuid)
            with self._guard:
                self._runners.pop(task_uuid, None)
                for run_uuid in specs:
                    spec = self._run_specs.pop(run_uuid, {})
                    self._run_to_task.pop(run_uuid, None)
                    self._run_context.pop(run_uuid, None)
                    for job_uuid in spec.get("job_uuids", ()):
                        self._job_runs.pop(job_uuid, None)
                        self._waiting_resource_jobs.pop(job_uuid, None)
                        self._dispatched_jobs.discard(job_uuid)
                        self._manual_confirmation_jobs.discard(job_uuid)

    def stop(self) -> None:
        with self._guard:
            runners = list(self._runners.items())
            loop = self._loop
            # 人工确认是可跨进程恢复的 pending 事实；优雅停机时不要把它
            # 误转成 canceled，下一进程会按 job_uuid 幂等读回原确认单。
            manual_task_ids = {
                task_uuid
                for job_uuid in self._manual_confirmation_jobs
                if (run_uuid := self._job_runs.get(job_uuid)) is not None
                if (task_uuid := self._run_to_task.get(run_uuid)) is not None
            }
        for task_uuid, runner in runners:
            if task_uuid not in manual_task_ids:
                runner.cancel()
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        remove = getattr(self.executor, "remove_job_finished_listener", None)
        if callable(remove):
            remove(self._on_executor_finished)
        self._thread = None
        self._loop = None

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._started.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    def _build_dag(
        self,
        task: Dict[str, Any],
        runs: list[Dict[str, Any]],
    ) -> tuple[TaskDag, Dict[str, Dict[str, Any]]]:
        plan = task.get("execution_plan") or {}
        snapshot = task.get("workflow_snapshot") or {}
        snapshot_nodes = {
            str(node["uuid"]): node for node in snapshot.get("nodes", [])
        }
        planned_nodes = {
            str(node["uuid"]): node for node in plan.get("nodes", [])
        }
        runs_by_node = {str(run["workflow_node_uuid"]): run for run in runs}
        scheduler_revision = int(
            (task.get("meta_data") or {}).get("scheduler_revision") or 1
        )
        dag_nodes: Dict[str, DagNode] = {}
        specs: Dict[str, Dict[str, Any]] = {}
        for workflow_node_uuid, run in runs_by_node.items():
            planned = planned_nodes.get(workflow_node_uuid, {})
            source = snapshot_nodes.get(workflow_node_uuid, {})
            if run["executor_kind"] != "device_action":
                raise BackendSchedulingError(
                    f"executor_kind {run['executor_kind']!r} is not wired locally"
                )
            param = dict(run.get("param") or planned.get("param") or {})
            source_meta = dict(source.get("meta_data") or {})
            device_id = str(
                source_meta.get("target_device_id")
                or run.get("material_uuid")
                or planned.get("material_uuid")
                or source.get("material_uuid")
                or param.get("device_id")
                or ""
            )
            action = str(source.get("action_name") or param.get("action") or "")
            if not device_id or not action:
                raise BackendSchedulingError(
                    f"workflow node {workflow_node_uuid} lacks material_uuid/device action"
                )
            run_uuid = str(run["uuid"])
            policy = run.get("execution_policy") or {}
            dag_nodes[run_uuid] = DagNode(
                node_id=run_uuid,
                device_id=device_id,
                action=action,
                action_type=str(source.get("action_type") or ""),
                action_args=param,
                always_free=bool(policy.get("always_free")),
            )
            specs[run_uuid] = {
                "workflow_node_uuid": workflow_node_uuid,
                # 节点显式声明优先；未声明时派发前按注册表 @action(always_free) 解析
                "always_free_policy": policy.get("always_free"),
                "base_param": param,
                "edges": list(plan.get("edges") or []),
                "runs_by_node": {
                    node_uuid: str(node_run["uuid"])
                    for node_uuid, node_run in runs_by_node.items()
                },
                "inventory_requirements": list(
                    planned.get("inventory_requirements") or []
                ),
                "reserved_material_uuids": [],
                "scheduler_revision": scheduler_revision,
                # 当前 attempt：执行器 job_id / 资源 owner / 库存 reservation 的键
                "current_job_uuid": str(run["current_job_uuid"]),
                "attempt_no": int(run.get("attempt_count") or 1),
                "job_uuids": [str(run["current_job_uuid"])],
            }
            manual_meta = source_meta.get("manual_confirm")
            if isinstance(manual_meta, dict):
                specs[run_uuid]["manual_confirm"] = dict(manual_meta)

        dag_edges = []
        seen_edges: set[tuple[str, str]] = set()

        def add_dag_edge(source_node: str, target_node: str) -> None:
            source_run = runs_by_node.get(source_node)
            target_run = runs_by_node.get(target_node)
            if source_run is None or target_run is None:
                return
            key = (str(source_run["uuid"]), str(target_run["uuid"]))
            if key in seen_edges or key[0] == key[1]:
                return
            seen_edges.add(key)
            dag_edges.append(
                DagEdge(source_node_uuid=key[0], target_node_uuid=key[1])
            )

        for edge in plan.get("edges") or []:
            add_dag_edge(
                str(edge["source_node_uuid"]), str(edge["target_node_uuid"])
            )
        # execution_policy.depends_on：纯执行序依赖（@workflow 声明式步骤等
        # 无 handle 数据流的节点用它表达串行），与 handle 边合并去重。
        for workflow_node_uuid, run in runs_by_node.items():
            depends_on = (run.get("execution_policy") or {}).get("depends_on") or []
            if not isinstance(depends_on, list):
                continue
            for upstream in depends_on:
                add_dag_edge(str(upstream), workflow_node_uuid)
        return (
            TaskDag(
                task_id=str(task["uuid"]),
                notebook_id="",
                server_info={},
                nodes=dag_nodes,
                edges=dag_edges,
            ),
            specs,
        )

    def _start_node(self, task: Dict[str, Any], node: DagNode) -> None:
        """为节点运行的当前 attempt 申请完整资源集合；``held`` 即下发。"""

        spec = self._run_specs[node.node_id]
        args = self._resolve_action_args(node.node_id)
        # InventoryRequirement 是节点上的声明；权威预留后解析出的具体出库内容
        # （物料 uuid / lot 与数量）按需求 key 注入同名动作参数，设备拿到的已是具体引用。
        args.update(spec.get("inventory_allocations") or {})
        parameter_names = self._material_lock_parameters(
            node.device_id,
            node.action,
        )
        material_uuids = set(
            material_uuids_for_parameters(parameter_names, args)
        )
        material_uuids.update(spec.get("reserved_material_uuids") or ())
        spec["resolved_action_args"] = args
        spec["materials_need_lock"] = parameter_names
        spec["always_free"] = self._action_always_free(node, spec)
        job_uuid = spec["current_job_uuid"]
        # 资源申请以当前 attempt 为 owner：acquire 对同一 owner 幂等重放，重试
        # 的新 attempt 必须是新的申请才能重新排队获取。
        record = self.resources.acquire(
            SchedulerResourceRequest(
                request_uuid=f"resource:{job_uuid}",
                owner_uuid=job_uuid,
                task_uuid=str(task["uuid"]),
                current_action=ActionLockClaim(
                    device_id=node.device_id,
                    action_name=node.action,
                ),
                always_free=spec["always_free"],
                material_lock_claims=[
                    MaterialLockClaim(material_uuid=material_uuid)
                    for material_uuid in sorted(material_uuids)
                ],
            )
        )
        with self._guard:
            self._run_context[node.node_id] = (task, node)
            self._job_runs[job_uuid] = node.node_id
            self._waiting_resource_jobs[job_uuid] = (task, node)
        if record.status == "held":
            self._dispatch_held_node(task, node)

    def _executor_ready(self) -> bool:
        """执行适配器（HostLink / ROS2 host node）是否已注册；未就绪时不派发。"""

        ready = getattr(self.executor, "host_ready", None)
        return not callable(ready) or bool(ready())

    def resume_pending_dispatches(self) -> None:
        """执行适配器就绪（``publish_host_ready``）：重算等待集合，派发被闸住的节点。"""

        self._reconcile_resources()

    def _dispatch_held_node(self, task: Dict[str, Any], node: DagNode) -> None:
        """下发一个已经持有完整动作/物料集合的节点运行（当前 attempt）。"""

        with self._guard:
            spec = self._run_specs[node.node_id]
            job_uuid = spec["current_job_uuid"]
            if self._dispatch_paused or not self._executor_ready():
                # 安静点重启闸门 / 执行适配器尚未就绪（ROS2 host node 在设备初始化
                # 完成后才注册）：节点保持在等待集合并继续持有资源，resume /
                # host ready 后由 _reconcile_resources 原样派发，不产生失败。
                return
            if job_uuid in self._dispatched_jobs:
                return
            record = self.resources.request_for_owner(job_uuid)
            if record.status != "held":
                return
            self._waiting_resource_jobs.pop(job_uuid, None)
            self._dispatched_jobs.add(job_uuid)
            args = dict(spec["resolved_action_args"])
            is_manual_confirmation = node.action.strip().lower() == "manual_confirm"
            if is_manual_confirmation:
                self._manual_confirmation_jobs.add(job_uuid)
        if is_manual_confirmation:
            try:
                # 缺少 key 与显式 [] 必须区分：前者是编辑器未完成配置，
                # 后者表示 unrestricted（任何人可确认）。
                if "assignee_user_ids" not in args:
                    raise BackendSchedulingError(
                        "manual_confirm requires explicit assignee_user_ids; use [] for unrestricted"
                    )
                timeout = args.get("timeout_seconds", 3600)
                if isinstance(timeout, bool) or not isinstance(timeout, int):
                    raise BackendSchedulingError(
                        "manual_confirm timeout_seconds must be an integer"
                    )
                if timeout <= 0:
                    timeout = 3600
                manual_meta = spec.get("manual_confirm") or {}
                label = str(manual_meta.get("label") or "人工确认").strip()
                prompt = str(manual_meta.get("prompt") or "").strip()
                confirmation = self.workflow.open_workflow_manual_confirmation(
                    job_uuid,
                    param=args,
                    description=(f"{label}：{prompt}" if prompt else label),
                    meta_data={
                        "workflow_task_uuid": str(task["uuid"]),
                        "workflow_node_uuid": str(spec["workflow_node_uuid"]),
                        "target_device_id": node.device_id,
                    },
                    timeout_seconds=timeout,
                )
                # 进程恢复时，决策可能已在 scheduler 尚未重新接管期间提交；
                # 对已终态的幂等读回立即收敛，不重复调用设备。
                if confirmation.get("status") != "pending":
                    self._on_manual_confirmation_decided(confirmation)
                return
            except Exception:
                with self._guard:
                    self._manual_confirmation_jobs.discard(job_uuid)
                try:
                    self.resources.cancel_owner(job_uuid, reason="manual_open_failed")
                except ResourceNotFound:
                    pass
                raise
        try:
            self.workflow.mark_workflow_node_job_running(job_uuid)
            payload = build_job_start_payload(
                job_id=job_uuid,
                task_id=str(task["uuid"]),
                workflow_id=str(task.get("workflow_uuid") or ""),
                node_id=spec["workflow_node_uuid"],
                device_id=node.device_id,
                action_name=node.action,
                action_type=node.action_type,
                action_args=args,
                materials_need_lock=spec["materials_need_lock"],
                inventory_requirements=spec["inventory_requirements"],
                inventory_reservation_uuid=spec.get(
                    "inventory_reservation_uuid"
                ),
                scheduler_revision=spec["scheduler_revision"],
                node_run_uuid=node.node_id,
                attempt_no=spec["attempt_no"],
            )
            payload["always_free"] = spec.get("always_free", node.always_free)
            self.executor.dispatch(payload)
        except Exception:
            with self._guard:
                self._dispatched_jobs.discard(job_uuid)
            try:
                self.resources.cancel_owner(
                    job_uuid,
                    reason="dispatch_failed",
                )
            except ResourceNotFound:
                pass
            raise

    def _reconcile_resources(self) -> None:
        """重算等待集合；只下发本轮已获得完整资源的 attempt。"""

        with self._guard:
            candidates = list(self._waiting_resource_jobs.items())
        for job_uuid, (task, node) in candidates:
            try:
                record = self.resources.request_for_owner(job_uuid)
            except ResourceNotFound:
                continue
            if record.status != "held":
                continue
            try:
                self._dispatch_held_node(task, node)
            except Exception:
                logger.exception(
                    "scheduler failed to dispatch promoted job %s",
                    job_uuid,
                )
                self._notify_start_failure(node.node_id)

    def _notify_start_failure(self, run_uuid: str) -> None:
        with self._guard:
            task_uuid = self._run_to_task.get(run_uuid)
            runner = self._runners.get(task_uuid or "")
        if runner is not None:
            runner.notify_terminal(run_uuid, NodeState.FAILED)

    def _material_lock_parameters(
        self,
        device_id: str,
        action_name: str,
    ) -> list[str]:
        resolver = self._materials_need_lock_resolver
        if resolver is None:
            resolver = getattr(
                self.executor,
                "resolve_material_lock_parameters",
                None,
            )
        if not callable(resolver):
            return []
        return list(resolver(device_id, action_name) or [])

    def _action_always_free(self, node: DagNode, spec: Dict[str, Any]) -> bool:
        """节点是否免动作锁：节点 execution_policy 显式声明优先，否则取注册表 ``@action(always_free)``。

        与 ``materials_need_lock`` 一样在派发前解析：此时执行适配器已就绪，注册表副本
        （含 slave 远端设备）完整。
        """

        explicit = spec.get("always_free_policy")
        if explicit is not None:
            return bool(explicit)
        resolver = getattr(self.executor, "resolve_action_always_free", None)
        if not callable(resolver):
            return node.always_free
        return bool(resolver(node.device_id, node.action))

    def _release_job_resources(self, job_uuid: str, *, canceled: bool) -> None:
        """释放一个 attempt 持有的资源申请。"""

        try:
            record = self.resources.request_for_owner(job_uuid)
        except ResourceNotFound:
            with self._guard:
                self._waiting_resource_jobs.pop(job_uuid, None)
                self._dispatched_jobs.discard(job_uuid)
                self._manual_confirmation_jobs.discard(job_uuid)
            return
        if record.status not in {"released", "canceled"}:
            if canceled:
                self.resources.cancel_owner(job_uuid, reason="job_canceled")
            else:
                self.resources.release(job_uuid, reason="job_terminal")
        with self._guard:
            self._waiting_resource_jobs.pop(job_uuid, None)
            self._dispatched_jobs.discard(job_uuid)
            self._manual_confirmation_jobs.discard(job_uuid)
        self._reconcile_resources()

    def _cancel_task(self, task_uuid: str) -> None:
        self.executor.cancel_task(task_uuid)
        with self._guard:
            manual_jobs = [
                job_uuid
                for job_uuid in self._manual_confirmation_jobs
                if (run_uuid := self._job_runs.get(job_uuid)) is not None
                and self._run_to_task.get(run_uuid) == task_uuid
            ]
        for job_uuid in manual_jobs:
            try:
                self.workflow.decide_workflow_manual_confirmation(
                    str(self.workflow.get_manual_confirmation_for_job(job_uuid)["uuid"]),
                    action="cancel",
                    confirmed_by="scheduler",
                    decision_idempotency_key=f"scheduler-cancel:{job_uuid}",
                )
            except Exception:  # noqa: BLE001 - 任务取消仍由 run/job 收敛兜底
                logger.exception("failed to cancel manual confirmation for job %s", job_uuid)
        self._cleanup_task_resources(task_uuid)

    def _cleanup_task_resources(self, task_uuid: str) -> None:
        with self._guard:
            job_uuids = [
                job_uuid
                for run_uuid, owner_task_uuid in self._run_to_task.items()
                if owner_task_uuid == task_uuid
                for job_uuid in self._run_specs.get(run_uuid, {}).get("job_uuids", ())
            ]
        for job_uuid in job_uuids:
            self._release_job_resources(job_uuid, canceled=True)

    def resource_snapshot(self):
        """返回当前统一动作/物料锁快照，供诊断 API 使用。"""

        return self.resources.snapshot()

    @staticmethod
    def _inventory_command_uuid(task_uuid: str, suffix: str = "") -> str:
        try:
            namespace = UUID(task_uuid)
        except ValueError:
            namespace = UUID("4f632a8d-f5cc-41e5-9471-f37c79dad537")
        return str(uuid5(namespace, f"inventory:{task_uuid}{suffix}"))

    def _reserve_inventory(
        self,
        task: Dict[str, Any],
        targets: list[tuple[str, Dict[str, Any]]],
        *,
        command_suffix: str = "",
    ) -> None:
        """为若干 (attempt job uuid, spec) 建库存 reservation，all-or-nothing。

        reservation 绑定 attempt 的 job uuid（执行器按 job_id 校验），所以任务启动时
        为每个节点运行的首个 attempt 预留；retry 的新 attempt 再单独预留一次。
        """

        requests = [
            InventoryReservationCreate(
                task_uuid=str(task["uuid"]),
                node_uuid=str(spec["workflow_node_uuid"]),
                job_uuid=job_uuid,
                scheduler_revision=spec["scheduler_revision"],
                requirements=spec["inventory_requirements"],
            )
            for job_uuid, spec in targets
            if spec["inventory_requirements"]
        ]
        if not requests:
            return
        if self.materials_gateway is None:
            raise BackendSchedulingError(
                "workflow declares inventory requirements but materials authority "
                "is unavailable"
            )
        task_uuid = str(task["uuid"])
        value = InventoryTaskReservationCreate(
            task_uuid=task_uuid,
            scheduler_revision=requests[0].scheduler_revision,
            reservations=requests,
        )
        mutation = InventoryMutation(
            command_uuid=self._inventory_command_uuid(task_uuid, command_suffix),
            effect_key="inventory.task.reserve",
            operation="reserve_task_inventory",
            actor_type="scheduler",
        )
        result = self.materials_gateway.reserve_task_inventory(mutation, value)
        specs_by_job = {job_uuid: spec for job_uuid, spec in targets}
        for reservation in result.data.reservations:
            spec = specs_by_job.get(reservation.job_uuid)
            if spec is not None:
                spec["inventory_reservation_uuid"] = reservation.reservation_uuid
                spec["reserved_material_uuids"] = sorted(
                    {
                        item.material_uuid
                        for item in reservation.items
                        if item.kind == "material"
                        and item.material_uuid is not None
                    }
                )
                # 权威解析出的出库内容：按需求 key 归并，派发时注入同名动作参数
                spec["inventory_allocations"] = allocation_arguments(reservation.items)

    def _reserve_task_inventory(
        self,
        task: Dict[str, Any],
        specs: Dict[str, Dict[str, Any]],
    ) -> None:
        self._reserve_inventory(
            task,
            [(spec["current_job_uuid"], spec) for spec in specs.values()],
        )

    def _release_unconsumed_task_inventory(self, task_uuid: str) -> None:
        if self.materials_gateway is None:
            return
        try:
            reservations = self.materials_gateway.list_inventory_reservations(
                task_uuid=task_uuid,
                status="active",
            )
        except Exception:  # noqa: BLE001 - task result must remain persisted
            logger.exception(
                "failed to list active inventory reservations for task %s",
                task_uuid,
            )
            return
        command_uuid = self._inventory_command_uuid(task_uuid)
        for reservation in reservations:
            try:
                value = InventoryReservationTransition(
                    reservation_uuid=reservation.reservation_uuid,
                    reason="workflow_terminal",
                )
                mutation = InventoryMutation(
                    command_uuid=command_uuid,
                    effect_key=(
                        f"inventory.release:{reservation.reservation_uuid}"
                    ),
                    operation="release_inventory_reservation",
                    actor_type="scheduler",
                    job_uuid=reservation.job_uuid,
                )
                self.materials_gateway.release_inventory_reservation(
                    mutation,
                    value,
                )
            except Exception:  # noqa: BLE001 - ledger can be reconciled and retried
                logger.exception(
                    "failed to release inventory reservation %s",
                    reservation.reservation_uuid,
                )

    def _resolve_action_args(self, run_uuid: str) -> Dict[str, Any]:
        spec = self._run_specs[run_uuid]
        target_node = spec["workflow_node_uuid"]
        result: Any = dict(spec["base_param"])
        for edge in spec["edges"]:
            if str(edge.get("target_node_uuid")) != target_node:
                continue
            if edge.get("dependency_only"):
                continue
            source_key = str(edge.get("source_data_key") or "")
            target_key = str(edge.get("target_data_key") or "")
            if not source_key or not target_key:
                continue
            source_run_uuid = spec["runs_by_node"].get(
                str(edge.get("source_node_uuid"))
            )
            if not source_run_uuid:
                raise ParamResolveError("source workflow node run is missing")
            # 节点运行的 return_info 是当前 attempt 的投影：上游若经历过 retry，
            # 这里拿到的就是重试后的结果。
            source_run = self.workflow.get_workflow_node_run(source_run_uuid)
            value: Any = (source_run.get("return_info") or {}).get("return_value")
            exists, value = json_get_exists(value, source_key)
            if not exists:
                raise ParamResolveError(
                    f"value not exist: source data_key {source_key!r}"
                )
            keys = target_key.split("@@@")
            for nested in keys[:-1]:
                exists, value = json_get_exists(value, nested)
                if not exists:
                    raise ParamResolveError(
                        f"value not exist: nested target key {nested!r}"
                    )
            result = json_set(result, keys[-1], value)
        return dict(result)

    def _on_manual_confirmation_decided(
        self, confirmation: Dict[str, Any]
    ) -> None:
        """消费 durable 人工决策，将对应 attempt 收敛为节点终态。"""

        status = str(confirmation.get("status") or "pending")
        if status == "pending":
            return
        job_uuid = str(confirmation.get("workflow_node_job_uuid") or "")
        if not job_uuid:
            return
        with self._guard:
            run_uuid = self._job_runs.get(job_uuid)
            task_uuid = self._run_to_task.get(run_uuid or "")
            runner = self._runners.get(task_uuid or "")
        # 决策可以先于新进程接管到达；事实已在 DB，恢复时 _dispatch_held_node
        # 会再次读到终态并调用本方法，此处不凭空修改未知任务。
        if run_uuid is None or task_uuid is None:
            return

        decision_action = str(
            (confirmation.get("meta_data") or {}).get("decision_action") or ""
        ).strip().lower()
        if status == "approved":
            job_status = "skipped" if decision_action == "skip" else "succeeded"
            node_state = NodeState.SUCCESS
        elif status == "rejected":
            job_status = "failed"
            node_state = NodeState.FAILED
        elif status == "timed_out":
            job_status = "timeout"
            node_state = NodeState.FAILED
        elif status == "canceled":
            job_status = "canceled"
            node_state = NodeState.CANCELLED
        else:
            logger.warning(
                "ignore unknown manual confirmation status %s for job %s",
                status,
                job_uuid,
            )
            return

        try:
            outcome = self.workflow.record_workflow_node_job_terminal(
                job_uuid,
                status=job_status,
                return_info={
                    "suc": job_status in {"succeeded", "skipped"},
                    "suc_type": "manual_confirm",
                    "return_value": {
                        "confirmation_uuid": confirmation.get("uuid"),
                        "status": status,
                        "decision_action": decision_action or None,
                        "confirmed_by": confirmation.get("confirmed_by"),
                        "comment": confirmation.get("comment"),
                    },
                },
                error_info=(
                    []
                    if job_status in {"succeeded", "skipped", "canceled"}
                    else [{"code": f"manual_confirmation_{status}"}]
                ),
            )
        except Exception:  # noqa: BLE001 - 保留 durable 决策，等待恢复重试
            logger.exception(
                "failed to settle manual confirmation %s for job %s",
                confirmation.get("uuid"),
                job_uuid,
            )
            return
        self._release_job_resources(
            job_uuid,
            canceled=job_status in {"failed", "timeout", "canceled"},
        )
        run = outcome.get("run") or {}
        if runner is not None and str(run.get("status") or "") in _RUN_TERMINAL:
            runner.notify_terminal(run_uuid, node_state)

    def _on_executor_finished(
        self,
        job_id: str,
        success: bool,
        ret_value: Any,
        suc_type: str = "normal",
        return_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._guard:
            run_uuid = self._job_runs.get(job_id)
            task_uuid = self._run_to_task.get(run_uuid or "")
            runner = self._runners.get(task_uuid or "")
            context = self._run_context.get(run_uuid or "")
            spec = self._run_specs.get(run_uuid or "")
        if run_uuid is None or task_uuid is None or runner is None or spec is None:
            # 非本调度器派发的 job（如 Backend-controlled 下发的 execution_job）
            return
        job_status = "skipped" if success and suc_type == "skip" else (
            "succeeded" if success else "failed"
        )
        resolution = (
            (return_info or {}).get("error_resolution")
            if isinstance(return_info, dict)
            else None
        )
        # attempt 终态先落表并投影到节点运行；retry 决策由 store 在同一事务里追加新 attempt
        outcome = self.workflow.record_workflow_node_job_terminal(
            job_id,
            status=job_status,
            return_info={
                "suc": success,
                "suc_type": suc_type,
                "return_value": ret_value,
            },
            error_info=[] if success else [{"code": "action_failed"}],
            error_resolution=resolution if isinstance(resolution, dict) else None,
        )
        run = outcome["run"]
        self._release_job_resources(job_id, canceled=False)

        next_job = outcome.get("next_job")
        if next_job is not None and context is not None:
            # retry：store 已在同一事务里追加新 attempt 并把节点运行切回 pending；
            # 这里只需为新 attempt 重新预留库存、申请资源并下发，DAG 节点不终结。
            task, node = context
            with self._guard:
                spec["current_job_uuid"] = str(next_job["uuid"])
                spec["attempt_no"] = int(next_job.get("attempt_no") or spec["attempt_no"] + 1)
                spec["job_uuids"].append(str(next_job["uuid"]))
                spec.pop("inventory_reservation_uuid", None)
                spec["reserved_material_uuids"] = []
            logger.info(
                "workflow node %s retrying as attempt %s (job %s -> %s)",
                spec["workflow_node_uuid"],
                spec["attempt_no"],
                job_id,
                next_job["uuid"],
            )
            try:
                self._reserve_inventory(
                    task,
                    [(spec["current_job_uuid"], spec)],
                    command_suffix=f":{spec['current_job_uuid']}",
                )
                self._start_node(task, node)
            except Exception:  # noqa: BLE001 - 新 attempt 起不来按节点失败收敛
                logger.exception(
                    "workflow node %s failed to start retry attempt %s",
                    spec["workflow_node_uuid"],
                    next_job["uuid"],
                )
                self._persist_terminal_if_needed(run_uuid, NodeState.FAILED)
                runner.notify_terminal(run_uuid, NodeState.FAILED)
            return

        if run["status"] in _RUN_TERMINAL:
            runner.notify_terminal(
                run_uuid,
                NodeState.SUCCESS
                if run["status"] in {"succeeded", "skipped"}
                else NodeState.FAILED,
            )

    def publish_job_error_decision_required(self, report: Dict[str, Any]) -> bool:
        """执行面决策桥：本机派发的 attempt 失败并挂起等待决策。"""

        job_uuid = str(report.get("job_id") or "")
        with self._guard:
            owned = job_uuid in self._job_runs
        if not owned:
            return False
        self.workflow.mark_workflow_node_job_decision_pending(job_uuid, report)
        return True

    def _on_node_terminal(self, run_uuid: str, state: NodeState) -> None:
        self._persist_terminal_if_needed(run_uuid, state)
        with self._guard:
            spec = self._run_specs.get(run_uuid, {})
            job_uuid = spec.get("current_job_uuid")
        if job_uuid:
            self._release_job_resources(
                job_uuid,
                canceled=state is not NodeState.SUCCESS,
            )

    def _persist_terminal_if_needed(self, run_uuid: str, state: NodeState) -> None:
        status = {
            NodeState.SUCCESS: "succeeded",
            NodeState.FAILED: "failed",
            NodeState.CANCELLED: "canceled",
        }.get(state)
        if status is None:
            return
        self.workflow.close_workflow_node_run(run_uuid, status=status)

    def _fail_unstarted_task(
        self,
        task_uuid: str,
        runs: list[Dict[str, Any]],
        error: Exception,
    ) -> None:
        for run in runs:
            if run["status"] not in _RUN_TERMINAL:
                self.workflow.close_workflow_node_run(str(run["uuid"]), status="canceled")
        self.workflow.finish_workflow_task(
            task_uuid,
            status="failed",
            error_info=[
                {"code": "plan_not_executable", "message": str(error)}
            ],
        )


__all__ = ["BackendScheduler", "BackendSchedulingError", "allocation_arguments"]
