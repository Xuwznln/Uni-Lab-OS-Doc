"""Backend Scheduler 到执行适配层的 Job 载荷。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class DispatchPayload(Dict[str, Any]):
    """Backend ``execute_job`` 的执行器下发载荷。"""


def build_job_start_payload(
    job_id: str,
    task_id: str,
    workflow_id: str,
    node_id: str,
    device_id: str,
    action_name: str,
    action_type: str,
    action_args: Any,
    materials_need_lock: Optional[List[str]] = None,
    inventory_requirements: Optional[List[Dict[str, Any]]] = None,
    inventory_reservation_uuid: Optional[str] = None,
    scheduler_revision: int = 0,
    node_run_uuid: str = "",
    attempt_no: int = 1,
) -> DispatchPayload:
    """构造执行器消费的 Backend ``execute_job`` 载荷。

    ``job_id`` 是本次 attempt 的 uuid；``node_run_uuid`` 是所属节点运行
    （≡ runtime.v1 ``attempt_group_uuid``），``attempt_no`` 从 1 计，执行器据此
    在错误决策报告里给出 ``retry_count``。
    """
    return DispatchPayload(
        job_id=job_id,
        task_id=task_id,
        node_id=node_id,
        workflow_id=workflow_id,
        device_id=device_id,
        action=action_name,
        action_type=action_type,
        action_args=action_args,
        materials_need_lock=list(materials_need_lock or []),
        inventory_requirements=list(inventory_requirements or []),
        inventory_reservation_uuid=inventory_reservation_uuid,
        scheduler_revision=scheduler_revision,
        node_run_uuid=node_run_uuid,
        attempt_no=int(attempt_no),
        retry_count=max(int(attempt_no) - 1, 0),
        sample_material={},
    )


__all__ = [
    "DispatchPayload",
    "build_job_start_payload",
]
