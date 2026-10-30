"""平台单步额度：与本地事实或 A 共事务消费，绑定原尝试的额度可供人工续行。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from unilabos.scheduling.protocol import require
from unilabos.workflow.store import WorkflowStore, utc_now


@dataclass(frozen=True, slots=True)
class StepPermit:
    permit_id: str
    domain_id: str
    run_epoch: str
    task_id: str
    node_id: str
    job_id: str
    attempt_no: int
    expected_application: str


class StepPermitStore:
    def __init__(self, store: WorkflowStore, domain_id: str) -> None:
        self.store, self.domain_id = store, domain_id
        with store.transaction() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS workflow_scheduler_step_permit (
                permit_id TEXT PRIMARY KEY, domain_id TEXT NOT NULL, run_epoch TEXT NOT NULL,
                task_id TEXT NOT NULL, node_id TEXT NOT NULL, job_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
                expected_application TEXT NOT NULL, application TEXT, attempt_id TEXT,
                status TEXT NOT NULL CHECK(status IN ('offered','bound','consumed','revoked')),
                created_at TEXT NOT NULL, consumed_at TEXT, revoked_at TEXT)""")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS scheduler_step_offered
                ON workflow_scheduler_step_permit(domain_id,task_id) WHERE status='offered'""")
            connection.execute("""CREATE INDEX IF NOT EXISTS scheduler_step_attempt
                ON workflow_scheduler_step_permit(domain_id,task_id,node_id,job_id,attempt_no,status)""")

    def recover(self, run_epoch: str) -> None:
        """旧世代尚未消费的意图不恢复；已绑定真实尝试的额度保留原事实。"""
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE workflow_scheduler_step_permit SET status='revoked',revoked_at=? "
                "WHERE domain_id=? AND run_epoch<>? AND status='offered'",
                (utc_now(), self.domain_id, run_epoch),
            )

    def issue(
        self, *, run_epoch: str, task_id: str, node_id: str, job_id: str,
        attempt_no: int, expected_application: str = "",
    ) -> StepPermit:
        permit = StepPermit(str(uuid4()), self.domain_id, run_epoch, task_id, node_id, job_id,
                            attempt_no, expected_application)
        with self.store.transaction() as connection:
            self._require_epoch(connection, run_epoch)
            task = connection.execute(
                "SELECT status,execution_mode,control_status,dispatch_gate FROM workflow_task "
                "WHERE uuid=? AND deleted_at IS NULL", (task_id,),
            ).fetchone()
            job = connection.execute(
                "SELECT * FROM workflow_node_job WHERE uuid=? AND deleted_at IS NULL", (job_id,),
            ).fetchone()
            require(
                task is not None and task["status"] in {"pending", "running"}
                and task["execution_mode"] == "step" and task["dispatch_gate"] == "paused"
                and task["control_status"] in {"active", "paused"},
                "STALE_DECISION", "单步 Task 门禁已变化",
            )
            require(
                job is not None and job["workflow_task_uuid"] == task_id and job["status"] == "pending"
                and job["current_attempt"] and job["attempt"] == attempt_no
                and not job["edge_command_uuid"] and not job["uncertainty_reason"]
                and (json.loads(job["control_data"]).get("runtime_node_id") or job["workflow_node_uuid"]) == node_id,
                "STALE_DECISION", "单步目标不再是待执行的当前尝试",
            )
            connection.execute(
                "UPDATE workflow_scheduler_step_permit SET status='revoked',revoked_at=? "
                "WHERE domain_id=? AND task_id=? AND status='offered'",
                (utc_now(), self.domain_id, task_id),
            )
            connection.execute(
                "INSERT INTO workflow_scheduler_step_permit "
                "(permit_id,domain_id,run_epoch,task_id,node_id,job_id,attempt_no,expected_application,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,'offered',?)",
                (permit.permit_id, permit.domain_id, permit.run_epoch, permit.task_id, permit.node_id,
                 permit.job_id, permit.attempt_no, permit.expected_application, utc_now()),
            )
        return permit

    def _require_epoch(self, connection: sqlite3.Connection, run_epoch: str) -> None:
        row = connection.execute(
            "SELECT run_epoch,lifecycle FROM scheduler_engine_domain WHERE domain_id=?", (self.domain_id,),
        ).fetchone()
        require(row is not None and row["run_epoch"] == run_epoch and row["lifecycle"] == "running",
                "STALE_DECISION", "单步许可不属于当前可运行世代")

    def verify(self, connection: sqlite3.Connection, permit: StepPermit, *, application: str) -> None:
        self._require_epoch(connection, permit.run_epoch)
        row = connection.execute(
            "SELECT * FROM workflow_scheduler_step_permit WHERE permit_id=?", (permit.permit_id,),
        ).fetchone()
        require(
            row is not None and all(row[name] == getattr(permit, name) for name in (
                "domain_id", "run_epoch", "task_id", "node_id", "job_id", "attempt_no", "expected_application",
            )) and row["status"] in {"offered", "bound", "consumed"}
            and (not permit.expected_application or permit.expected_application == application)
            and (row["application"] is None or row["application"] == application),
            "STALE_DECISION", "单步许可已撤销、身份变化或已用于其他控制阶段",
        )

    def consume(
        self, connection: sqlite3.Connection, permit: StepPermit, *,
        application: str, attempt_id: str | None = None,
    ) -> None:
        self.verify(connection, permit, application=application)
        require(
            (application == "admission" and attempt_id == permit.job_id)
            or (application != "admission" and attempt_id is None),
            "STALE_DECISION", "单步许可不能绑定其他尝试",
        )
        connection.execute(
            "UPDATE workflow_scheduler_step_permit SET status=?,application=?,attempt_id=?,consumed_at=? "
            "WHERE permit_id=? AND status='offered'",
            ("bound" if attempt_id else "consumed", application, attempt_id, utc_now(), permit.permit_id),
        )

    def close(self, permit: StepPermit) -> None:
        """调用结束时撤销未消费额度，不撤销已经准入的原尝试。"""
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE workflow_scheduler_step_permit SET status='revoked',revoked_at=? "
                "WHERE permit_id=? AND status='offered'", (utc_now(), permit.permit_id),
            )

    def revoke_task(self, task_id: str) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE workflow_scheduler_step_permit SET status='revoked',revoked_at=? "
                "WHERE domain_id=? AND task_id=? AND status IN ('offered','bound','consumed')",
                (utc_now(), self.domain_id, task_id),
            )

    def offered(self, run_epoch: str) -> set[tuple[str, str, str, int]]:
        with self.store.read() as connection:
            rows = connection.execute(
                "SELECT task_id,node_id,job_id,attempt_no FROM workflow_scheduler_step_permit "
                "WHERE domain_id=? AND run_epoch=? AND status='offered'", (self.domain_id, run_epoch),
            ).fetchall()
            return {(row["task_id"], row["node_id"], row["job_id"], row["attempt_no"]) for row in rows}

    def bound_to(self, task_id: str, node_id: str, job_id: str, attempt_no: int) -> bool:
        with self.store.read() as connection:
            return connection.execute(
                "SELECT 1 FROM workflow_scheduler_step_permit WHERE domain_id=? AND task_id=? "
                "AND node_id=? AND job_id=? AND attempt_no=? AND attempt_id=? AND status='bound' LIMIT 1",
                (self.domain_id, task_id, node_id, job_id, attempt_no, job_id),
            ).fetchone() is not None

    def read(self, permit_id: str) -> dict:
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_scheduler_step_permit WHERE permit_id=?", (permit_id,),
            ).fetchone()
            require(row is not None, "STALE_DECISION", "单步许可不存在")
            return dict(row)
