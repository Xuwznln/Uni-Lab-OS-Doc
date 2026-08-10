"""调度权威运行模式（SchedulerAuthorityProfile）及启动选择规则。"""

from __future__ import annotations

from enum import Enum


class SchedulerAuthorityConflict(RuntimeError):
    """启动配置会形成双调度权威（Scheduler Authority）。"""


class SchedulerAuthorityProfile(str, Enum):
    """OS 进程对工作流任务（WorkflowTask）权威的显式运行选择。"""

    LOCAL_SCHEDULER = "local_scheduler"
    BACKEND_CONTROLLED = "backend_controlled"
    OFFLINE_RECOVERY = "offline_recovery"

    @classmethod
    def parse(
        cls,
        value: str | SchedulerAuthorityProfile,
    ) -> SchedulerAuthorityProfile:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip())
        except ValueError as error:
            allowed = ", ".join(item.value for item in cls)
            raise ValueError(
                f"invalid SchedulerAuthorityProfile {value!r}; expected one of: {allowed}"
            ) from error

    @property
    def can_create_local_workflow_task(self) -> bool:
        return self is self.LOCAL_SCHEDULER

    @property
    def can_recover_local_workflow_task(self) -> bool:
        return self in {self.LOCAL_SCHEDULER, self.OFFLINE_RECOVERY}

    @property
    def can_execute_backend_command(self) -> bool:
        return self is self.BACKEND_CONTROLLED

    @property
    def opens_local_inventory_authority(self) -> bool:
        return self in {self.LOCAL_SCHEDULER, self.OFFLINE_RECOVERY}


def select_scheduler_authority_profile(
    value: str | SchedulerAuthorityProfile | None,
    *,
    edge_control_enabled: bool,
) -> SchedulerAuthorityProfile:
    """从启动参数确定唯一档位，并拒绝 Edge 控制与本地调度双权威。"""

    if value is None or not str(value).strip():
        return (
            SchedulerAuthorityProfile.BACKEND_CONTROLLED
            if edge_control_enabled
            else SchedulerAuthorityProfile.LOCAL_SCHEDULER
        )
    profile = SchedulerAuthorityProfile.parse(value)
    if (
        edge_control_enabled
        and profile is not SchedulerAuthorityProfile.BACKEND_CONTROLLED
    ):
        raise SchedulerAuthorityConflict(
            "edge_control 只能与 backend_controlled 调度权威运行模式一起启用"
        )
    return profile


__all__ = [
    "SchedulerAuthorityConflict",
    "SchedulerAuthorityProfile",
    "select_scheduler_authority_profile",
]
