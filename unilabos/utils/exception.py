
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class DeviceClassInvalid(Exception):
    pass


class DeviceActionError(RuntimeError):
    """跨设备调用动作失败时抛出。

    把远端设备执行动作时产生的错误（被拒绝 / 执行失败 / 超时 / 结果无法解析）
    转换成本地异常，在调用方的执行流程中 raise 出来。

    Attributes:
        device_id: 远端设备 ID。
        action_name: 远端动作 / 函数名。
        remote_error: 远端返回的原始错误信息（通常是远端 traceback 字符串）。
        rejected: 目标是否被远端拒绝。
        return_value: 失败时远端附带的返回值（如有）。
    """

    def __init__(
        self,
        device_id: str,
        action_name: str,
        remote_error: str = "",
        *,
        rejected: bool = False,
        return_value=None,
    ):
        self.device_id = device_id
        self.action_name = action_name
        self.remote_error = remote_error or ""
        self.rejected = rejected
        self.return_value = return_value
        detail = " (目标拒绝了请求)" if rejected else ""
        suffix = f": {self.remote_error}" if self.remote_error else ""
        super().__init__(f"调用设备动作 [{device_id}.{action_name}] 失败{detail}{suffix}")


BUILT_IN_DECISIONS = frozenset({"retry", "skip", "abort"})


class DeviceExceptionCategory(str, Enum):
    """设备异常分类。"""

    NETWORK = "network"
    HARDWARE = "hardware"
    TIMEOUT = "timeout"
    PARAMETER = "parameter"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


class DeviceExceptionSeverity(str, Enum):
    """设备异常严重程度。"""

    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class UserAction:
    """展示给用户的框架内置异常处理操作。"""

    action: str
    label: str
    description: str = ""

    def __post_init__(self):
        if self.action not in BUILT_IN_DECISIONS:
            raise ValueError(f"不支持的异常处理操作: {self.action}")


class DeviceException(Exception):
    """设备驱动主动抛出的结构化异常基类。"""

    category = DeviceExceptionCategory.UNKNOWN
    severity = DeviceExceptionSeverity.ERROR

    def __init__(
        self,
        message: str,
        suggested_actions: Optional[List[UserAction]] = None,
        device_snapshot: Optional[dict] = None,
        cause: Optional[BaseException] = None,
    ):
        super().__init__(message)
        self.message = message
        self.suggested_actions = suggested_actions if suggested_actions is not None else self._default_actions()
        self.device_snapshot = device_snapshot or {}
        self.cause = cause

    def _default_actions(self) -> List[UserAction]:
        return [
            UserAction("retry", "重试", "重新执行当前操作"),
            UserAction("skip", "跳过", "跳过当前操作继续执行"),
            UserAction("abort", "终止任务", "停止当前任务"),
        ]

    def to_alarm_dict(
        self,
        *,
        device_id: str,
        device_uuid: str,
        action_name: str,
        task_id: str,
        job_id: str,
        traceback_text: str,
    ) -> dict:
        return {
            "device_id": device_id,
            "device_uuid": device_uuid,
            "action_name": action_name,
            "task_id": task_id,
            "job_id": job_id,
            "exception_type": type(self).__name__,
            "category": self.category.value,
            "severity": self.severity.value,
            "error_message": self.message,
            "suggested_actions": [
                {
                    "action": action.action,
                    "label": action.label,
                    "description": action.description,
                }
                for action in self.suggested_actions
            ],
            "device_snapshot": self.device_snapshot,
            "traceback": traceback_text,
            "require_confirmation": True,
        }


class TimeoutException(DeviceException):
    category = DeviceExceptionCategory.TIMEOUT


class ParameterError(DeviceException):
    category = DeviceExceptionCategory.PARAMETER
    severity = DeviceExceptionSeverity.WARNING

    def _default_actions(self) -> List[UserAction]:
        return [UserAction("abort", "终止任务", "参数错误需修改后重新提交")]


class ModbusConnectionError(DeviceException):
    category = DeviceExceptionCategory.NETWORK


class OPCUAConnectionError(DeviceException):
    category = DeviceExceptionCategory.NETWORK


class EmergencyStopError(DeviceException):
    category = DeviceExceptionCategory.HARDWARE
    severity = DeviceExceptionSeverity.CRITICAL

    def _default_actions(self) -> List[UserAction]:
        return [
            UserAction("retry", "重试", "确认解除急停后重新执行当前操作"),
            UserAction("abort", "终止任务", "停止当前任务"),
        ]


class PLCStepTimeout(DeviceException):
    category = DeviceExceptionCategory.TIMEOUT

    def __init__(
        self,
        message: str,
        current_step: int = -1,
        expected_step: int = -1,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.current_step = current_step
        self.expected_step = expected_step

    def to_alarm_dict(self, **kwargs) -> dict:
        data = super().to_alarm_dict(**kwargs)
        data["current_step"] = self.current_step
        data["expected_step"] = self.expected_step
        return data


class SensorError(DeviceException):
    category = DeviceExceptionCategory.HARDWARE


class ResourceConflictError(DeviceException):
    category = DeviceExceptionCategory.RESOURCE
    severity = DeviceExceptionSeverity.WARNING


class TipPickupError(DeviceException):
    category = DeviceExceptionCategory.HARDWARE

    def __init__(
        self,
        message: str,
        tip_position: str = "",
        remaining_tips: int = 0,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.tip_position = tip_position
        self.remaining_tips = remaining_tips

    def to_alarm_dict(self, **kwargs) -> dict:
        data = super().to_alarm_dict(**kwargs)
        data["tip_position"] = self.tip_position
        data["remaining_tips"] = self.remaining_tips
        return data
