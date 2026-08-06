"""用于设备异常处理链路联调的虚拟设备。"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from typing_extensions import TypedDict

from unilabos.registry.decorators import action, device
from unilabos.utils.exception import (
    EmergencyStopError,
    ModbusConnectionError,
    PLCStepTimeout,
    SensorError,
    TipPickupError,
)


class SimpleResult(TypedDict):
    success: bool
    message: str


@device(
    id="fault_injection_device",
    display_name="故障注入设备",
    category=["virtual_device"],
    description="触发设备异常，用于 retry / skip / abort 端到端测试",
)
class FaultInjectionDevice:
    """提供可重复、无外部硬件依赖的故障场景。"""

    def __init__(
        self,
        device_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        self.device_id = device_id or kwargs.pop("id", "fault_injection_device")
        self.config = config or kwargs.pop("config", {})
        self.logger = logging.getLogger(f"FaultInjectionDevice.{self.device_id}")
        self._async_attempts = 0
        self._sync_attempts = 0

    @action(description="正常调用，立即返回成功")
    async def run_ok(self) -> SimpleResult:
        await asyncio.sleep(0.05)
        return {"success": True, "message": "ok"}

    @action(description="首次失败，用户重试后成功")
    async def fail_once_then_success(self) -> SimpleResult:
        self._async_attempts += 1
        if self._async_attempts == 1:
            raise SensorError(
                "首次温度读取失败（模拟）",
                device_snapshot={"sensor": "temp_1", "attempt": self._async_attempts},
            )
        return {"success": True, "message": f"succeeded on attempt {self._async_attempts}"}

    @action(description="模拟长耗时操作，触发框架 timeout", timeout=2.0)
    async def run_long(self, duration: float = 5.0) -> SimpleResult:
        await asyncio.sleep(duration)
        return {"success": True, "message": f"slept {duration}s"}

    @action(description="持续抛出 Modbus 连接异常，可测试 skip 或 abort")
    async def raise_modbus_error(self) -> SimpleResult:
        raise ModbusConnectionError(
            "Modbus 端口连接失败（模拟）",
            device_snapshot={"port": "/dev/ttyUSB_FAKE", "baudrate": 9600},
        )

    @action(description="抛出 critical 急停异常")
    async def raise_emergency_stop(self) -> SimpleResult:
        raise EmergencyStopError("急停按钮已触发（模拟）")

    @action(description="抛出 PLC 步序超时")
    async def raise_plc_step_timeout(self) -> SimpleResult:
        raise PLCStepTimeout(
            "PLC 步序长时间未变化（模拟）",
            current_step=5,
            expected_step=6,
            device_snapshot={"elapsed_seconds": 120},
        )

    @action(description="抛出取头失败，仅提供框架内置操作")
    async def raise_tip_pickup_error(self) -> SimpleResult:
        raise TipPickupError(
            "tip 取头失败（模拟）",
            tip_position="A1",
            remaining_tips=95,
            device_snapshot={"tip_position": "A1"},
        )

    @action(description="同步版本：首次失败，用户重试后成功")
    def fail_once_then_success_sync(self) -> SimpleResult:
        self._sync_attempts += 1
        if self._sync_attempts == 1:
            raise SensorError(
                "首次同步温度读取失败（模拟）",
                device_snapshot={"sensor": "temp_sync", "attempt": self._sync_attempts},
            )
        return {"success": True, "message": f"succeeded on attempt {self._sync_attempts}"}

    @action(description="同步版本：模拟长耗时操作", timeout=2.0)
    def run_long_sync(self, duration: float = 5.0) -> SimpleResult:
        time.sleep(duration)
        return {"success": True, "message": f"slept {duration}s"}

    @action(description="重置首次失败场景的计数")
    def reset(self) -> SimpleResult:
        self._async_attempts = 0
        self._sync_attempts = 0
        return {"success": True, "message": "fault injection state reset"}


# 本地启动示例（凭据通过环境或 CLI 安全传入，不写入源码）：
# unilab --graph unilabos/test/experiments/fault_injection.json --addr test --disable_browser
