"""本地控制提交的宿主授权；状态与容量检查和业务事实共用工作流写事务。"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from unilabos.scheduling.protocol import require
from unilabos.workflow.event_writer import append_runtime_event
from unilabos.workflow.scheduler_capacity import activate_workflow_task
from unilabos.workflow.station_status_projection import append_task_state_event
from unilabos.workflow.store import utc_now


@dataclass(frozen=True, slots=True)
class LocalControlAuthority:
    """入口验证尚可推进，出口仅复验门禁，允许本次业务合法结束 Task/Job。"""

    task_id: str
    job_id: str
    attempt_no: int
    execution_mode: str
    max_active_tasks: int
    max_tasks_per_workflow: int
    verify_decision: Callable[[sqlite3.Connection], None]
    platform_allowed: Callable[[], bool]
    consume_step: Callable[[sqlite3.Connection], None] | None = None

    def _verify(self, connection: sqlite3.Connection) -> None:
        self.verify_decision(connection)
        task = connection.execute(
            "SELECT execution_mode, control_status, dispatch_gate FROM workflow_task "
            "WHERE uuid=? AND deleted_at IS NULL", (self.task_id,),
        ).fetchone()
        require(task is not None, "STALE_DECISION", "本地控制 Task 已不存在")
        step = self.execution_mode == "step"
        require(
            task["execution_mode"] == self.execution_mode
            and task["control_status"] in ({"active", "paused"} if step else {"active"})
            and task["dispatch_gate"] == ("paused" if step else "active")
            and self.platform_allowed(),
            "STALE_DECISION", "本地控制的 Task 或工站门禁已改变",
        )

    def __call__(self, connection: sqlite3.Connection) -> None:
        self._verify(connection)
        if self.consume_step is not None:
            self.consume_step(connection)
            self.verify_decision(connection)

    def commit_error(self, connection: sqlite3.Connection) -> None:
        """错误处置自身改变门禁，仍须在同一事务复验 D 并消费本次单步额度。"""
        self.verify_decision(connection)
        if self.consume_step is not None:
            self.consume_step(connection)
            self.verify_decision(connection)

    def before_effect(self, connection: sqlite3.Connection) -> None:
        self._verify(connection)
        task = connection.execute(
            "SELECT status FROM workflow_task WHERE uuid=? AND deleted_at IS NULL", (self.task_id,),
        ).fetchone()
        job = connection.execute(
            "SELECT workflow_task_uuid, status, current_attempt, attempt, edge_command_uuid, uncertainty_reason "
            "FROM workflow_node_job WHERE uuid=? AND deleted_at IS NULL", (self.job_id,),
        ).fetchone()
        require(
            task["status"] in {"pending", "running"}
            and job is not None and job["workflow_task_uuid"] == self.task_id
            and job["status"] == "pending" and job["current_attempt"]
            and job["attempt"] == self.attempt_no
            and not job["edge_command_uuid"] and not job["uncertainty_reason"],
            "STALE_DECISION", "本地控制的 Task 或当前尝试已不可推进",
        )
        capacity = activate_workflow_task(
            connection, task_uuid=self.task_id, max_active_tasks=self.max_active_tasks,
            max_tasks_per_workflow=self.max_tasks_per_workflow,
        )
        require(capacity.available, "STALE_DECISION", "本地控制的 Task 容量已不可用")
        if capacity.activated:
            now = utc_now()
            append_runtime_event(
                connection, task_uuid=self.task_id, kind="task_transition",
                from_status="pending", to_status="running", now=now,
            )
            started_at = connection.execute(
                "SELECT started_at FROM workflow_task WHERE uuid=?", (self.task_id,),
            ).fetchone()[0]
            append_task_state_event(
                connection, task_uuid=self.task_id, status="running", details={"started_at": started_at},
            )
