"""由平台权威生成调度事实；不排序 Task、不估时，也不持有算法检查点。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from typing import Any

from unilabos.app.scheduler.binding_resources import resource_bindings
from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.device_target import DeviceTargetUnavailable, ResolvedDeviceTarget
from unilabos.app.scheduler.engine_graph import RuntimeGraph
from unilabos.app.scheduler.engine_resources import execution_waiters, platform_resources
from unilabos.app.scheduler.inventory.material_io import MaterialIOError
from unilabos.app.scheduler.models import priority_weight
from unilabos.app.scheduler.param_resolver import ParamResolveError
from unilabos.app.scheduler.resource_lock import normalize_resource_lock_keys
from unilabos.app.scheduler.resource_wait_policy import is_temporary_resource_condition
from unilabos.app.scheduler.transfer_resource_set import TransferResourceSetError
from unilabos.scheduling.engine import PREPARATION_ERROR_PREFIX, reason
from unilabos.scheduling.metadata import timing_profile_catalog
from unilabos.scheduling.protocol import DEFAULT_LIMITS, EngineError, encode_message, require
from unilabos.workflow.execution_started import execution_start_times
from unilabos.workflow.manual_confirmation import ManualConfirmationStore
from unilabos.workflow.resource_lock_key import (
    conflicting_resource_lock_keys,
    device_key_from_action_key,
    device_lock_key,
    material_lock_key,
    parse_canonical_resource_lock_key,
    site_lock_key,
)
from unilabos.workflow.scheduling_journal import SchedulingJournal
from unilabos.workflow.store import WorkflowStore
from unilabos.workflow.task_runtime_projection import TaskRuntimeProjection
from unilabos.workflow.workflow_spec_compiler import WorkflowSpecCompiler

_TERMINAL = {"succeeded", "failed", "canceled", "timeout"}
_INFLIGHT = {"dispatched", "running", "cancel_requested", "intervention_required"}
_KINDS = {
    "device_action": "device", "material_transfer": "device", "condition": "condition",
    "repeat_until": "loop", "workflow_command": "workflow_command",
    "material_source": "material_source", "manual_confirm": "manual", "material_io": "manual",
}


def timestamp(value: str | None) -> int | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "SNAPSHOT_INVALID", "平台时间事实缺少时区")
    return round(parsed.timestamp() * 1000)


class RuntimeSnapshot:
    """锁内复制 Task/Job 与执行占用。返回值不包含数据库、设备或可变运行对象。"""

    def __init__(
        self, store: WorkflowStore, scheduler: Any, journal: SchedulingJournal, *,
        domain_id: str, timing_profiles: dict[str, Any] | None = None,
        step_permits: Any = None,
    ) -> None:
        self.store, self.scheduler, self.journal, self.domain_id = store, scheduler, journal, domain_id
        self.compiler = WorkflowSpecCompiler()
        self.step_permits = step_permits
        self.timing_profiles = timing_profile_catalog({} if timing_profiles is None else timing_profiles)

    def capture(self) -> dict[str, Any]:
        with self.scheduler.execution_control_lock, self.store.read():
            try:
                self._busy = self.scheduler._busy_keys(strict=True) | self.scheduler._held_resource_locks()
                return deepcopy(self._capture())
            except EngineError:
                raise
            except (OSError, ValueError, KeyError, TypeError, MaterialIOError) as error:
                raise EngineError("SNAPSHOT_INVALID", "平台事实无法生成完整快照：" + str(error)) from error

    def _capture(self) -> dict[str, Any]:
        scheduler = self.scheduler
        domain = self.journal.read(self.domain_id)
        step_offers = self.step_permits.offered(domain["run_epoch"]) if self.step_permits is not None else set()
        all_leases, ownerships = platform_resources(
            self.store, scheduler.station_resource_inventory,
            TaskRuntimeProjection(self.store).list_execution_locks(active_only=True),
        )
        self._busy.update(lease["lock_key"] for lease in all_leases)
        self._occupancy_owners = defaultdict(set)
        self._foreign_busy: dict[str, set[str]] = {}
        for lease in all_leases:
            self._occupancy_owners[lease["lock_key"]].add(lease["workflow_task_uuid"])
        for ownership in ownerships:
            for key in ownership["resource_ids"]:
                self._occupancy_owners[key].add(ownership["task_id"])
        held_tasks = {lease["workflow_task_uuid"] for lease in all_leases} | {held["task_id"] for held in ownerships}
        head_tasks = set(domain["head"]["task_scope"]) if domain["head"] else set()
        records, jobs_by_task, claim_tasks = self.store.scheduling_task_facts(
            (held_tasks | head_tasks) - {None},
        )
        held_tasks.update(claim_tasks)
        uncertain_tasks = {lease["workflow_task_uuid"] for lease in all_leases if lease["state"] == "uncertain"}
        leases_by_job = defaultdict(list)
        for lease in all_leases:
            leases_by_job[lease["workflow_node_job_uuid"]].append(lease)
        tasks, nodes, inflight, options, regions, intervals = [], [], [], [], [], []
        resources: dict[str, dict[str, Any]] = {}
        allocations = []
        pools = {
            "domain:tasks": self._pool("domain:tasks", "active_tasks", scheduler.max_active_tasks),
            "domain:jobs": self._pool("domain:jobs", "inflight_jobs", scheduler.max_in_flight_jobs),
        }
        jobs_index = {}
        job_facts = {job["uuid"]: job for jobs in jobs_by_task.values() for job in jobs}
        with self.store.read() as connection:
            start_times = execution_start_times(connection, list(job_facts))
        job_intervals: dict[str, dict[str, set[str]]] = {}
        active_keys: dict[str, set[str]] = {}
        confirmations_by_task = ManualConfirmationStore(self.store).list_by_tasks(jobs_by_task)
        for task in records:
            identity = task["uuid"]
            # 独立动作没有工作流定义；按自身持久 Task 分组，不伪造来源工作流关系。
            workflow = task["workflow_uuid"] or "standalone:" + identity
            jobs = jobs_by_task[identity]
            run = scheduler._workflows.get(identity)
            spec = run.spec if run else self.compiler.compile(task, jobs)
            fact_run = run
            if fact_run is None:
                fact_run = WorkflowRun(spec)
                fact_run.restore_execution_facts(jobs)
            runtime_graph = RuntimeGraph(task, jobs, fact_run)
            intervals.extend(self._intervals(fact_run, runtime_graph, resources))
            runtime_nodes = runtime_graph.nodes
            coordinator_ids = {
                node["uuid"] for node in task["execution_plan"]["nodes"]
                if node.get("kind") in {"material_source", "workflow_input", "workflow_output"}
                and not node.get("runtime_material_source")
            }
            graph = runtime_graph.states
            predecessors = runtime_graph.predecessors
            confirmations = confirmations_by_task.get(identity, {})
            workflow_pool = "workflow:" + workflow
            pools.setdefault(workflow_pool, self._pool(
                workflow_pool, "active_tasks", scheduler.max_tasks_per_workflow, workflow_id=workflow,
            ))
            task_pools = ["domain:tasks", workflow_pool]
            uncertain = identity in uncertain_tasks or any(job.get("uncertainty_reason") for job in jobs)
            intervention_task = any(job.get("execution_source") == "error_manual" for job in jobs)
            unfinished = any(job["status"] in _INFLIGHT for job in jobs)
            held = identity in held_tasks
            control = (
                "recovery_hold" if uncertain or task["control_status"] in {"error_waiting", "waiting_reconciliation", "recovering"}
                else "cancel_requested" if task["status"] == "canceling"
                else "paused" if task.get("dispatch_gate") == "paused" or task["control_status"] == "paused"
                else "active"
            )
            mode = task.get("execution_mode") or task.get("run_mode") or "normal"
            if mode == "switching_to_step":
                mode, control = "step", "paused"
            task_value = {
                "task_id": identity, "workflow_id": workflow,
                "queue_seq": self.journal.queue_sequence(self.domain_id, identity),
                "created_at_ms": timestamp(task["create_time"]),
                "priority": priority_weight(task.get("priority") or "normal"),
                "wait_since_ms": timestamp((task.get("wait_reason") or {}).get("waiting_since")),
                "status": {"timeout": "failed", "canceling": "running"}.get(task["status"], task["status"]),
                "admission": (
                    "blocked" if run and run.state.value == "waiting_for_material"
                    else "admitted" if run else ("blocked" if task.get("wait_reason") else "pending")
                ),
                "control": control, "execution_mode": mode,
                "normal_progression_allowed": bool(
                    run and not intervention_task and control == "active" and mode == "normal"
                    and task["status"] in {"pending", "running"} and run.dispatch_gate == "active" and run.state.value == "running"
                    and (scheduler._station_control is None or scheduler._station_control.allows_task(identity))
                ),
                "terminal_settled": bool(
                    task["status"] in _TERMINAL and task["cleanup_status"] in {"none", "settled"}
                    and not unfinished and not uncertain and not held
                ),
                "known_graph_complete": True, "blockers": [], "capacity_pool_ids": task_pools,
            }
            if control == "recovery_hold":
                task_value["blockers"] = [reason("RECOVERY_REQUIRED", "执行或结算事实待核实", task_id=identity)]
            tasks.append(task_value)
            if task["status"] in {"running", "canceling"}:
                for pool in task_pools:
                    pools[pool]["holders"].append({"task_id": identity, "attempt_id": None})
            for job in jobs:
                if not job.get("current_attempt", True):
                    continue
                node_id = str((job.get("control_data") or {}).get("runtime_node_id") or job["workflow_node_uuid"])
                node = runtime_nodes.get(node_id)
                if node is None and node_id in coordinator_ids:
                    require(
                        job["status"] in _TERMINAL | {"pending", "skipped"}
                        and not job.get("uncertainty_reason") and not job.get("edge_command_uuid"),
                        "SNAPSHOT_INVALID", "提交协调节点不能持有物理执行或未决事实",
                    )
                    # 预准入来源与 I/O 协调节点不进入调度执行图；未完成准入由 Task
                    # admission 表达，库存已持有而镜像未提交的所有权仍独立保留。
                    continue
                if node is None and job["status"] in _TERMINAL | {"skipped"} and not job.get("uncertainty_reason"):
                    # 已退出循环轮次仅留在历史事实中，不伪装成当前可派发图。
                    continue
                ref = {"task_id": identity, "node_id": node_id}
                jobs_index[job["uuid"]] = ref
                if node is None:
                    raise EngineError("SNAPSHOT_INVALID", "当前作业没有对应冻结节点")
                require(job["executor_kind"] in _KINDS, "SNAPSHOT_INVALID", "节点执行类型尚未支持投影")
                approved = confirmations.get(job["uuid"], {}).get("status") == "approved"
                intervention = job.get("execution_source") == "error_manual"
                if intervention:
                    approved = bool(
                        scheduler._station_control
                        and scheduler._station_control.manual_execution_authorized(job["uuid"])
                    )
                manual_mode = (
                    "intervention" if intervention
                    else "material_io" if node.executor_kind == "material_io"
                    else "confirm_then_execute" if node.executor_kind == "manual_confirm" or job["uuid"] in confirmations
                    else "none"
                )
                admitted_manual = (
                    manual_mode != "none" and job["status"] == "pending" and approved
                    and bool(job.get("edge_command_uuid")) and bool(leases_by_job[job["uuid"]])
                )
                status = (
                    "uncertain" if job.get("uncertainty_reason")
                    else "running" if job["status"] in _INFLIGHT or admitted_manual
                    else {"timeout": "failed"}.get(job["status"], job["status"])
                )
                if node.executor_kind == "repeat_until" and status == "pending" and graph[node_id]["state"] == "dispatched":
                    # 循环 Job 在持久库可保持 pending；已进入的本地 controller 仍是运行中，
                    # 但不因此产生物理在途 attempt 或占用 Job 容量。
                    status = "running"
                parameters = deepcopy(job.get("param") or {})
                final = job["status"] != "pending" or admitted_manual
                if run and job["status"] == "pending" and not admitted_manual:
                    try:
                        parameters = run.resolve_params(node_id)
                        final = True
                    except ParamResolveError:
                        pass
                revision = self.journal.parameters_revision(
                    self.domain_id, ref, {"attempt": job["attempt"], "parameters": parameters, "final": final},
                )
                loop_transition = bool(run and status == "running" and runtime_graph.loop_transition_ready(node_id))
                node_value = {
                    "ref": ref, "job_id": job["uuid"], "attempt_no": job["attempt"],
                    "kind": "manual" if manual_mode != "none" else _KINDS[job["executor_kind"]], "status": status,
                    "predecessors": [{"task_id": identity, "node_id": parent} for parent in sorted(set(predecessors[node_id]))],
                    "ready": bool(
                        not intervention and (
                            loop_transition or (
                                status == "pending" and run and graph.get(node_id, {}).get("state") in {"pending", "ready"}
                                and not graph[node_id]["pending_parents"]
                            )
                        )
                    ),
                    "parameters_final": final, "parameters_revision": revision, "parameters": parameters,
                    "action_id": node.device_action_key if node.action_name else None,
                    "action_revision": job.get("definition_fingerprint"),
                    "information_boundary": manual_mode != "none", "manual_approved": approved,
                    "step_authorized": (identity, node_id, job["uuid"], job["attempt"]) in step_offers,
                    "blockers": [], "control_region_id": runtime_graph.parent[node_id], "manual_mode": manual_mode,
                    "capacity_pool_ids": ["domain:jobs"],
                }
                nodes.append(node_value)
                if not intervention and status == "pending" and node_value["kind"] in {"device", "manual"}:
                    options.extend(self._options(fact_run, node, node_value, resources))
                data = job.get("control_data") or {}
                by_lock = data.get("resource_interval_ids_by_lock")
                if by_lock is not None:
                    job_intervals[job["uuid"]] = {
                        key: {identity + ":" + interval_id for interval_id in interval_ids}
                        for key, interval_ids in by_lock.items()
                    }
                else:
                    job_intervals[job["uuid"]] = {
                        lease["lock_key"]: {identity + ":" + interval_id for interval_id in node.resource_interval_ids}
                        for lease in leases_by_job[job["uuid"]]
                    }
                if node_value["kind"] in {"device", "manual"} and (job["status"] in _INFLIGHT or admitted_manual or job.get("uncertainty_reason")):
                    leases = leases_by_job[job["uuid"]]
                    plan, plan_keys, _, _ = scheduler._interval_projection(fact_run, node, current_job_id=job["uuid"])
                    active_keys[job["uuid"]] = scheduler._active_use_resource_keys(
                        fact_run, node, plan=plan, plan_resource_keys=plan_keys,
                        requested_lock_keys={lease["lock_key"] for lease in leases},
                    )
                    actual = data.get("actual_executor") or {}
                    command = job.get("edge_command_uuid")
                    continuation = (
                        not intervention and job["start_state"] == "NOT_STARTED" and control in {"active", "paused"}
                        and task["status"] in {"pending", "running"} and not job.get("uncertainty_reason")
                        and domain["lifecycle"] == "running"
                        and (task_value["normal_progression_allowed"]
                             or task.get("execution_mode") == "switching_to_step" or (
                            mode in {"step", "single_node"} and self.step_permits is not None
                            and self.step_permits.bound_to(identity, node_id, job["uuid"], job["attempt"])
                        ))
                        and (manual_mode == "none" or approved)
                    )
                    admitted_at = min((lease["acquired_at"] for lease in leases), default=None) or job.get("started_at")
                    if intervention and admitted_at is None:
                        # 旧受控动作只有单 Job Task 的准入时刻；不把它当物理开始证据。
                        admitted_at = task.get("started_at")
                    require(admitted_at is not None, "SNAPSHOT_INVALID", "在途对象缺少持久准入时间")
                    inflight.append({
                        "attempt_id": job["uuid"], "node": ref, "attempt_no": job["attempt"], "job_id": job["uuid"],
                        "command_ids": [command] if command else [],
                        "device_id": actual.get("material_uuid") or actual.get("local_device_id") or None,
                        "resource_ids": sorted(active_keys[job["uuid"]]),
                        "parameters": parameters, "parameters_revision": revision,
                        "dispatched_at_ms": timestamp(admitted_at), "started_at_ms": start_times.get(job["uuid"]),
                        "status": "uncertain" if job.get("uncertainty_reason") else (
                            "cancel_requested" if job["status"] == "cancel_requested" else (
                                "admitted" if job["start_state"] == "NOT_STARTED" else (
                                    "running" if job["uuid"] in start_times else "submitted"
                                )
                            )
                        ),
                        "start_state": job["start_state"], "manual_phase": (
                            "none" if manual_mode == "none" else ("approved" if approved else "awaiting_approval")
                        ),
                        "continuation_allowed": continuation,
                        "timing_profile": (data.get("scheduling_observation_context") or {}).get("timing_profile"),
                    })
                    pools["domain:jobs"]["holders"].append({"task_id": identity, "attempt_id": job["uuid"]})
            task_regions = runtime_graph.regions([node for node in nodes if node["ref"]["task_id"] == identity])
            task_value["known_graph_complete"] = not any(region["unmaterialized_tail"] for region in task_regions)
            regions.extend(task_regions)
        for lease in all_leases:
            key = lease["lock_key"]
            resources.setdefault(key, self._resource(key))
            owner = lease["workflow_task_uuid"] if lease["workflow_task_uuid"] in jobs_by_task else None
            owner_ref = jobs_index.get(lease["workflow_node_job_uuid"])
            job_id = lease["workflow_node_job_uuid"]
            kind = "inflight" if key in active_keys.get(job_id, set()) else "continuous"
            if owner is None:
                kind = "external"
            if lease["state"] == "uncertain":
                kind = "uncertain"
            allocations.append({
                "allocation_id": lease["uuid"], "owner_task_id": owner, "owner_node": owner_ref,
                "resource_ids": [key], "kind": kind,
                "release_boundary": None,
            })
            memberships = job_intervals.get(job_id, {}).get(key, set())
            if not memberships:
                # 旧轮次仍持有资源时使用持久区间引用，不能依赖当前内存节点。
                data = (job_facts.get(job_id) or {}).get("control_data") or {}
                memberships = {
                    owner + ":" + interval_id
                    for interval_id in data.get("resource_interval_ids", [])
                } if owner else set()
            for interval in intervals:
                if interval["interval_id"] in memberships and key in interval["resource_ids"]:
                    interval["held_allocation_ids"].append(lease["uuid"])
        for interval in intervals:
            require(
                not self.scheduler._interval_resource_holders.get(
                    (interval["owner_task_id"], interval["interval_id"].split(":", 1)[1])
                ) or interval["held_allocation_ids"],
                "SNAPSHOT_INVALID", "连续资源的内存持有缺少持久占用证据",
            )
        aliases = {}
        for job in job_facts.values():
            actual = (job.get("control_data") or {}).get("actual_executor") or {}
            if actual.get("material_uuid") and actual.get("local_device_id"):
                aliases[device_lock_key(actual["local_device_id"])] = device_lock_key(actual["material_uuid"])
        for option in options:
            actual = option["binding_facts"]["actual_executor"]
            if actual.get("material_uuid"):
                aliases[device_lock_key(actual["local_device_id"])] = device_lock_key(actual["material_uuid"])
        represented = {key for allocation in allocations for key in allocation["resource_ids"]}
        external = {
            aliases.get(key, key)
            for busy in self._busy
            for key in [device_key_from_action_key(busy) or busy]
        } - represented
        for key in sorted(external):
            resources.setdefault(key, self._resource(key))
            allocations.append({
                "allocation_id": "external:" + hashlib.sha256(key.encode()).hexdigest(),
                "owner_task_id": None, "owner_node": None, "resource_ids": [key],
                "kind": "external", "release_boundary": None,
            })
        waiters = []
        for waiter in execution_waiters(self.store):
            waiter_ref = jobs_index.get(waiter["job_id"])
            require(
                waiter_ref is not None and waiter_ref["task_id"] == waiter["task_id"],
                "SNAPSHOT_INVALID", "持久等待者缺少当前节点事实",
            )
            for key in waiter["resource_ids"]:
                resources.setdefault(key, self._resource(key))
            waiters.append({
                "node": waiter_ref, "attempt_no": waiter["attempt_no"], "resource_ids": waiter["resource_ids"],
                "enqueued_at_ms": timestamp(waiter["enqueued_at"]),
            })
        self._project_ownerships(ownerships, tasks, nodes, resources, allocations, intervals)
        conflicts = [
            {"left": left, "right": right}
            for index, left in enumerate(sorted(resources))
            for right in sorted(resources)[index + 1:]
            if conflicting_resource_lock_keys({left}, {right})
        ]
        for pool in pools.values():
            pool["used"] = len(pool["holders"])
        return {
            "revision": "0", "captured_at_ms": round(scheduler._clock() * 1000), "feedback_watermark": "0",
            "lifecycle": "recovering", "execution_gate": "held", "head": None, "complete": True,
            "task_view": {"tasks": tasks, "nodes": nodes, "control_regions": regions},
            "resource_view": {"resources": list(resources.values()), "options": options, "allocations": allocations, "intervals": intervals, "conflicts": conflicts, "waiters": waiters},
            "inflight": inflight, "capacity_view": {"pools": list(pools.values())},
        }

    def _project_ownerships(
        self, ownerships: list[dict[str, Any]], tasks: list[dict[str, Any]], nodes: list[dict[str, Any]],
        resources: dict[str, dict[str, Any]], allocations: list[dict[str, Any]], intervals: list[dict[str, Any]],
    ) -> None:
        """任务所有权包含自身执行区间，释放边界必须等待平台的真实结算。"""
        task_ids = {task["task_id"] for task in tasks}
        by_task: dict[str, dict[str, Any]] = {}
        for held in ownerships:
            keys = set(held["resource_ids"])
            # 整物料独占覆盖其库位；明确列出当前图中的下级键，让引擎无需解释平台锁语法。
            keys.update(key for key in resources if conflicting_resource_lock_keys({key}, keys))
            for key in keys:
                resources.setdefault(key, self._resource(key))
            owner = held["task_id"] if held["task_id"] in task_ids else None
            allocations.append({
                "allocation_id": held["allocation_id"], "owner_task_id": owner,
                "owner_node": None, "resource_ids": sorted(keys),
                "kind": "continuous" if owner else "external", "release_boundary": None,
            })
            if owner is None:
                continue
            interval = by_task.setdefault(owner, {
                "interval_id": owner + ":task-ownership", "owner_task_id": owner,
                "resource_ids": set(), "members": [node["ref"] for node in nodes if node["ref"]["task_id"] == owner],
                "acquire_node": None, "release_node": None, "held_allocation_ids": [], "contained_in": [],
            })
            interval["resource_ids"].update(keys)
            interval["held_allocation_ids"].append(held["allocation_id"])
        for interval in intervals:
            parent = by_task.get(interval["owner_task_id"])
            if parent and set(interval["resource_ids"]) <= parent["resource_ids"]:
                interval["contained_in"].append(parent["interval_id"])
        for interval in by_task.values():
            interval["resource_ids"] = sorted(interval["resource_ids"])
            intervals.append(interval)

    def _devices(self, node: Any) -> list[ResolvedDeviceTarget]:
        """穷举注册权威可证明的绑定；忙碌候选保留，由真实占用单独表达。"""
        if node.is_material_io():
            return [ResolvedDeviceTarget("scheduler-control", "")]
        resolver = self.scheduler._device_target_resolver
        if node.device_id:
            selector = {
                "mode": "fixed", "local_device_id": node.device_id,
                "material_uuid": node.device_material_uuid,
            }
            if resolver is not None:
                return [resolver(selector, node.action_name, set())]
            require(
                not self.scheduler.physical_dispatch_enabled,
                "SNAPSHOT_INVALID", "物理调度缺少设备注册权威",
            )
            return [ResolvedDeviceTarget(node.device_id, node.device_material_uuid)]
        require(resolver is not None, "SNAPSHOT_INVALID", "动态设备缺少注册权威")
        require(bool(node.device_selector), "SNAPSHOT_INVALID", "设备动作缺少冻结选择器")
        result: list[ResolvedDeviceTarget] = []
        excluded: set[str] = set()
        for _ in range(DEFAULT_LIMITS["max_options"] + 1):
            try:
                target = resolver(node.device_selector, node.action_name, excluded)
            except DeviceTargetUnavailable:
                if result:
                    return result
                raise
            require(target not in result, "SNAPSHOT_INVALID", "设备枚举权威未遵守排除集合")
            result.append(target)
            excluded.update({device_lock_key(target.local_device_id), device_lock_key(target.material_uuid or target.local_device_id)})
        raise EngineError("LIMIT_EXCEEDED", "设备候选超过协议上限，不能截断完整快照")

    def _options(self, run: Any, node: Any, value: dict[str, Any], resources: dict[str, Any]) -> list[dict[str, Any]]:
        """参数、设备与所需资源来自执行权威，不含时长模型或调度优先级。"""
        if not value["parameters_final"]:
            value["blockers"].append(reason("PARAMETERS_PENDING", "参数尚待真实上游结果", task_id=value["ref"]["task_id"]))
            return []
        params = value["parameters"]
        plan, plan_keys, _, _ = self.scheduler._interval_projection(run, node, require_inheritable=False)
        try:
            targets = self._devices(node)
        except DeviceTargetUnavailable as error:
            value["blockers"].append(self._resource_error(error, value["ref"], resources))
            return []
        continuation = self.scheduler._available_continuation_resource_keys(value["ref"]["task_id"], node)
        task_id = value["ref"]["task_id"]
        if task_id not in self._foreign_busy:
            self._foreign_busy[task_id] = {
                key for key, owners in self._occupancy_owners.items() if owners - {task_id}
            }
        # 本 Task 的区间续行只能豁免自己的持有，不能抹去其他权威同键占用。
        busy = (self._busy - continuation) | self._foreign_busy[task_id]
        result = []
        for target in targets:
            try:
                candidates = resource_bindings(self.scheduler, node, params, target)
            except MaterialIOError as error:
                if error.code == "effect_corrupt":
                    raise
                value["blockers"].append({
                    **reason(PREPARATION_ERROR_PREFIX + error.code, str(error), task_id=task_id),
                    "node": value["ref"],
                })
                continue
            except ValueError as error:
                code = getattr(error, "code", "")
                if (
                    isinstance(error, TransferResourceSetError)
                    and code == "source_constraint_mismatch" and not value["ready"]
                ):
                    # 后续搬运的固定参数可提前解析，但其来源位置只能由前序真实
                    # 结算建立。此时不伪造未来库存绑定；节点就绪后仍须严格复核。
                    value["blockers"].append({
                        **reason("SOURCE_LOCATION_PENDING", "来源库位约束等待前序执行事实", task_id=task_id),
                        "node": value["ref"],
                    })
                    continue
                if not is_temporary_resource_condition(code):
                    raise
                value["blockers"].append(self._resource_error(error, value["ref"], resources))
                continue
            for candidate in candidates:
                needed = candidate.resources | plan_keys
                if plan is not None:
                    require(
                        not (normalize_resource_lock_keys(needed) - normalize_resource_lock_keys(plan_keys | candidate.runtime_resources)),
                        "SNAPSHOT_INVALID", "候选绑定包含冻结资源计划外的资源",
                    )
                active = self.scheduler._active_use_resource_keys(
                    run, node, plan=plan, plan_resource_keys=plan_keys, requested_lock_keys=needed,
                )
                # 内部互斥集合会把整物料锁下的库位键归并；完整绑定仍保留这些键，
                # 它们同样是活动使用，不能被误标成缺少区间依据的纯所有权。
                active_binding = {
                    key for key in needed if normalize_resource_lock_keys(active | {key}) == active
                }
                for key in needed:
                    resources.setdefault(key, self._resource(key))
                needs_device = not node.is_material_io() and (not node.always_free or node.is_manual_confirm())
                device_busy = bool({
                    device_lock_key(target.local_device_id),
                    device_lock_key(target.material_uuid or target.local_device_id),
                    f"/devices/{target.local_device_id}/{node.action_name}",
                } & busy) if needs_device else False
                occupied = device_busy or bool(needed & busy) or bool(conflicting_resource_lock_keys(needed, busy))
                blockers = [
                    {**blocker, "task_id": task_id, "node": value["ref"]}
                    for blocker in candidate.blockers
                ]
                if occupied:
                    blocked_keys = {
                        key for key in needed if key in busy or conflicting_resource_lock_keys({key}, busy)
                    }
                    if device_busy:
                        blocked_keys.add(device_lock_key(target.material_uuid or target.local_device_id))
                    blockers.append({
                        **reason("RESOURCE_BUSY", "所需资源存在真实占用", task_id=task_id),
                        "node": value["ref"], "resource_ids": sorted(blocked_keys),
                    })
                binding = {
                    "node": value["ref"], "attempt_no": value["attempt_no"],
                    "parameters_revision": value["parameters_revision"],
                    "device_id": None if node.is_material_io() else target.material_uuid or target.local_device_id,
                    "resource_ids": sorted(needed), "resolved_parameters": candidate.parameters,
                    "binding_facts": {
                        **candidate.facts,
                        "actual_executor": {"local_device_id": target.local_device_id, "material_uuid": target.material_uuid},
                        "active_resource_ids": sorted(active_binding),
                        "continuation_resource_ids": sorted(needed & continuation),
                    },
                    "timing_profile": deepcopy(self.timing_profiles.get(value["action_id"])),
                }
                result.append({
                    **binding, "option_id": hashlib.sha256(encode_message(binding)).hexdigest(),
                    "eligible_now": not blockers,
                    "blockers": blockers,
                })
        return result

    def _resource_error(self, error: Any, ref: dict[str, str], resources: dict[str, Any]) -> dict[str, Any]:
        """即使完整方案尚不可形成，也保留库存或设备权威给出的真实阻塞身份。"""
        keys = set()
        for item in getattr(error, "resources", ()):
            if item["scope"] == "device":
                key = device_lock_key(item.get("device_id") or item["material_uuid"])
            elif item["scope"] == "material":
                key = material_lock_key(item["material_uuid"])
            elif item["scope"] == "material_site":
                key = site_lock_key(item["material_uuid"], item["site_uuid"])
            else:
                key = item["lock_key"]
                require(parse_canonical_resource_lock_key(key) is not None, "SNAPSHOT_INVALID", "阻塞资源身份无效")
            keys.add(key)
            resources.setdefault(key, self._resource(key))
        return {
            **reason(error.code.upper(), str(error), task_id=ref["task_id"]),
            "node": ref, "resource_ids": sorted(keys),
        }

    def _intervals(self, run: Any, graph: RuntimeGraph, resources: dict[str, Any]) -> list[dict[str, Any]]:
        plan = self.scheduler._resource_plan_for_spec(run.spec)
        if plan is None:
            return []
        resource_by_id = {resource.resource_id: resource for resource in plan.resources}
        scopes = {scope.scope_id: scope for scope in plan.scopes}
        result = []
        for interval in plan.intervals:
            key = resource_by_id[interval.resource_id].canonical_key
            resources.setdefault(key, self._resource(key))
            members = sorted({
                graph.actual_by_template[identity] for identity in interval.node_uuids
                if identity in graph.actual_by_template
            })
            acquire = graph.actual_by_template.get(interval.acquire_node_uuid)
            release = graph.actual_by_template.get(interval.release_node_uuid)
            ancestors, scope = set(), scopes.get(interval.scope_id)
            while scope is not None and scope.parent_scope_id:
                require(scope.parent_scope_id not in ancestors, "SNAPSHOT_INVALID", "资源作用域包含有环")
                ancestors.add(scope.parent_scope_id)
                scope = scopes.get(scope.parent_scope_id)
            result.append({
                "interval_id": graph.task_id + ":" + interval.interval_id, "owner_task_id": graph.task_id,
                "resource_ids": [key], "members": [graph.ref(identity) for identity in members],
                "acquire_node": graph.ref(acquire) if acquire else None,
                "release_node": graph.ref(release) if release else None,
                "held_allocation_ids": [],
                "contained_in": [
                    graph.task_id + ":" + outer.interval_id
                    for outer in plan.intervals if outer.scope_id in ancestors
                ],
            })
        return result

    @staticmethod
    def _resource(key: str) -> dict[str, Any]:
        parsed = parse_canonical_resource_lock_key(key)
        return {
            "resource_id": key, "kind": {
                "device": "device", "material_site": "site", "material": "material_lock",
            }.get(parsed.scope, "other") if parsed else "other",
            "availability": "available", "attributes": {},
        }

    @staticmethod
    def _pool(identity: str, kind: str, limit: int, *, workflow_id: str | None = None) -> dict[str, Any]:
        return {
            "pool_id": identity, "kind": kind, "scope": "workflow" if workflow_id else "domain",
            "workflow_id": workflow_id, "limit": limit, "used": 0, "external_used": 0, "holders": [],
        }
