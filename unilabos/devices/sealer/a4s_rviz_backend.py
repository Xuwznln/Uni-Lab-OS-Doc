"""
Brooks / Azenta a4S 封膜机仿真 RViz Backend

设备功能：自动封膜机（热压贴膜密封微孔板）
模型完整度：⭐⭐⭐ — 单体 mesh + socketTypeGenericSbsFootprint。

仿真逻辑（两关节）：
  feed_joint   prismatic  沿 Y 轴  [0, 0.146]  — 板水平推入
  press_joint  prismatic  沿 Z 轴  [0, -0.05]  — 加热压盘下压封膜

封膜流程：
  1. open()  → feed_joint = 0（等待放板）
  2. seal(temperature, duration)
            → close（推板入）
            → press_joint 下压
            → 停留 duration 秒（热压）
            → press_joint 抬起
            → feed_joint 退出

关节定义（需同步更新 modal.xacro）：
    feed_joint   prismatic  Y  [0, 0.146]
    press_joint  prismatic  Z  [0, -0.05]

依赖：
    unilabos/devices/ros_dev/simple_joint_publisher_node.py
"""

import asyncio
import threading
from typing import Optional

import rclpy

from unilabos.devices.ros_dev.simple_joint_publisher_node import SimpleJointPublisher

try:
    from pylabrobot.sealing.backend import SealerBackend
    _PLR_AVAILABLE = True
except ImportError:
    class SealerBackend:  # type: ignore
        async def setup(self): pass
        async def stop(self): pass
    _PLR_AVAILABLE = False


# 行程参数（米）
_FEED_INSERT  = 0.146   # 板完全推入（与 XACRO socket 坐标对应）
_FEED_HOME    = 0.0
_PRESS_DOWN   = -0.05   # 压盘下压行程
_PRESS_UP     = 0.0
_FEED_SPEED   = 0.04    # m/s
_PRESS_SPEED  = 0.008   # m/s（缓慢下压模拟热压）


class A4SSealerRvizBackend(SealerBackend):
    """
    Brooks a4S 封膜机仿真 Backend。

    Parameters
    ----------
    device_id : str
        与 URDF robot name 对应，默认 "brooks_a4ssealer"。
    """

    def __init__(self, device_id: str = "brooks_a4ssealer"):
        if _PLR_AVAILABLE:
            super().__init__()
        self.device_id = device_id
        self._target_temperature: float = 170.0  # °C，典型封膜温度
        self._current_temperature: float = 22.0
        self._tray_open: bool = True

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
            joint_names=["feed_joint", "press_joint"],
            rate=50,
        )
        self._executor = rclpy.executors.MultiThreadedExecutor()
        self._executor.add_node(self._publisher)
        self._executor_thread = threading.Thread(
            target=self._executor.spin, daemon=True
        )
        self._executor_thread.start()

        self._publisher.set_immediate({"feed_joint": _FEED_HOME, "press_joint": _PRESS_UP})
        self._tray_open = True
        print(f"[{self.device_id}] Setup complete. Ready for plate.")

    async def stop(self) -> None:
        if self._executor and self._publisher:
            self._executor.remove_node(self._publisher)
        if self._executor_thread and self._executor_thread.is_alive():
            self._executor.shutdown()
        print(f"[{self.device_id}] Stopped.")

    # ------------------------------------------------------------------
    # SealerBackend interface
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """板托退出，等待放/取板。"""
        if self._tray_open:
            return
        loop = asyncio.get_event_loop()
        print(f"[{self.device_id}] Opening (feeding plate out)...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to({"feed_joint": _FEED_HOME}, speed=_FEED_SPEED),
        )
        self._tray_open = True
        print(f"[{self.device_id}] Tray open.")

    async def close(self) -> None:
        """推板入封膜腔。"""
        if not self._tray_open:
            return
        loop = asyncio.get_event_loop()
        print(f"[{self.device_id}] Feeding plate in...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to({"feed_joint": _FEED_INSERT}, speed=_FEED_SPEED),
        )
        self._tray_open = False
        print(f"[{self.device_id}] Plate loaded.")

    async def seal(self, temperature: int, duration: float) -> None:
        """
        执行封膜流程。

        Parameters
        ----------
        temperature : int
            封膜温度（°C），典型值 160–180°C。
        duration : float
            热压持续时间（秒），典型值 1.0–3.0s。
        """
        loop = asyncio.get_event_loop()

        await self.set_temperature(float(temperature))
        await self.close()

        # 压盘下压
        print(f"[{self.device_id}] Pressing at {temperature}°C for {duration}s...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to({"press_joint": _PRESS_DOWN}, speed=_PRESS_SPEED),
        )

        # 热压停顿
        await asyncio.sleep(duration)

        # 压盘抬起
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to({"press_joint": _PRESS_UP}, speed=_PRESS_SPEED),
        )

        await self.open()
        print(f"[{self.device_id}] Seal complete.")

    async def set_temperature(self, temperature: float) -> None:
        self._target_temperature = temperature
        print(f"[{self.device_id}] Set temperature → {temperature}°C (simulated).")

    async def get_temperature(self) -> float:
        diff = self._target_temperature - self._current_temperature
        self._current_temperature += min(abs(diff), 5.0) * (1 if diff > 0 else -1)
        return round(self._current_temperature, 1)

    def serialize(self) -> dict:
        return {"type": self.__class__.__name__, "device_id": self.device_id}
