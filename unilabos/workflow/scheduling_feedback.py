"""把平台持久 outbox 投影成引擎反馈；反馈和消费游标在同一事务内提交。"""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from unilabos.scheduling.engine import reason
from unilabos.workflow.execution_claim import get_execution_claim
from unilabos.workflow.execution_started import publish_execution_starts
from unilabos.workflow.scheduling_journal import SchedulingJournal
from unilabos.workflow.scheduling_observation import publish_observations

_OUTCOMES = {"succeeded": "success", "failed": "failure", "canceled": "canceled", "timeout": "timeout"}
SOURCE_BATCH_SIZE = 256


class RuntimeFeedback:
    """事件是消费切面上的权威值；不把旧日志时间伪装成物理开始时刻。"""

    def __init__(self, journal: SchedulingJournal, domain_id: str) -> None:
        self.journal, self.domain_id = journal, domain_id

    def reconcile(self, snapshot: dict[str, Any]) -> bool:
        """每个源最多投影一个批次；余量由宿主唤醒继续，不删除原始证据。"""
        store = self.journal.store
        tasks = {task["task_id"]: task for task in snapshot["task_view"]["tasks"]}
        with store.transaction() as connection:
            samples_pending = publish_observations(
                connection, self.domain_id, self.journal.append_feedback, limit=SOURCE_BATCH_SIZE,
            )
            starts_pending = publish_execution_starts(
                connection, self.domain_id, self.journal.append_feedback, limit=SOURCE_BATCH_SIZE,
            )
            cursor = connection.execute(
                "SELECT source_cursor FROM scheduler_engine_domain WHERE domain_id=?", (self.domain_id,),
            ).fetchone()[0]
            # 只消费本次事实切面的有限前缀；重放或反馈 ACK 不会制造新源事件。
            rows = connection.execute(
                "SELECT sequence,workflow_task_uuid,workflow_node_job_uuid,kind,to_status "
                "FROM workflow_runtime_journal WHERE sequence>? ORDER BY sequence LIMIT ?",
                (cursor, SOURCE_BATCH_SIZE),
            ).fetchall()
            if not rows:
                return samples_pending or starts_pending
            last = rows[-1]["sequence"]
            affected_tasks = {row["workflow_task_uuid"] for row in rows}
            affected_jobs = {
                row["workflow_node_job_uuid"] for row in rows
                if row["workflow_node_job_uuid"] and (
                    row["kind"] in {"uncertainty_opened", "uncertainty_resolved", "lock_operator_released"}
                    or (row["kind"] == "job_transition" and row["to_status"] in _OUTCOMES)
                )
            }

            def emit(identity: str, payload: dict[str, Any]) -> None:
                self.journal.append_feedback(
                    connection, self.domain_id,
                    event_id=str(uuid5(NAMESPACE_URL, f"unilab.scheduler.feedback:{self.domain_id}:{last}:{identity}")),
                    occurred_at_ms=snapshot["captured_at_ms"], payload=payload,
                )

            for job_id in sorted(affected_jobs):
                job = store.get_job(job_id)
                # 本地条件节点没有设备尝试，不能伪装成物理结果。
                if job["executor_kind"] in {"condition", "repeat_until", "workflow_command", "material_source"}:
                    continue
                if job["status"] not in _OUTCOMES and not job.get("uncertainty_reason"):
                    continue
                data = job.get("control_data") or {}
                claim = get_execution_claim(connection, job_uuid=job_id)
                uncertain = bool(job.get("uncertainty_reason"))
                if uncertain:
                    self.journal.report_delivery(
                        connection, self.domain_id, job_id, disposition="uncertain",
                        occurred_at_ms=snapshot["captured_at_ms"],
                        reason=reason("DELIVERY_UNCERTAIN", "原尝试的交付或执行事实待核实"),
                    )
                emit("result:" + job_id, {
                    "kind": "execution_result", "node": {
                        "task_id": job["workflow_task_uuid"],
                        "node_id": str(data.get("runtime_node_id") or job["workflow_node_uuid"]),
                    },
                    "attempt_id": job_id, "outcome": "unknown" if uncertain else _OUTCOMES[job["status"]],
                    "terminal_confirmed": not uncertain and job["status"] in _OUTCOMES,
                    "resources_settled": not uncertain and (claim is None or claim["state"] == "released"),
                    "unknown_command_ids": [job["edge_command_uuid"]] if uncertain and job.get("edge_command_uuid") else [],
                    "result": job.get("return_info") or {}, "errors": job.get("error_info") or [],
                })
            for task_id in sorted(affected_tasks & tasks.keys()):
                emit("task:" + task_id, {"kind": "task_changed", "task": tasks[task_id]})
            resource_ids = sorted({
                resource for allocation in snapshot["resource_view"]["allocations"]
                if allocation["owner_task_id"] in affected_tasks for resource in allocation["resource_ids"]
            })
            emit("resources", {
                "kind": "resources_changed", "resource_ids": resource_ids,
                "reason": reason("PLATFORM_FACTS_CHANGED", "平台事实已提交，完整占用以当前快照为准"),
            })
            connection.execute(
                "UPDATE scheduler_engine_domain SET source_cursor=? WHERE domain_id=?", (last, self.domain_id),
            )
            return samples_pending or starts_pending or connection.execute(
                "SELECT 1 FROM workflow_runtime_journal WHERE sequence>? LIMIT 1", (last,),
            ).fetchone() is not None
