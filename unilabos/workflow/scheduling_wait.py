"""把完整平台切面中的资源等待落账；不取得资源、不激活 Task、不决定优先级。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any
from uuid import uuid4

from unilabos.scheduling.engine import PREPARATION_ERROR_PREFIX
from unilabos.scheduling.protocol import encode_message, require
from unilabos.workflow.event_writer import append_frontend_event
from unilabos.workflow.execution_lock_lease import wait_resource_from_execution_lock
from unilabos.workflow.resource_lock_key import parse_canonical_resource_lock_key
from unilabos.workflow.scheduling_journal import SchedulingJournal
from unilabos.workflow.store import utc_now

_SOURCE = "scheduler_resource_observation"


def _wait_reason(node: dict[str, Any], options: list[dict[str, Any]]) -> dict[str, Any] | None:
    if (
        node["status"] != "pending" or not node["ready"] or not node["parameters_final"]
        or node["manual_mode"] == "intervention"
        or any(option["eligible_now"] for option in options)
    ):
        return None
    blockers = [blocker for option in options for blocker in option["blockers"]] or node["blockers"]
    # 准备错误保留在节点诊断中，不能建立资源 waiter 或参与等待老化。
    blockers = [blocker for blocker in blockers if not blocker["code"].startswith(PREPARATION_ERROR_PREFIX)]
    if not blockers:
        return None
    # 阻塞事实是集合；候选枚举顺序和重复诊断不能重置等待或制造新的反馈。
    normalized = {}
    for blocker in blockers:
        value = {**blocker, "resource_ids": sorted(set(blocker["resource_ids"]))}
        normalized[encode_message(value)] = value
    blockers = [normalized[key] for key in sorted(normalized)]
    keys = sorted({key for blocker in blockers for key in blocker["resource_ids"]})
    return {
        "source": _SOURCE, "code": "resource_unavailable", "message": "执行资源条件尚未满足",
        "resource_ids": keys, "blockers": blockers,
    }


def reconcile_resource_waits(journal: SchedulingJournal, domain_id: str, snapshot: dict[str, Any]) -> bool:
    """同步首次等待、候选变化及解除；事实、日志和界面失效通知同事务提交。"""
    options: dict[str, list[dict[str, Any]]] = defaultdict(list)
    nodes = {node["job_id"]: node for node in snapshot["task_view"]["nodes"]}
    jobs_by_ref = {
        (node["ref"]["task_id"], node["ref"]["node_id"]): job for job, node in nodes.items()
    }
    for option in snapshot["resource_view"]["options"]:
        options[jobs_by_ref[(option["node"]["task_id"], option["node"]["node_id"])]].append(option)
    desired = {job: _wait_reason(node, options[job]) for job, node in nodes.items()}
    changed_tasks: set[str] = set()
    now = utc_now()
    with journal.store.transaction() as connection:
        managed = {
            row["workflow_node_job_uuid"] for row in connection.execute(
                "SELECT workflow_node_job_uuid FROM execution_lock_waiter "
                "WHERE state='waiting' AND deleted_at IS NULL AND json_extract(meta_data,'$.source')=? "
                "AND json_extract(meta_data,'$.domain_id')=?", (_SOURCE, domain_id),
            ).fetchall()
        }
        for job_id in sorted(nodes.keys() | managed):
            row = connection.execute(
                "SELECT job.*, task.status AS task_status, task.deleted_at AS task_deleted_at "
                "FROM workflow_node_job AS job JOIN workflow_task AS task ON task.uuid=job.workflow_task_uuid "
                "WHERE job.uuid=?", (job_id,),
            ).fetchone()
            if row is None:
                continue
            reason = desired.get(job_id)
            if (
                row["deleted_at"] or row["task_deleted_at"] or row["status"] != "pending"
                or not row["current_attempt"] or row["task_status"] not in {"pending", "running"}
                or (job_id in nodes and row["attempt"] != nodes[job_id]["attempt_no"])
            ):
                reason = None
            changed = _sync_job(connection, row, reason, now, domain_id)
            if changed:
                task_id = row["workflow_task_uuid"]
                changed_tasks.add(task_id)
        for task_id in sorted(changed_tasks):
            _sync_task_reason(connection, task_id, now)
            journal.append_feedback(
                connection, domain_id, event_id=str(uuid4()), occurred_at_ms=snapshot["captured_at_ms"],
                payload={
                    "kind": "resources_changed",
                    "resource_ids": sorted({
                        key for job, node in nodes.items() if node["ref"]["task_id"] == task_id
                        for option in options[job] for key in option["resource_ids"]
                    }),
                    "reason": {
                        "code": "RESOURCE_WAIT_CHANGED", "message": "平台资源等待事实已提交",
                        "task_id": task_id, "node": None, "resource_ids": [],
                    },
                },
            )
            append_frontend_event(
                connection, event="workflow.runtime.changed", data={"workflow_task_uuid": task_id}, now=now,
            )
    return bool(changed_tasks)


def _sync_job(connection: Any, job: Any, reason: dict[str, Any] | None, now: str, domain_id: str) -> bool:
    previous = json.loads(job["wait_reason"])
    rows = connection.execute(
        "SELECT * FROM execution_lock_waiter WHERE workflow_node_job_uuid=? "
        "AND state='waiting' AND deleted_at IS NULL", (job["uuid"],),
    ).fetchall()
    owned = {
        row["lock_key"]: row for row in rows
        if json.loads(row["meta_data"]).get("source") == _SOURCE
        and json.loads(row["meta_data"]).get("domain_id") == domain_id
    }
    wanted = set(reason["resource_ids"]) if reason is not None else set()
    changed = False
    if reason is not None:
        starts = [row["enqueued_at"] for row in rows]
        if previous.get("waiting_since"):
            starts.append(previous["waiting_since"])
        reason = {**reason, "waiting_since": min(starts) if starts else now, "resources": []}
        existing = {row["lock_key"]: row for row in rows}
        for key in sorted(wanted):
            parsed = parse_canonical_resource_lock_key(key)
            require(parsed is not None, "SNAPSHOT_INVALID", "等待资源缺少规范身份")
            lock = {"lock_key": key, "scope": parsed.scope, "material_uuid": parsed.material_uuid, "site_uuid": parsed.site_uuid}
            reason["resources"].append(wait_resource_from_execution_lock(lock))
            metadata = encode_message({"semantic_scope": parsed.scope, "source": _SOURCE, "domain_id": domain_id}).decode()
            if key not in existing:
                connection.execute(
                    "INSERT INTO execution_lock_waiter(uuid,create_time,update_time,meta_data,"
                    "workflow_task_uuid,workflow_node_job_uuid,lock_key,scope,material_uuid,site_uuid,state,enqueued_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,'waiting',?)",
                    (str(uuid4()), now, now, metadata, job["workflow_task_uuid"], job["uuid"],
                     key, parsed.scope, parsed.material_uuid, parsed.site_uuid, reason["waiting_since"]),
                )
                changed = True
            elif key not in owned:
                require(
                    json.loads(existing[key]["meta_data"]).get("source") != _SOURCE,
                    "SNAPSHOT_INVALID", "不能接管其他调度域的等待事实",
                )
                connection.execute("UPDATE execution_lock_waiter SET meta_data=?,update_time=? WHERE uuid=?",
                                   (metadata, now, existing[key]["uuid"]))
                changed = True
    for key in owned.keys() - wanted:
        connection.execute(
            "UPDATE execution_lock_waiter SET state='released',released_at=?,update_time=? WHERE uuid=?",
            (now, now, owned[key]["uuid"]),
        )
        changed = True
    # 独立的人工、容量和控制等待归原权威管理，资源观察只更新自己的展示原因。
    if not previous or previous.get("source") == _SOURCE or (owned and previous.get("code") == "operation_lease"):
        updated = reason or {}
        if previous != updated:
            connection.execute("UPDATE workflow_node_job SET wait_reason=?,update_time=? WHERE uuid=?",
                               (encode_message(updated).decode(), now, job["uuid"]))
            changed = True
    return changed


def _sync_task_reason(connection: Any, task_id: str, now: str) -> None:
    previous = json.loads(connection.execute(
        "SELECT wait_reason FROM workflow_task WHERE uuid=?", (task_id,),
    ).fetchone()["wait_reason"])
    if previous and previous.get("source") != _SOURCE:
        return
    pending = [
        json.loads(row["wait_reason"]) for row in connection.execute(
            "SELECT wait_reason FROM workflow_node_job WHERE workflow_task_uuid=? AND status='pending' "
            "AND current_attempt=1 AND deleted_at IS NULL AND wait_reason<>'{}'", (task_id,),
        ).fetchall()
    ]
    updated = min(pending, key=lambda item: item.get("waiting_since", now)) if pending else {}
    if previous != updated:
        connection.execute("UPDATE workflow_task SET wait_reason=?,update_time=? WHERE uuid=?",
                           (encode_message(updated).decode(), now, task_id))
