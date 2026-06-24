"""
Thermo Fisher Cytomat 2C 仿真 RViz Backend

设备功能：微孔板自动存储孵育器（内置加热 / 可选制冷）
模型完整度：⭐⭐⭐⭐ — 单体 mesh（机身）+ XACRO 中预留了注释关节：
            • 0_door_joint    revolute Z  [0, 1.93 rad]  — 前门开合
            • 0_reardoor_joint prismatic Z [0, -0.13 m]  — 后门推拉（传输口）

仿真逻辑：
  load_plate()   → 后门打开 → 模拟等待放板 → 后门关闭
  unload_plate() → 后门打开 → 模拟等待取板 → 后门关闭
  open_door()    → 前门旋转打开（人工访问）
  close_door()   → 前门关闭

依赖：
    unilabos/devices/ros_dev/simple_joint_publisher_node.py

注意：XACRO 中门关节已预留，但门的 mesh 文件当前不存在，
      关节将发布关节状态但在 RViz 中无对应可视化几何体。
"""

import asyncio
import threading
from typing import Optional

import rclpy

from unilabos.devices.ros_dev.simple_joint_publisher_node import SimpleJointPublisher

try:
    from pylabrobot.heating_shaking.backend import HeaterShakerBackend
    _PLR_AVAILABLE = True
except ImportError:
    class HeaterShakerBackend:  # type: ignore
        async def setup(self): pass
        async def stop(self): pass
    _PLR_AVAILABLE = False


# 关节参数
_FRONT_DOOR_OPEN  = 1.93   # rad（约 110°）
_FRONT_DOOR_CLOSE = 0.0
_REAR_DOOR_OPEN   = -0.13  # m（向 -Z 推出 130 mm）
_REAR_DOOR_CLOSE  = 0.0

_DOOR_ROT_SPEED   = 0.8    # rad/s
_DOOR_LIN_SPEED   = 0.03   # m/s

# 模拟板进出等待时间（秒）
_TRANSFER_DWELL   = 1.5


class ThermoFisherCytomat2CRvizBackend(HeaterShakerBackend):
    """
    Thermo Fisher Cytomat 2C 孵育器仿真 Backend。

    Parameters
    ----------
    device_id : str
        与 URDF robot name 对应，默认 "thermo_cytomat2c"。
    """

    def __init__(self, device_id: str = "thermo_cytomat2c"):
        if _PLR_AVAILABLE:
            super().__init__()
        self.device_id = device_id
        self._target_temperature: float = 37.0
        self._current_temperature: float = 22.0
        self._shaking: bool = False

        self._publisher: Optional[SimpleJointPublisher] = None
        self._executor = None
        self._executor_thread = None

    # ------------------------------------------------------------------
    # MachineBackend interface
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        if not rclpy.ok():
            rclpy.init()

        self._publisher = SimpleJointPublisher(
            device_id=self.device_id,
            joint_names=["0_door_joint", "0_reardoor_joint"],
            rate=50,
        )
        self._executor = rclpy.executors.MultiThreadedExecutor()
        self._executor.add_node(self._publisher)
        self._executor_thread = threading.Thread(
            target=self._executor.spin, daemon=True
        )
        self._executor_thread.start()

        self._publisher.set_immediate({
            "0_door_joint": _FRONT_DOOR_CLOSE,
            "0_reardoor_joint": _REAR_DOOR_CLOSE,
        })
        print(f"[{self.device_id}] Setup complete. Both doors closed.")

    async def stop(self) -> None:
        if self._executor and self._publisher:
            self._executor.remove_node(self._publisher)
        if self._executor_thread and self._executor_thread.is_alive():
            self._executor.shutdown()
        print(f"[{self.device_id}] Stopped.")

    # ------------------------------------------------------------------
    # Incubator operations（PLR 通过直接命令驱动，这里封装为动画操作）
    # ------------------------------------------------------------------

    async def load_plate(self) -> None:
        """模拟从后传输口放入一块板。"""
        loop = asyncio.get_event_loop()

        print(f"[{self.device_id}] Opening rear transfer door...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_reardoor_joint": _REAR_DOOR_OPEN}, speed=_DOOR_LIN_SPEED
            ),
        )
        print(f"[{self.device_id}] Transfer window open. Loading plate...")
        await asyncio.sleep(_TRANSFER_DWELL)

        print(f"[{self.device_id}] Closing rear transfer door...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_reardoor_joint": _REAR_DOOR_CLOSE}, speed=_DOOR_LIN_SPEED
            ),
        )
        print(f"[{self.device_id}] Plate loaded.")

    async def unload_plate(self) -> None:
        """模拟从后传输口取出一块板。"""
        loop = asyncio.get_event_loop()

        print(f"[{self.device_id}] Opening rear transfer door for unload...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_reardoor_joint": _REAR_DOOR_OPEN}, speed=_DOOR_LIN_SPEED
            ),
        )
        await asyncio.sleep(_TRANSFER_DWELL)

        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_reardoor_joint": _REAR_DOOR_CLOSE}, speed=_DOOR_LIN_SPEED
            ),
        )
        print(f"[{self.device_id}] Plate unloaded.")

    async def open_door(self) -> None:
        """打开前门（人工访问/维护）。"""
        loop = asyncio.get_event_loop()
        print(f"[{self.device_id}] Opening front door...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_door_joint": _FRONT_DOOR_OPEN}, speed=_DOOR_ROT_SPEED
            ),
        )
        print(f"[{self.device_id}] Front door open.")

    async def close_door(self) -> None:
        """关闭前门。"""
        loop = asyncio.get_event_loop()
        print(f"[{self.device_id}] Closing front door...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_door_joint": _FRONT_DOOR_CLOSE}, speed=_DOOR_ROT_SPEED
            ),
        )
        print(f"[{self.device_id}] Front door closed.")

    # ------------------------------------------------------------------
    # HeaterShakerBackend interface
    # ------------------------------------------------------------------

    @property
    def supports_locking(self) -> bool:
        return False

    async def lock_plate(self) -> None:
        print(f"[{self.device_id}] Lock plate (simulated).")

    async def unlock_plate(self) -> None:
        print(f"[{self.device_id}] Unlock plate (simulated).")

    async def start_shaking(self, speed: float) -> None:
        self._shaking = True
        print(f"[{self.device_id}] Shaking at {speed} RPM (simulated, no motion).")

    async def stop_shaking(self) -> None:
        self._shaking = False
        print(f"[{self.device_id}] Stop shaking.")

    @property
    def supports_active_cooling(self) -> bool:
        return False

    async def set_temperature(self, temperature: float) -> None:
        self._target_temperature = temperature
        print(f"[{self.device_id}] Set temperature → {temperature}°C (simulated).")

    async def get_current_temperature(self) -> float:
        diff = self._target_temperature - self._current_temperature
        self._current_temperature += min(abs(diff), 0.5) * (1 if diff > 0 else -1)
        return round(self._current_temperature, 1)

    async def deactivate(self) -> None:
        self._target_temperature = 22.0
        print(f"[{self.device_id}] Temperature deactivated.")

    def serialize(self) -> dict:
        return {"type": self.__class__.__name__, "device_id": self.device_id}
