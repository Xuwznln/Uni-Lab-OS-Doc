"""合并平台资源权威的事实，不在此选择候选或安排等待者优先级。"""

from __future__ import annotations

import hashlib
from typing import Any

from unilabos.scheduling.protocol import EngineError, encode_message, require
from unilabos.workflow.device_tenancy import active_task_device_tenancies
from unilabos.workflow.resource_lock_key import (
    device_lock_key,
    material_lock_key,
    parse_canonical_resource_lock_key,
)
from unilabos.workflow.store import WorkflowStore


def platform_resources(
    store: WorkflowStore, inventory: Any, mirrors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """一次读取库存权威；分开投影执行占用与允许同 Task 续行的所有权。"""
    held = []
    if inventory is not None:
        reader = getattr(inventory, "execution_occupancies", None)
        if not callable(reader):
            raise EngineError("SNAPSHOT_INVALID", "库存权威缺少完整执行占用读取接口")
        held = list(reader())
        require(
            all(row["source"] in {"dispatch", "ingress", "task_material"} for row in held),
            "SNAPSHOT_INVALID", "未知库存占用来源",
        )
    return (
        execution_leases([row for row in held if row["source"] != "task_material"], mirrors),
        task_ownerships(store, [row for row in held if row["source"] == "task_material"]),
    )


def execution_leases(held_rows: list[dict[str, Any]], mirrors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """工作流镜像与库存同一 Claim/Fence 合并；其他真实持有继续保留。"""
    result = [dict(row) for row in mirrors]
    by_identity = {(row["claim_uuid"], row["lock_key"]): row for row in result}
    for held in held_rows:
        key = held["lock_key"]
        require(parse_canonical_resource_lock_key(key) is not None, "SNAPSHOT_INVALID", "库存占用资源键非法")
        source = held["source"]
        require(source in {"dispatch", "ingress"}, "SNAPSHOT_INVALID", "未知库存占用来源")
        claim = held.get("claim_uuid")
        if source == "dispatch":
            require(
                held["state"] in {"prepared", "reserved", "running", "uncertain"}
                and type(held["fencing_token"]) is int and held["fencing_token"] > 0,
                "SNAPSHOT_INVALID", "库存执行占用状态或栅栏非法",
            )
            mirror = by_identity.get((claim, key))
            if mirror is not None:
                require(
                    mirror["workflow_task_uuid"] == held["task_uuid"]
                    and mirror["workflow_node_job_uuid"] == held["job_uuid"]
                    and mirror["fencing_token"] == held["fencing_token"],
                    "SNAPSHOT_INVALID", "库存 Claim 与工作流占用镜像身份不一致",
                )
                if held["state"] == "uncertain":
                    mirror["state"] = "uncertain"
                continue
        identity = [source, claim or held["reservation_uuid"], key, held.get("fencing_token")]
        result.append({
            "uuid": "inventory:" + hashlib.sha256(encode_message(identity)).hexdigest(),
            "workflow_task_uuid": held.get("task_uuid"),
            "workflow_node_job_uuid": held.get("job_uuid"),
            "claim_uuid": claim, "fencing_token": held.get("fencing_token"),
            "lock_key": key, "state": held.get("state", "reserved"), "acquired_at": held["acquired_at"],
        })
    return result


def task_ownerships(store: WorkflowStore, inventory_claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并 Task 物料独占镜像，并保留每个尚未真实释放的设备托管。"""
    with store.read() as connection:
        claims = [dict(row) for row in connection.execute(
            "SELECT claim.workflow_task_uuid AS task_uuid, claim.workflow_node_uuid AS node_id, "
            "claim.material_uuid, job.attempt "
            "FROM workflow_task_material_claim AS claim "
            "JOIN workflow_node_job AS job ON job.uuid=claim.workflow_node_job_uuid "
            "WHERE claim.deleted_at IS NULL AND claim.status='active' "
            "ORDER BY claim.workflow_task_uuid, claim.workflow_node_uuid"
        ).fetchall()]
        tenancies = active_task_device_tenancies(connection)
    by_binding: dict[tuple[str, str, int], str] = {}
    result: dict[str, dict[str, Any]] = {}
    for claim in [*claims, *inventory_claims]:
        task, material = claim["task_uuid"], claim["material_uuid"]
        binding = (task, claim["node_id"], claim["attempt"])
        require(
            binding not in by_binding or by_binding[binding] == material,
            "SNAPSHOT_INVALID", "库存与工作流的 Task 物料绑定不一致",
        )
        by_binding[binding] = material
        identity = "task-material:" + hashlib.sha256(encode_message([task, material])).hexdigest()
        # 同一 Task/物料只有一个所有权，即使两库镜像尚未同时释放也继续保留。
        result[identity] = {
            "allocation_id": identity, "task_id": task,
            "resource_ids": [device_lock_key(material), material_lock_key(material)],
        }
    for tenancy in tenancies:
        key = tenancy["device_lock_key"]
        parsed = parse_canonical_resource_lock_key(key)
        require(parsed is not None and parsed.scope == "device", "SNAPSHOT_INVALID", "设备托管资源键非法")
        identity = "device-tenancy:" + tenancy["uuid"]
        result[identity] = {
            "allocation_id": identity, "task_id": tenancy["workflow_task_uuid"], "resource_ids": [key],
        }
    return list(result.values())


def execution_waiters(store: WorkflowStore) -> list[dict[str, Any]]:
    """读取仍属当前待执行尝试的持久等待记录，保留首次排队时间。"""
    with store.read() as connection:
        rows = connection.execute(
            "SELECT waiter.workflow_task_uuid, waiter.workflow_node_job_uuid, "
            "waiter.lock_key, waiter.enqueued_at, job.attempt "
            "FROM execution_lock_waiter AS waiter "
            "JOIN workflow_node_job AS job ON job.uuid=waiter.workflow_node_job_uuid "
            "JOIN workflow_task AS task ON task.uuid=waiter.workflow_task_uuid "
            "WHERE waiter.deleted_at IS NULL AND waiter.state='waiting' "
            "AND job.deleted_at IS NULL AND job.status='pending' AND job.current_attempt=1 "
            "AND task.deleted_at IS NULL AND task.status IN ('pending','running') "
            "ORDER BY waiter.workflow_task_uuid, waiter.workflow_node_job_uuid, waiter.lock_key"
        ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = row["workflow_node_job_uuid"]
        item = grouped.setdefault(identity, {
            "job_id": identity, "task_id": row["workflow_task_uuid"], "attempt_no": row["attempt"],
            "resource_ids": [], "enqueued_at": row["enqueued_at"],
        })
        item["resource_ids"].append(row["lock_key"])
        item["enqueued_at"] = min(item["enqueued_at"], row["enqueued_at"])
    return list(grouped.values())
