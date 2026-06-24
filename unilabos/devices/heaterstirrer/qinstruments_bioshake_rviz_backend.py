"""
QInstruments BioShake 仿真 RViz Backend

适用型号：BioShake 3000T / BioShake 5000 / BioShake Q1
设备功能：加热振荡器（orbiting shaker + 温控）
模型完整度：⭐⭐⭐ — 单体 mesh，无预设关节。
            仿真在 XACRO 中动态添加 "shake_joint"（绕 Z 轴连续旋转，
            模拟偏心振荡）。

仿真逻辑：
  - start_shaking(rpm)  → shake_joint 持续旋转（速度 = rpm/60 * 2π rad/s）
  - stop_shaking()      → 旋转停止，复位到 0
  - lock_plate()        → 打印（无运动）
  - set_temperature()   → 打印（无运动）

注意：BioShake 实为轨道振荡（偏心圆周运动），单关节旋转是近似仿真。

依赖：
    unilabos/devices/ros_dev/simple_joint_publisher_node.py
"""

import asyncio
import math
import threading
import time
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


# 每次振荡动画的角度增量（弧度），仿真轨道振荡
_SHAKE_STEP = math.radians(30)


class QInstrumentsBioShakeRvizBackend(HeaterShakerBackend):
    """
    QInstruments BioShake 振荡器仿真 Backend。

    通过 shake_joint 绕 Z 轴连续旋转来模拟振荡。

    Parameters
    ----------
    device_id : str
        与 URDF robot name 对应，可选 "qinstruments_bioshake_3000t" /
        "qinstruments_bioshake_5000" / "qinstruments_bioshake_q1"。
    """

    def __init__(self, device_id: str = "qinstruments_bioshake_3000t"):
        if _PLR_AVAILABLE:
            super().__init__()
        self.device_id = device_id
        self._rpm: float = 0.0
        self._target_temperature: float = 25.0
        self._current_temperature: float = 22.0
        self._shaking: bool = False
        self._plate_locked: bool = False

        self._publisher: Optional[SimpleJointPublisher] = None
        self._executor = None
        self._executor_thread = None
        self._shake_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # MachineBackend interface
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        if not rclpy.ok():
            rclpy.init()

        self._publisher = SimpleJointPublisher(
            device_id=self.device_id,
            joint_names=["shake_joint"],
            rate=50,
        )
        self._executor = rclpy.executors.MultiThreadedExecutor()
        self._executor.add_node(self._publisher)
        self._executor_thread = threading.Thread(
            target=self._executor.spin, daemon=True
        )
        self._executor_thread.start()

        self._publisher.set_immediate({"shake_joint": 0.0})
        print(f"[{self.device_id}] Setup complete.")

    async def stop(self) -> None:
        await self.stop_shaking()
        if self._executor and self._publisher:
            self._executor.remove_node(self._publisher)
        if self._executor_thread and self._executor_thread.is_alive():
            self._executor.shutdown()
        print(f"[{self.device_id}] Stopped.")

    # ------------------------------------------------------------------
    # ShakerBackend interface
    # ------------------------------------------------------------------

    @property
    def supports_locking(self) -> bool:
        return True

    async def lock_plate(self) -> None:
        self._plate_locked = True
        print(f"[{self.device_id}] Plate locked (simulated).")

    async def unlock_plate(self) -> None:
        self._plate_locked = False
        print(f"[{self.device_id}] Plate unlocked (simulated).")

    async def start_shaking(self, speed: float) -> None:
        """
        开始振荡。

        Parameters
        ----------
        speed : float
            转速（RPM）。
        """
        if self._shaking:
            await self.stop_shaking()

        self._rpm = speed
        self._shaking = True
        self._stop_event.clear()

        # 后台线程持续递增 shake_joint 角度
        self._shake_thread = threading.Thread(
            target=self._shake_loop, daemon=True
        )
        self._shake_thread.start()
        print(f"[{self.device_id}] Start shaking at {speed} RPM.")

    async def stop_shaking(self) -> None:
        self._shaking = False
        self._stop_event.set()
        if self._shake_thread and self._shake_thread.is_alive():
            self._shake_thread.join(timeout=2.0)
        if self._publisher:
            self._publisher.move_to({"shake_joint": 0.0}, speed=math.pi)
        print(f"[{self.device_id}] Stop shaking.")

    # ------------------------------------------------------------------
    # TemperatureControllerBackend interface
    # ------------------------------------------------------------------

    @property
    def supports_active_cooling(self) -> bool:
        return False

    async def set_temperature(self, temperature: float) -> None:
        self._target_temperature = temperature
        print(f"[{self.device_id}] Set temperature → {temperature}°C (simulated).")

    async def get_current_temperature(self) -> float:
        diff = self._target_temperature - self._current_temperature
        self._current_temperature += min(abs(diff), 0.3) * (1 if diff > 0 else -1)
        return round(self._current_temperature, 1)

    async def deactivate(self) -> None:
        self._target_temperature = 22.0
        print(f"[{self.device_id}] Temperature deactivated.")

    def serialize(self) -> dict:
        return {"type": self.__class__.__name__, "device_id": self.device_id}

    # ------------------------------------------------------------------
    # Internal shake loop
    # ------------------------------------------------------------------

    def _shake_loop(self) -> None:
        """后台线程：按 RPM 持续累加 shake_joint 角度，模拟轨道振荡。"""
        if self._publisher is None:
            return
        angle = self._publisher.get_position("shake_joint")
        # RPM → rad/s
        omega = self._rpm / 60.0 * 2 * math.pi
        dt = 0.02  # 50 Hz

        while not self._stop_event.is_set():
            angle += omega * dt
            # 折叠到 [-π, π] 防止溢出
            angle = (angle + math.pi) % (2 * math.pi) - math.pi
            self._publisher.set_immediate({"shake_joint": angle})
            time.sleep(dt)
