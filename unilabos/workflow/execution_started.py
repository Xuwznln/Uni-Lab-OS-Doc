"""平台保存实际开始回执；它不授予执行许可，也不改变 Task/Job 生命周期。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from unilabos.app.edge_control.execution_observation import validate_start_receipt
from unilabos.workflow.store import StoreConflict, StoreNotFound, WorkflowStore


def ensure_execution_started_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS workflow_execution_started ("
        "sequence INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT NOT NULL UNIQUE,"
        "job_uuid TEXT NOT NULL UNIQUE, task_uuid TEXT NOT NULL, node_id TEXT NOT NULL,"
        "started_at_ms INTEGER NOT NULL, receipt_json TEXT NOT NULL)"
    )


class ExecutionStartedStore:
    def __init__(self, store: WorkflowStore) -> None:
        self.store = store
        with store.transaction() as connection:
            ensure_execution_started_schema(connection)

    def record(self, job_uuid: str, receipt: dict[str, Any]) -> dict[str, Any]:
        """一个尝试只接受一个不可变开始回执；终态和旧尝试仍可补历史。"""
        validate_start_receipt(receipt)
        encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self.store.transaction() as connection:
            job = connection.execute("SELECT * FROM workflow_node_job WHERE uuid=?", (job_uuid,)).fetchone()
            if job is None:
                raise StoreNotFound("实际开始回执的 Job 不存在")
            if (
                receipt["job_uuid"] != job_uuid or receipt["command_uuid"] != job["edge_command_uuid"]
                or receipt["attempt"] != job["attempt"]
            ):
                raise StoreConflict("实际开始回执的执行身份冲突")
            if job["start_state"] == "NOT_STARTED":
                raise StoreConflict("未提交开始许可，不能接受实际开始回执")
            existing = connection.execute(
                "SELECT receipt_json FROM workflow_execution_started WHERE job_uuid=? OR receipt_id=?",
                (job_uuid, receipt["receipt_id"]),
            ).fetchone()
            if existing is not None:
                if existing["receipt_json"] != encoded:
                    raise StoreConflict("实际开始回执与原始证据冲突")
                return {"receipt_id": receipt["receipt_id"], "created": False}
            data = json.loads(job["control_data"] or "{}")
            connection.execute(
                "INSERT INTO workflow_execution_started("
                "receipt_id,job_uuid,task_uuid,node_id,started_at_ms,receipt_json) VALUES (?,?,?,?,?,?)",
                (receipt["receipt_id"], job_uuid, job["workflow_task_uuid"],
                 data.get("runtime_node_id") or job["workflow_node_uuid"], receipt["started_at_ms"], encoded),
            )
            return {"receipt_id": receipt["receipt_id"], "created": True}


def execution_start_times(connection: sqlite3.Connection, job_ids: list[str]) -> dict[str, int]:
    """只读取本次事实切面中的作业，避免每次快照扫描全部历史。"""
    result = {}
    for offset in range(0, len(job_ids), 500):
        batch = job_ids[offset:offset + 500]
        placeholders = ",".join("?" for _ in batch)
        for row in connection.execute(
            f"SELECT job_uuid,started_at_ms FROM workflow_execution_started WHERE job_uuid IN ({placeholders})", batch,
        ):
            result[row["job_uuid"]] = row["started_at_ms"]
    return result


def publish_execution_starts(connection, domain_id: str, publish, *, limit: int) -> bool:
    """回执与消费游标同事务投影；即使结果先到也补齐原尝试的历史事件。"""
    source = connection.execute(
        "SELECT sequence FROM scheduler_engine_source_cursor WHERE domain_id=? AND source='execution_started'",
        (domain_id,),
    ).fetchone()
    after = source[0] if source else 0
    rows = connection.execute(
        "SELECT * FROM workflow_execution_started WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit),
    ).fetchall()
    last = after
    for row in rows:
        publish(
            connection, domain_id,
            event_id=str(uuid5(NAMESPACE_URL, f"unilab.execution.started:{domain_id}:{row['receipt_id']}")),
            occurred_at_ms=row["started_at_ms"],
            payload={
                "kind": "execution_started", "node": {"task_id": row["task_uuid"], "node_id": row["node_id"]},
                "attempt_id": row["job_uuid"], "started_at_ms": row["started_at_ms"],
            },
        )
        last = row["sequence"]
    if last != after:
        connection.execute(
            "INSERT INTO scheduler_engine_source_cursor(domain_id,source,sequence) VALUES (?,'execution_started',?) "
            "ON CONFLICT(domain_id,source) DO UPDATE SET sequence=excluded.sequence", (domain_id, last),
        )
    return connection.execute(
        "SELECT 1 FROM workflow_execution_started WHERE sequence>? LIMIT 1", (last,),
    ).fetchone() is not None
