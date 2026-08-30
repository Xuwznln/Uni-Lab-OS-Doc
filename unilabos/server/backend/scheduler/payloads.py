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
) -> DispatchPayload:
    """构造执行器消费的 Backend ``execute_job`` 载荷。"""
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
        sample_material={},
    )


__all__ = [
    "DispatchPayload",
    "build_job_start_payload",
]
