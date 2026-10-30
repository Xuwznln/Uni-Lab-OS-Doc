"""已接受的引擎决策到现有平台执行边界的装配。"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable

from unilabos.app.scheduler.binding_resources import resource_bindings
from unilabos.app.scheduler.engine_graph import RuntimeGraph
from unilabos.app.scheduler.engine_projection import RuntimeSnapshot
from unilabos.app.scheduler.engine_wakeup import EngineWakeup
from unilabos.app.scheduler.inventory.material_io import MaterialIOError
from unilabos.scheduling.engine import PREPARATION_ERROR_PREFIX, preparation_error, reason
from unilabos.scheduling.protocol import EngineError, EngineTransport, encode_message, require
from unilabos.scheduling.resource_bindings import RESOURCE_BINDINGS_CAPABILITY
from unilabos.scheduling.validation import node_allowed, node_key, task_capacity_available
from unilabos.workflow.scheduling_feedback import RuntimeFeedback
from unilabos.workflow.scheduling_journal import SchedulingJournal
from unilabos.workflow.scheduling_local_authority import LocalControlAuthority
from unilabos.workflow.scheduling_observation import OBSERVED_DISPATCH_BOUNDARY
from unilabos.workflow.scheduling_session import SchedulingSession, stale_retry_delay_ms
from unilabos.workflow.scheduling_step_permit import StepPermit, StepPermitStore
from unilabos.workflow.scheduling_wait import reconcile_resource_waits


def stale_wakeup_delay_ms(error: EngineError, *, last_error: EngineError | None) -> int | None:
    """宿主过期立即重捕；引擎过期按 details 退避；已故障则停止唤醒。"""
    if last_error is not None:
        return None
    if error.origin == "host":
        return 0
    return int(error.details.get("retry_after_ms") or stale_retry_delay_ms(1))


class EngineRuntime:
    """单一启动实例；失败保留结算入口，并停止新的自动推进。"""

    def __init__(
        self, store: Any, scheduler: Any, transport: EngineTransport, *,
        engine_id: str, config: dict[str, Any], domain_id: str = "station",
        continuation_authority: Callable[[str], None] | None = None,
        local_continuation_authority: Callable[[str], None] | None = None,
        restore_mode: str = "resume", allow_model_reset: bool = False,
        timer_factory: Callable[..., Any] = threading.Timer,
        timing_profiles: dict[str, Any] | None = None,
        timeout_ms: int | None = None, compute_budget_ms: int | None = None,
        apply_budget_ms: int | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.journal = SchedulingJournal(store)
        self.step_permits = StepPermitStore(store, domain_id)
        self._step_scopes: dict[tuple[str, str], StepPermit] = {}
        session_kwargs: dict[str, Any] = {}
        if timeout_ms is not None:
            session_kwargs["timeout_ms"] = timeout_ms
        if compute_budget_ms is not None:
            session_kwargs["compute_budget_ms"] = compute_budget_ms
        if apply_budget_ms is not None:
            session_kwargs["apply_budget_ms"] = apply_budget_ms
        self.session = SchedulingSession(
            self.journal, transport, domain_id=domain_id, engine_id=engine_id, config=config,
            required_capabilities=("core.v1", RESOURCE_BINDINGS_CAPABILITY),
            **session_kwargs,
        )
        self.projection = RuntimeSnapshot(
            store, scheduler, self.journal, domain_id=domain_id, timing_profiles=timing_profiles,
            step_permits=self.step_permits,
        )
        self.feedback = RuntimeFeedback(self.journal, domain_id)
        self.snapshot: dict[str, Any] = {}
        self.decision: dict[str, Any] = {}
        self.intents: dict[tuple[str, str], dict[str, Any]] = {}
        self.options: dict[str, dict[str, Any]] = {}
        self.continuation_authority = continuation_authority
        self.local_continuation_authority = local_continuation_authority
        self._closed = False
        self._wakeup = EngineWakeup(lambda: scheduler._wake_reconcile(wait=False), timer_factory=timer_factory)
        require(restore_mode in {"resume", "rebuild"}, "CONFIG_INVALID", "恢复模式必须为 resume 或 rebuild")
        require(not allow_model_reset or restore_mode == "rebuild", "CONFIG_INVALID", "模型重置必须显式重建")
        self.startup_result = self.session.start(rebuild=restore_mode == "rebuild", allow_model_reset=allow_model_reset)
        self.step_permits.recover(self.session.run_epoch)
        for warning in self.startup_result["warnings"]:
            logging.getLogger(__name__).warning("调度引擎恢复警告 [%s]：%s", warning["code"], warning["message"])
        self._fresh = store.list_tasks(page=1, page_size=1)["total"] == 0

    def refresh(self) -> bool:
        if self._closed:
            return False
        try:
            with self.journal.store.read():
                self.snapshot = self.projection.capture()
                if reconcile_resource_waits(self.journal, self.session.domain_id, self.snapshot):
                    self.snapshot = self.projection.capture()
                sources_pending = self.feedback.reconcile(self.snapshot)
            if self.session.last_error is not None:
                self._wakeup.cancel()
                return False
            accepted = self.session.decide(self.snapshot)
            for _ in range(7):
                if accepted["decision"]["status"] != "sync":
                    break
                # 每轮最多八个反馈批次，长历史在释放执行锁后继续追平。
                accepted = self.session.decide(self.snapshot)
            if (
                self._fresh and not sources_pending and accepted["decision"]["status"] != "sync"
                and self.journal.read(self.session.domain_id)["lifecycle"] == "recovering"
            ):
                try:
                    self.session.ready()
                except EngineError as error:
                    if error.code != "FEEDBACK_GAP":
                        raise
                    # 求解期间可有新事实提交；保留恢复屏障并追平，不能误报引擎故障。
                    sources_pending = True
                else:
                    self._fresh = False
                    self.snapshot = self.projection.capture()
                    accepted = self.session.decide(self.snapshot)
            self.decision = accepted["decision"]
            self.intents = {node_key(intent["node"]): intent for intent in self.decision["dispatch_now"]}
            self.options = {option["option_id"]: option for option in self.snapshot["resource_view"]["options"]}
            wakeup_ms = self.decision["wakeup_after_ms"]
            if sources_pending or self.decision["status"] == "sync":
                # 排空仍须追平已提交事实；完成后停止，不开启新的自主调度。
                wakeup_ms = 0
            elif self.journal.read(self.session.domain_id)["lifecycle"] == "draining":
                # 排空只处理外部事实与已准入续行，不接受新的自主重算计时器。
                wakeup_ms = None
            elif self.decision["status"] == "advance":
                # D 到期只撤销尚未准入的推进；下一轮检查真实 A/S，绝不重发。
                ttl = self.session.apply_budget_ms
                wakeup_ms = ttl if wakeup_ms is None else min(ttl, wakeup_ms)
            self._wakeup.schedule(wakeup_ms)
            return True
        except EngineError as error:
            self.intents = {}
            self.decision = {}
            if error.code == "STALE_DECISION":
                delay = stale_wakeup_delay_ms(error, last_error=self.session.last_error)
                if delay is None:
                    self._wakeup.cancel()
                else:
                    self._wakeup.schedule(delay)
            elif error.code == "DEADLINE_EXCEEDED" and self.session.last_error is None:
                self._wakeup.schedule(min(1000, max(50, self.session.timeout_ms // 10)))
            elif error.code != "STALE_DECISION":
                self.fault(error)
            return False
        except sqlite3.Error:
            self.fault(EngineError("PERSISTENCE_FAILED", "调度事实或反馈事务未提交"))
            return False

    def fault(self, error: EngineError) -> None:
        self.intents = {}
        self.decision = {}
        self._wakeup.cancel()
        self.session.fault(error)

    def capture_observation(self, job_uuid: str) -> dict[str, Any] | None:
        """只采集本进程完整观测的派发到结果时长；恢复推断和人工等待不采样。"""
        live = self.scheduler._inflight.get(job_uuid)
        if live is None or not live.timing_origin_observed:
            return None
        run = self.scheduler._workflows.get(live.workflow_id)
        node = run.node(live.node_id) if run else None
        job = self.journal.store.get_job(job_uuid)
        if (
            node is None or node.waits_for_manual_confirmation() or job.get("uncertainty_reason")
            or not job.get("current_attempt", True) or job.get("execution_source") == "error_manual"
            or run.state.value != "running" or run.dispatch_gate != "active"
        ):
            return None
        if live.completion_observed_at is None:
            live.completion_observed_at = self.scheduler._clock()
        elapsed = (live.completion_observed_at - live.dispatched_at) * 1000
        if not math.isfinite(elapsed) or elapsed <= 0:
            return None
        context = (job.get("control_data") or {}).get("scheduling_observation_context") or {}
        profile = context.get("timing_profile") or {}
        return {
            "occurred_at_ms": round(live.completion_observed_at * 1000),
            "observation": {
                "observation_id": job_uuid, "node": {"task_id": live.workflow_id, "node_id": live.node_id},
                "attempt_id": job_uuid, "action_id": context.get("action_id") or live.device_action_key,
                "action_revision": context.get("action_revision") or job.get("definition_fingerprint") or "",
                "device_id": live.device_material_uuid or live.device_id or None,
                "parameters": deepcopy(live.resolved_args), "elapsed_ms": round(elapsed),
                "timing_boundary": OBSERVED_DISPATCH_BOUNDARY, "outcome": "success", "complete": True,
                "calibration": False, "provenance": "live", "source_ref": job_uuid,
                "timing_spec_id": profile.get("spec_id"), "timing_spec_version": profile.get("spec_version"),
            },
        }

    def recover(self, *, rebuild: bool = False, allow_model_reset: bool = False) -> bool:
        """进程内清除 engine_fault；rebuild=True 时重建引擎实例。"""
        if rebuild:
            self.session.rebuild(allow_model_reset=allow_model_reset)
            self._fresh = True
        else:
            self.session.recover()
            self._fresh = True
        self.step_permits.recover(self.session.run_epoch)
        self._step_scopes.clear()
        return self.refresh()

    def recovery_complete(self) -> None:
        """平台完成事实、占用和路由恢复后开放；旧 head 仍不能签发新准入。"""
        if self.session.last_error is not None:
            return
        self.journal.set_lifecycle(self.session.domain_id, self.session.run_epoch, "recovering")
        self._fresh = True
        self.refresh()

    def begin_drain(self) -> None:
        self._wakeup.cancel()
        if self.session.last_error is None:
            self.journal.set_lifecycle(self.session.domain_id, self.session.run_epoch, "draining")
        self.intents = {}
        self.decision = {}

    def resume_from_drain(self) -> None:
        if self.session.last_error is None:
            self.recovery_complete()

    def local_allowed(self, task_id: str, node_id: str) -> bool:
        node = next((node for node in self.snapshot.get("task_view", {}).get("nodes", [])
                     if node["ref"] == {"task_id": task_id, "node_id": node_id}), None)
        return bool(
            node and node["ready"] and node["parameters_final"]
            and self.decision.get("status") == "advance"
            and self.journal.authorized(
                self.session.domain_id, self.session.run_epoch,
                self.decision["decision_id"], task_id,
            )
        )

    def report_preparation_error(self) -> bool:
        """仅在有效 advance 下提交当前节点的准备失败，错误本身不取得执行占用。"""
        control = self.scheduler._station_control
        if control is None:
            return False
        tasks = {task["task_id"]: task for task in self.snapshot.get("task_view", {}).get("tasks", [])}
        pools = {pool["pool_id"]: pool for pool in self.snapshot.get("capacity_view", {}).get("pools", [])}
        for node in self.snapshot.get("task_view", {}).get("nodes", []):
            diagnostic = preparation_error(node)
            task_id, node_id = node_key(node["ref"])
            if (
                diagnostic is None or not node_allowed(tasks[task_id], node)
                or not self.local_allowed(task_id, node_id)
                or not task_capacity_available(tasks[task_id], pools)
            ):
                continue
            run = self.scheduler._workflows[task_id]
            current = run.node(node_id)
            try:
                authority = self.local_commit_authority(task_id, node_id, application="prepare_error")
                verify_decision = authority.verify_decision

                def verify(connection: sqlite3.Connection) -> None:
                    verify_decision(connection)
                    parameters = run.resolve_params(node_id)
                    require(parameters == node["parameters"], "STALE_DECISION", "准备错误的参数已经改变")
                    # 库存和参数可能在求解期间修正；事务前后重新证明同一错误仍成立。
                    try:
                        resource_bindings(
                            self.scheduler, current, parameters, self.projection._devices(current)[0],
                        )
                    except MaterialIOError as error:
                        require(
                            PREPARATION_ERROR_PREFIX + error.code == diagnostic["code"]
                            and str(error) == diagnostic["message"],
                            "STALE_DECISION", "准备错误的库存事实已经改变",
                        )
                    else:
                        raise EngineError("STALE_DECISION", "准备错误已消失，等待重新投影")

                control.report_error(
                    node["job_id"], phase="prepare", message=diagnostic["message"],
                    error_code=diagnostic["code"].removeprefix(PREPARATION_ERROR_PREFIX),
                    scheduling_authority=replace(authority, verify_decision=verify),
                )
                run.hold_error(node_id)
                self.intents = {}
                self._wakeup.schedule(0)
            except EngineError as error:
                self.defer_local_control(error)
            except sqlite3.Error:
                self.fault(EngineError("PERSISTENCE_FAILED", "节点准备失败未能持久化"))
            return True
        return False

    def begin_step(self, task_id: str, node_id: str, *, application: str = "") -> StepPermit | None:
        """由平台已选定节点开立额度；引擎只读取事实，不签发用户单步许可。"""
        if self._closed or self.session.last_error is not None:
            return None
        try:
            run = self.scheduler._workflows[task_id]
            graph = RuntimeGraph(self.journal.store.get_task(task_id), self.journal.store.list_jobs(task_id), run)
            job = graph.jobs[node_id]
            if application:
                require(graph.loop_transition_ready(node_id), "STALE_DECISION", "循环轮末尚未具备推进事实")
            permit = self.step_permits.issue(
                run_epoch=self.session.run_epoch, task_id=task_id, node_id=node_id,
                job_id=job["uuid"], attempt_no=job["attempt"], expected_application=application,
            )
            self._step_scopes[(task_id, node_id)] = permit
            return permit
        except EngineError as error:
            # 门禁未开放时等待新的平台事实；不能由单步申请自己持续唤醒重试。
            if error.code != "STALE_DECISION":
                self.fault(error)
        except sqlite3.Error:
            self.fault(EngineError("PERSISTENCE_FAILED", "单步许可未持久化"))
        return None

    def end_step(self, permit: StepPermit | None) -> None:
        if permit is None:
            return
        self._step_scopes.pop((permit.task_id, permit.node_id), None)
        try:
            self.step_permits.close(permit)
        except sqlite3.Error:
            self.fault(EngineError("PERSISTENCE_FAILED", "未消费的单步许可未能撤销"))

    @staticmethod
    def local_application(event: Any) -> str:
        return "local:" + encode_message({
            "kind": event.get("control_type"), "phase": event.get("phase"),
            "iteration": event.get("iteration_index"),
        }).decode("utf-8")

    def local_commit_authority(self, task_id: str, node_id: str, *, application: str) -> LocalControlAuthority:
        """冻结本次控制提交使用的 D，不能在事务中借用后来接受的其他决策。"""
        require(self.local_allowed(task_id, node_id), "STALE_DECISION", "本地控制缺少有效调度授权")
        domain_id, run_epoch = self.session.domain_id, self.session.run_epoch
        decision_id = self.decision["decision_id"]
        node = next(node for node in self.snapshot["task_view"]["nodes"]
                    if node["ref"] == {"task_id": task_id, "node_id": node_id})
        task = next(task for task in self.snapshot["task_view"]["tasks"] if task["task_id"] == task_id)
        step_permit = self._step_scopes.get((task_id, node_id))
        is_step = task["execution_mode"] == "step"
        if is_step:
            require(
                step_permit is not None and step_permit.job_id == node["job_id"]
                and step_permit.attempt_no == node["attempt_no"],
                "STALE_DECISION", "本地控制缺少当前尝试的持久单步许可",
            )

        def verify(connection: sqlite3.Connection) -> None:
            self.journal.require_local_authority(
                connection, domain_id, run_epoch=run_epoch, decision_id=decision_id, task_id=task_id,
            )
            if is_step:
                assert step_permit is not None
                self.step_permits.verify(connection, step_permit, application=application)

        def consume_step(connection: sqlite3.Connection) -> None:
            assert step_permit is not None
            self.step_permits.consume(connection, step_permit, application=application)

        def platform_allowed() -> bool:
            run = self.scheduler._workflows.get(task_id)
            if run is None or (
                self.scheduler._station_control is not None
                and not self.scheduler._station_control.allows_task(task_id)
            ):
                return False
            if task["execution_mode"] == "step":
                return bool(
                    node["step_authorized"] and (
                        self.scheduler._step_targets.get(task_id) == node_id
                        or RuntimeGraph(
                            self.journal.store.get_task(task_id), self.journal.store.list_jobs(task_id), run,
                        ).loop_transition_ready(node_id)
                    )
                )
            return run.state.value == "running" and run.dispatch_gate == "active"

        return LocalControlAuthority(
            task_id=task_id, job_id=node["job_id"], attempt_no=node["attempt_no"],
            execution_mode=task["execution_mode"],
            max_active_tasks=self.scheduler.max_active_tasks,
            max_tasks_per_workflow=self.scheduler.max_tasks_per_workflow,
            verify_decision=verify, platform_allowed=platform_allowed,
            consume_step=consume_step if is_step else None,
        )

    def defer_local_control(self, error: EngineError) -> None:
        """控制事务失效后保留原节点，按普通竞争重算；持久化故障则关闭自动推进。"""
        if error.code == "STALE_DECISION":
            self._wakeup.schedule(0)
        else:
            self.fault(error)

    def order(self, ready: list[Any]) -> list[Any]:
        by_key = {(task.workflow_id, task.node.id): task for task in ready}
        ordered = []
        for key, intent in self.intents.items():
            if key not in by_key or intent["kind"] == "continue_attempt":
                continue
            task = by_key[key]
            target = self.options[intent["option_id"]]["binding_facts"]["actual_executor"]
            ordered.append(replace(task, node=replace(
                task.node, device_id=target["local_device_id"], device_material_uuid=target["material_uuid"],
            )))
        return ordered

    def selected_option(self, task_id: str, node_id: str) -> dict[str, Any]:
        """返回本轮已接受的完整资源方案副本，宿主复验时不能改选其他候选。"""
        intent = self.intents[(task_id, node_id)]
        return deepcopy(self.options[intent["option_id"]])

    def admission(self, dispatching: dict[str, Any]) -> Any:
        """把候选固定为事务回调；真正 A 提交前再次复验 D 和最终绑定。"""
        key = (dispatching["workflow_id"], dispatching["node_id"])
        intent = deepcopy(self.intents.get(key))
        if intent is None or intent["kind"] == "continue_attempt":
            raise EngineError("STALE_DECISION", "派发缺少本轮意图")
        option = deepcopy(self.options[intent["option_id"]])
        node = next(node for node in self.snapshot["task_view"]["nodes"] if node["ref"] == intent["node"])
        observation_context = {
            "action_id": node["action_id"], "action_revision": node["action_revision"] or "",
            "timing_profile": deepcopy(option["timing_profile"]),
        }
        decision_id = self.decision["decision_id"]
        step_permit = self._step_scopes.get(key)
        task_mode = next(task["execution_mode"] for task in self.snapshot["task_view"]["tasks"]
                         if task["task_id"] == key[0])

        def commit(connection: sqlite3.Connection, job: dict[str, Any]) -> None:
            data = job["control_data"]
            expected_executor = {key: value for key, value in option["binding_facts"]["actual_executor"].items() if value}
            require(
                job["attempt"] == intent["attempt_no"] and job["param"] == option["resolved_parameters"]
                and data.get("actual_executor") == expected_executor
                and {item["lock_key"] for item in data["execution_locks"]} == set(option["resource_ids"]),
                "STALE_DECISION", "实际准入参数或资源与已接受候选不一致",
            )
            task = connection.execute(
                "SELECT execution_mode,control_status,dispatch_gate,status FROM workflow_task WHERE uuid=?",
                (key[0],),
            ).fetchone()
            require(
                task is not None and task["execution_mode"] == task_mode
                and task["status"] in {"pending", "running"}
                and task["control_status"] in ({"active", "paused"} if task_mode == "step" else {"active"})
                and task["dispatch_gate"] == ("paused" if task_mode == "step" else "active"),
                "STALE_DECISION", "准入时 Task 门禁或执行模式已变化",
            )
            self.journal.admit(
                connection, self.session.domain_id, run_epoch=self.session.run_epoch, decision_id=decision_id,
                intent_id=intent["intent_id"], attempt_id=job["uuid"],
                command_ids=[job["edge_command_uuid"]] if job.get("edge_command_uuid") else [],
                occurred_at_ms=round(self.scheduler._clock() * 1000),
            )
            if task_mode == "step":
                require(step_permit is not None, "STALE_DECISION", "准入缺少持久单步许可")
                assert step_permit is not None
                self.step_permits.consume(connection, step_permit, application="admission", attempt_id=job["uuid"])
            # 与 A 同时冻结；原尝试续行或更换宿主目录时不得改写采集规则身份。
            connection.execute(
                "UPDATE workflow_node_job SET control_data=? WHERE uuid=?",
                (encode_message({**data, "scheduling_observation_context": observation_context}).decode("utf-8"), job["uuid"]),
            )
            self.journal.require_local_authority(
                connection, self.session.domain_id, run_epoch=self.session.run_epoch,
                decision_id=decision_id, task_id=key[0],
            )

        return commit

    def start_allowed(self, job_uuid: str) -> bool:
        if not self.journal.attempt_authorized(self.session.domain_id, self.session.run_epoch, job_uuid):
            return False
        job = self.journal.store.get_job(job_uuid)
        task = self.journal.store.get_task(job["workflow_task_uuid"])
        return task["execution_mode"] != "step" or self.step_permits.bound_to(
            task["uuid"], str((job.get("control_data") or {}).get("runtime_node_id") or job["workflow_node_uuid"]),
            job_uuid, job["attempt"],
        )

    def reject_admission(self, dispatching: dict[str, Any], *, code: str, message: str) -> bool:
        intent = self.intents.get((dispatching["workflow_id"], dispatching["node_id"]))
        if intent is None:
            return False
        try:
            rejected = self.journal.reject_intent(
                self.session.domain_id, intent["intent_id"],
                reason=reason(code, message, task_id=dispatching["workflow_id"]),
                occurred_at_ms=round(self.scheduler._clock() * 1000),
            )
            if rejected:
                self.intents.pop((dispatching["workflow_id"], dispatching["node_id"]), None)
                self._wakeup.schedule(0)
            return rejected
        except sqlite3.Error as error:
            failure = EngineError("PERSISTENCE_FAILED", "准入拒绝反馈未提交")
            self.fault(failure)
            raise failure from error

    def submitted(self, job_uuid: str) -> None:
        """执行器已接收原命令；写失败交回平台冻结占用，不能当作未派发。"""
        try:
            with self.journal.store.transaction() as connection:
                known = self.journal.report_delivery(
                    connection, self.session.domain_id, job_uuid, disposition="submitted",
                    occurred_at_ms=round(self.scheduler._clock() * 1000),
                )
                require(
                    known or self.journal.store.get_job(job_uuid)["execution_source"] == "error_manual",
                    "INVALID_DECISION", "执行器交付没有已准入的引擎意图",
                )
        except sqlite3.Error as error:
            failure = EngineError("PERSISTENCE_FAILED", "交付结果反馈未提交")
            self.fault(failure)
            raise failure from error
        except EngineError as error:
            self.fault(error)
            raise

    def continue_admitted(self, attempt_id: str) -> None:
        """恢复已有 A 的原命令；实际 Claim、控制和 Job 仍由平台事务复验。"""
        if self.start_allowed(attempt_id):
            return
        require(self.continuation_authority is not None, "ENGINE_UNAVAILABLE", "原尝试续行权威未装配")
        if self.continuation_authority is not None:
            self.continuation_authority(attempt_id)

    def continue_local(self, attempt_id: str) -> None:
        """调用平台已有的本地人工操作入口；库存效果不进入算法引擎。"""
        require(self.local_continuation_authority is not None, "ENGINE_UNAVAILABLE", "本地人工续行权威未装配")
        if self.local_continuation_authority is not None:
            self.local_continuation_authority(attempt_id)

    def continuation_allowed(self, task_id: str, node_id: str, attempt_id: str) -> bool:
        if self.journal.read(self.session.domain_id)["lifecycle"] == "draining":
            return self.start_allowed(attempt_id)
        intent = self.intents.get((task_id, node_id))
        return bool(intent and intent["kind"] == "continue_attempt" and intent["attempt_id"] == attempt_id)

    def continuation(self, job: dict[str, Any]) -> Any:
        if self.journal.read(self.session.domain_id)["lifecycle"] == "draining":
            def preserve_admission(connection: sqlite3.Connection) -> None:
                require(self.start_allowed(job["uuid"]), "STALE_DECISION", "排空只允许本世代已经准入的原尝试")
            return preserve_admission
        node_id = str((job.get("control_data") or {}).get("runtime_node_id") or job["workflow_node_uuid"])
        key = (job["workflow_task_uuid"], node_id)
        intent = deepcopy(self.intents.get(key))
        if intent is None or intent["kind"] != "continue_attempt" or intent["attempt_id"] != job["uuid"]:
            raise EngineError("STALE_DECISION", "人工续行缺少原 attempt 的本轮意图")
        decision_id = self.decision["decision_id"]

        def commit(connection: sqlite3.Connection) -> None:
            task = self.journal.store.get_task(job["workflow_task_uuid"])
            require(
                task["execution_mode"] != "step" or self.step_permits.bound_to(
                    task["uuid"], node_id, job["uuid"], job["attempt"],
                ), "STALE_DECISION", "原尝试的单步许可已失效",
            )
            self.journal.admit(
                connection, self.session.domain_id, run_epoch=self.session.run_epoch, decision_id=decision_id,
                intent_id=intent["intent_id"], attempt_id=job["uuid"],
                command_ids=[job["edge_command_uuid"]] if job.get("edge_command_uuid") else [],
                occurred_at_ms=round(self.scheduler._clock() * 1000),
            )

        return commit

    def close(self) -> None:
        self._closed = True
        self._wakeup.close()
        self.session.close()
