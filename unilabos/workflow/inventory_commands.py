"""工作流库存命令的证据校验；所有数量写入仍由库存命令事务完成。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from unilabos.app.scheduler.inventory.commands import execute_command
from unilabos.app.scheduler.inventory.domain import CommandRejected, InventoryError
from unilabos.workflow.local_effects import inventory_effect_transaction
from unilabos.workflow.scheduling_local_authority import LocalControlAuthority

_PREFIX = "unilabos.workflow.native_commands:InventoryCommands."
_RESULT_FIELDS = ("previous_count", "measured_count", "changed", "version")
_OUTCOME_TABLE = "inventory_workflow_command_outcome"


class InventoryWorkflowCommandError(InventoryError):
    """保留库存事务拒绝的具体原因，供前端和复核使用。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _input_job(store, event: Mapping[str, Any], key: str, task_uuid: str):
    """参数必须由同任务成功作业提供，不能用字面量冒充实测证据。"""
    inputs = dict(event.get("input_jobs") or {})
    identity = inputs.get(f"goal.{key}") or inputs.get(key)
    if not identity:
        raise CommandRejected(f"{key} 必须引用成功的上游作业结果")
    job = store.get_job(str(identity))
    if job["workflow_task_uuid"] != task_uuid or job["status"] != "succeeded":
        raise CommandRejected("库存命令的上游证据不是本任务已成功作业")
    return job


def execute_inventory_command(
    store, inventory, event: Mapping[str, Any], *, task_uuid: str,
    scheduling_authority: LocalControlAuthority | None = None,
) -> dict[str, Any]:
    """库存提交前复验本轮授权；已提交结果沿用原 Job 身份幂等回放。"""
    if scheduling_authority is None or inventory is None:
        return _execute_inventory_command(store, inventory, event, task_uuid=task_uuid)
    with inventory_effect_transaction(store, inventory, scheduling_authority):
        with inventory.command_transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS inventory_workflow_command_outcome ("
                "job_uuid TEXT PRIMARY KEY, task_uuid TEXT NOT NULL, command_name TEXT NOT NULL, "
                "request_digest TEXT NOT NULL, result_json TEXT NOT NULL CHECK(json_valid(result_json)))"
            )
            job_id = str(event["job_id"])
            command_name = _workflow_command_name(store, job_id, task_uuid)
            if command_name != event.get("command_name"):
                raise CommandRejected("本地库存命令与冻结作业定义不一致")
            request = {key: event.get(key) for key in ("command_name", "arguments", "input_jobs")}
            digest = hashlib.sha256(_json(request).encode()).hexdigest()
            previous = connection.execute(
                "SELECT * FROM inventory_workflow_command_outcome WHERE job_uuid=?", (job_id,),
            ).fetchone()
            if previous is not None:
                if previous["request_digest"] != digest:
                    raise CommandRejected("本地库存命令重放参数或证据身份变化")
                return _read_outcome(previous, task_uuid=task_uuid, command_name=command_name)
            result = _execute_inventory_command(store, inventory, event, task_uuid=task_uuid)
            # 与库存业务及 processed_command 同事务；不以重做动作修补另一库未提交的结果。
            connection.execute(
                "INSERT INTO inventory_workflow_command_outcome VALUES (?,?,?,?,?)",
                (job_id, task_uuid, command_name, digest, _json(result)),
            )
            return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _workflow_command_name(store, job_uuid: str, task_uuid: str) -> str:
    """用冻结计划证明结果的任务、作业和命令归属，不信任待回放结果中的名字。"""
    job = store.get_job(job_uuid)
    if job["workflow_task_uuid"] != task_uuid or job["executor_kind"] != "workflow_command":
        raise CommandRejected("本地库存命令结果不属于该任务作业")
    nodes = store.get_task(task_uuid)["execution_plan"]["nodes"]
    node = next((item for item in nodes if item["uuid"] == job["workflow_node_uuid"]), None)
    if node is None or node.get("kind") != "workflow_command" or not node.get("action_name"):
        raise CommandRejected("本地库存命令缺少冻结定义")
    return str(node["action_name"])


def _read_outcome(row, *, task_uuid: str, command_name: str) -> dict[str, Any]:
    if row["task_uuid"] != task_uuid or row["command_name"] != command_name:
        raise CommandRejected("本地库存命令持久结果归属冲突")
    result = json.loads(row["result_json"])
    if not isinstance(result, dict):
        raise CommandRejected("本地库存命令持久结果不是对象")
    _json(result)
    return result


def _execute_inventory_command(
    store, inventory, event: Mapping[str, Any], *, task_uuid: str,
) -> dict[str, Any]:
    """冻结测量前版本并检查成功证据；重复提交由库存 command_id 幂等处理。"""
    name = event.get("command_name")
    if name not in {
        _PREFIX + "read_count",
        _PREFIX + "reconcile_count",
        _PREFIX + "inspect_carrier_transfer",
        _PREFIX + "locate",
        _PREFIX + "mark_used",
        _PREFIX + "site_occupant",
    }:
        raise CommandRejected(f"OS 本地命令未注册：{name}")
    if inventory is None:
        raise CommandRejected("OS 库存权威未装配")
    if name == _PREFIX + "inspect_carrier_transfer":
        from unilabos.app.scheduler.inventory.carrier_snapshot import (
            inspect_carrier_transfer,
        )

        return inspect_carrier_transfer(
            inventory, task_uuid=task_uuid, **dict(event.get("arguments") or {})
        )
    if name == _PREFIX + "locate":
        from unilabos.app.scheduler.inventory.material_usage import locate_material

        return locate_material(inventory, task_uuid=task_uuid, **dict(event.get("arguments") or {}))
    if name == _PREFIX + "site_occupant":
        from unilabos.app.scheduler.inventory.material_usage import site_occupant

        return site_occupant(inventory, task_uuid=task_uuid, **dict(event.get("arguments") or {}))
    if name == _PREFIX + "mark_used":
        from unilabos.app.scheduler.inventory.material_usage import mark_material_used

        return mark_material_used(
            inventory, task_uuid=task_uuid, job_uuid=str(event["job_id"]),
            **dict(event.get("arguments") or {}),
        )
    replayed = committed_inventory_command_result(
        store, inventory, str(event["job_id"]), task_uuid=task_uuid
    )
    if replayed is not None:
        return replayed
    args = dict(event.get("arguments") or {})
    lot = inventory.store.get_lot(str(args["lot_id"]))
    if lot is None or lot["warehouse_zone_id"] != args["mount_uuid"]:
        raise CommandRejected("计数库存不存在或不属于测量容器")
    if name == _PREFIX + "read_count":
        count = lot["quantity_total"]
        if int(count) != count:
            raise CommandRejected("计数命令不能用于非整数库存")
        return {
            "lot_id": args["lot_id"],
            "mount_uuid": args["mount_uuid"],
            "count": int(count),
            "version": lot["version"],
        }

    evidence = _input_job(store, event, "measured_count", task_uuid)
    snapshot = _input_job(store, event, "expected_version", task_uuid)
    if (
        evidence["executor_kind"] != "device_action"
        or snapshot["executor_kind"] != "workflow_command"
    ):
        raise CommandRejected("盘点必须引用设备测量和测量前库存快照")
    snapshot_value = snapshot.get("return_info") or {}
    snapshot_value = snapshot_value.get("return_value", snapshot_value)
    if (
        any(snapshot_value.get(key) != args[key] for key in ("lot_id", "mount_uuid"))
        or snapshot_value.get("version") != args["expected_version"]
    ):
        raise CommandRejected("盘点版本不属于本次测量容器的库存快照")
    observed_at = datetime.fromisoformat(
        str(evidence["finished_at"]).replace("Z", "+00:00")
    )
    snapshot_at = datetime.fromisoformat(
        str(snapshot["finished_at"]).replace("Z", "+00:00")
    )
    started_at = datetime.fromisoformat(
        str(evidence["started_at"]).replace("Z", "+00:00")
    )
    if (
        started_at < snapshot_at
        or not 0 <= (datetime.now(timezone.utc) - observed_at).total_seconds() <= 60
    ):
        raise CommandRejected("测量证据已过期或早于库存快照，须重新测量")
    result = execute_command(
        inventory,
        {
            "command_id": str(event["job_id"]),
            "type": "inventory.reconcile_count",
            "expected_version": args.pop("expected_version"),
            "actor": "workflow",
            "payload": {**args, "evidence_job_uuid": evidence["uuid"]},
        },
    )
    if result["status"] != "completed":
        raise InventoryWorkflowCommandError(
            str(result.get("error_code") or "command_rejected"),
            str(result.get("error") or "库存盘点失败"),
        )
    return {key: result["result"][key] for key in _RESULT_FIELDS}


def committed_inventory_command_result(
    store, inventory, job_uuid: str, *, task_uuid: str
) -> dict[str, Any] | None:
    """跨库崩溃后只重投已提交的数量事实，不重做测量或库存写入。"""
    if inventory is None:
        return None
    present = inventory.store.query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (_OUTCOME_TABLE,),
    )
    if present:
        outcome = inventory.store.query_one(
            "SELECT * FROM inventory_workflow_command_outcome WHERE job_uuid=?", (job_uuid,),
        )
        if outcome is not None:
            return _read_outcome(
                outcome, task_uuid=task_uuid, command_name=_workflow_command_name(store, job_uuid, task_uuid),
            )
    saved = inventory.store.get_processed_command(job_uuid)
    if saved is None or saved["status"] != "completed":
        return None
    result = json.loads(saved["result_json"])
    command_name = _workflow_command_name(store, job_uuid, task_uuid)
    if command_name == _PREFIX + "mark_used":
        # 旧版本没有完整结果账时，仅能恢复冻结参数已明确给出同一物料的记录。
        job = store.get_job(job_uuid)
        material = (job.get("param") or {}).get("material")
        if (
            not isinstance(material, dict) or material.get("uuid") != result.get("edge_uuid")
            or not isinstance(result.get("previous_status"), str) or result.get("status") != "used"
        ):
            raise CommandRejected("旧使用标记结果缺少可验证的原物料参数")
        return {"material": material, "previous_status": result["previous_status"], "status": result["status"]}
    if command_name != _PREFIX + "reconcile_count":
        raise CommandRejected("本地库存命令持久结果类型不匹配")
    if not all(key in result for key in (*_RESULT_FIELDS, "evidence_job_uuid")):
        raise CommandRejected("本地盘点命令的持久结果合同不一致")
    evidence = store.get_job(result["evidence_job_uuid"])
    if evidence["workflow_task_uuid"] != task_uuid or evidence["status"] != "succeeded":
        raise CommandRejected("持久盘点结果的测量证据归属错误")
    return {key: result[key] for key in _RESULT_FIELDS}
