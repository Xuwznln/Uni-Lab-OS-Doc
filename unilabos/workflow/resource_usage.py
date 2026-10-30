"""资源监控的只读投影：物料归属、执行占用和在途动作分别返回。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from unilabos.workflow.execution_lock_lease import list_execution_locks
from unilabos.workflow.store import utc_now


def read_resource_usage(connection: sqlite3.Connection) -> dict[str, Any]:
    """读取调用方持有存储锁期间的任务资源事实，不创建或释放任何占用。"""
    occupancies = [
        {**row, "occupancy_kind": "execution_lock"}
        for row in list_execution_locks(connection)
        if row["state"] != "released"
    ]
    occupancies.extend(
        {**dict(row), "occupancy_kind": "device_tenancy",
         "workflow_node_job_uuid": row["acquired_by_job_uuid"],
         "lock_key": row["device_lock_key"]}
        for row in connection.execute("SELECT * FROM task_device_tenancy WHERE state='active'")
    )
    bindings = [dict(row) for row in connection.execute(
        "SELECT * FROM workflow_task_material_claim WHERE status='active' AND deleted_at IS NULL"
    )]
    referenced = {row["workflow_node_job_uuid"] for row in [*occupancies, *bindings]}
    jobs = []
    # 同时包含无锁手动动作及持锁历史 Job，不能仅根据 current_attempt 删除占用来源。
    for row in connection.execute(
        "SELECT j.*,t.status AS task_status,t.workflow_snapshot,t.execution_plan "
        "FROM workflow_node_job j JOIN workflow_task t ON t.uuid=j.workflow_task_uuid "
        "WHERE j.deleted_at IS NULL AND t.deleted_at IS NULL AND ("
        "j.status IN ('dispatched','running','cancel_requested') OR "
        "j.uuid IN (SELECT workflow_node_job_uuid FROM execution_lock_lease WHERE state!='released' AND deleted_at IS NULL) OR "
        "j.uuid IN (SELECT acquired_by_job_uuid FROM task_device_tenancy WHERE state='active') OR "
        "j.uuid IN (SELECT workflow_node_job_uuid FROM workflow_task_material_claim WHERE status='active' AND deleted_at IS NULL))"
    ):
        if row["uuid"] not in referenced and row["status"] not in {"dispatched", "running", "cancel_requested"}:
            continue
        plan = json.loads(row["execution_plan"] or "{}")
        node = next((node for node in plan.get("nodes", []) if node.get("uuid") == row["workflow_node_uuid"]), {})
        snapshot = json.loads(row["workflow_snapshot"] or "{}")
        control = json.loads(row["control_data"] or "{}")
        payload = control.get("dispatch_payload", {})
        jobs.append({
            "uuid": row["uuid"], "task_uuid": row["workflow_task_uuid"],
            "task_status": row["task_status"],
            "task_name": snapshot.get("workflow", {}).get("name", ""),
            "node_uuid": row["workflow_node_uuid"],
            "node_name": node.get("name") or node.get("action_name") or payload.get("action") or row["workflow_node_uuid"],
            "status": row["status"], "device_id": payload.get("device_id") or node.get("device_id"),
            "material_uuid": row["material_uuid"],
            "expected_change_set": json.loads(row["expected_change_set"] or "{}"),
        })
    return {"captured_at": utc_now(), "occupancies": occupancies, "material_bindings": bindings, "jobs": jobs}
