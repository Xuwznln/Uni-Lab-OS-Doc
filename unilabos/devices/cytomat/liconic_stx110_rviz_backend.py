"""
LiCONiC STX110 仿真 RViz Backend

设备功能：转盘式板存储孵育器（5 个货架位，可加热）
模型完整度：⭐⭐⭐⭐⭐ — 具有 base + carousel 两个独立 mesh，
            以及已定义的 carousel_joint（continuous，绕 Z 轴旋转）。

仿真逻辑：
  - load_plate(slot)   → 转盘旋转至对应插槽角度
  - unload_plate(slot) → 转盘旋转至出口角度
  - set_temperature    → 仅打印（无运动）
  - get_temperature    → 返回设定值

STX110 共 5 个货架，角度间隔 72°（2π/5），slot 编号 1–5。

依赖：
    unilabos/devices/ros_dev/simple_joint_publisher_node.py
"""

import asyncio
import math
import threading
from typing import Optional

import rclpy

from unilabos.devices.ros_dev.simple_joint_publisher_node import SimpleJointPublisher

try:
    from pylabrobot.storage.backend import IncubatorBackend
    _PLR_AVAILABLE = True
except ImportError:
    # 未安装 pylabrobot 时退化为哑类，方便独立测试
    class IncubatorBackend:  # type: ignore
        async def setup(self): pass
        async def stop(self): pass
    _PLR_AVAILABLE = False


# 5 个货架的角度（弧度），从正前方开始逆时针
_SLOT_ANGLES = {i: math.radians((i - 1) * 72) for i in range(1, 6)}
# 出口/传输窗口角度（正前方）
_TRANSFER_ANGLE = 0.0
# 转盘旋转速度（rad/s）
_CAROUSEL_SPEED = 0.8


class LiconicStx110RvizBackend(IncubatorBackend):
    """
    LiCONiC STX110 孵育器仿真 Backend。

    在 RViz 中驱动转盘旋转，模拟板的存取动作。

    Parameters
    ----------
    device_id : str
        与 URDF 中 robot name 一致，默认 "liconic_stx110"。
    """

    def __init__(self, device_id: str = "liconic_stx110"):
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
            joint_names=["0_carousel_joint"],
            rate=50,
        )
        self._executor = rclpy.executors.MultiThreadedExecutor()
        self._executor.add_node(self._publisher)
        self._executor_thread = threading.Thread(
            target=self._executor.spin, daemon=True
        )
        self._executor_thread.start()

        # 复位到出口位置
        self._publisher.set_immediate({"0_carousel_joint": _TRANSFER_ANGLE})
        print(f"[{self.device_id}] Setup complete. Carousel at transfer position.")

    async def stop(self) -> None:
        if self._executor and self._publisher:
            self._executor.remove_node(self._publisher)
        if self._executor_thread and self._executor_thread.is_alive():
            self._executor.shutdown()
        print(f"[{self.device_id}] Stopped.")

    # ------------------------------------------------------------------
    # Incubator / storage operations
    # ------------------------------------------------------------------

    def _coerce_slot(self, site=None, default: int = 1) -> int:
        if isinstance(site, int):
            return site
        if isinstance(site, str) and site.isdigit():
            return int(site)
        for attr in ("name", "resource_name"):
            value = getattr(site, attr, None)
            if isinstance(value, str):
                digits = "".join(ch for ch in value if ch.isdigit())
                if digits:
                    return int(digits)
        return default

    async def load_plate(self, slot: int = 1, **backend_kwargs) -> None:
        """
        将转盘旋转使 slot 对准传输窗口，模拟机器人放板。

        Parameters
        ----------
        slot : int
            目标货架编号（1–5）。
        """
        if slot not in _SLOT_ANGLES:
            raise ValueError(f"Invalid slot {slot}. Must be 1–5.")

        angle = _SLOT_ANGLES[slot]
        print(f"[{self.device_id}] Rotating carousel to slot {slot} (angle={math.degrees(angle):.1f}°)")
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_carousel_joint": angle}, speed=_CAROUSEL_SPEED
            ),
        )
        print(f"[{self.device_id}] Slot {slot} aligned. Ready for plate load.")

    async def unload_plate(self, slot: int = 1, **backend_kwargs) -> None:
        """
        旋转转盘取出指定 slot 的板。

        Parameters
        ----------
        slot : int
            目标货架编号（1–5）。
        """
        if slot not in _SLOT_ANGLES:
            raise ValueError(f"Invalid slot {slot}. Must be 1–5.")

        angle = _SLOT_ANGLES[slot]
        print(f"[{self.device_id}] Rotating carousel to slot {slot} for unload (angle={math.degrees(angle):.1f}°)")
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_carousel_joint": angle}, speed=_CAROUSEL_SPEED
            ),
        )
        print(f"[{self.device_id}] Plate unloaded from slot {slot}.")
        # 回到出口
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"0_carousel_joint": _TRANSFER_ANGLE}, speed=_CAROUSEL_SPEED
            ),
        )

    async def open_door(self, **backend_kwargs) -> None:
        """STX110 uses a carousel transfer window; no moving door in RViz."""
        print(f"[{self.device_id}] Open door (simulated; transfer window available).")

    async def close_door(self, **backend_kwargs) -> None:
        """STX110 uses a carousel transfer window; no moving door in RViz."""
        print(f"[{self.device_id}] Close door (simulated; transfer window available).")

    async def fetch_plate_to_loading_tray(
        self, plate=None, plate_name=None, **backend_kwargs
    ) -> None:
        slot = backend_kwargs.pop("slot", None)
        if slot is None:
            slot = self._coerce_slot(getattr(plate, "parent", None))
        await self.unload_plate(slot=slot, **backend_kwargs)

    async def take_in_plate(self, plate=None, site="smallest", **backend_kwargs) -> None:
        slot = backend_kwargs.pop("slot", None)
        if slot is None:
            slot = self._coerce_slot(site)
        await self.load_plate(slot=slot, **backend_kwargs)

    # ------------------------------------------------------------------
    # IncubatorBackend interface（孵育器以加热为主，振荡为辅）
    # ------------------------------------------------------------------

    @property
    def supports_locking(self) -> bool:
        return False

    async def lock_plate(self) -> None:
        print(f"[{self.device_id}] Lock plate (simulated).")

    async def unlock_plate(self) -> None:
        print(f"[{self.device_id}] Unlock plate (simulated).")

    async def start_shaking(
        self, frequency: Optional[float] = None, speed: Optional[float] = None, **kwargs
    ) -> None:
        """STX110 本身不振荡，此处仅打印。"""
        rpm = frequency if frequency is not None else speed
        if rpm is None:
            rpm = 0.0
        self._shaking = True
        print(f"[{self.device_id}] Start shaking at {rpm} RPM (simulated, no motion).")

    async def shake(self, speed: float, *args, **kwargs) -> None:
        """ShakerBackend 抽象接口。STX110 不振荡，委托给 start_shaking。"""
        await self.start_shaking(speed=speed)

    async def stop_shaking(self, **kwargs) -> None:
        self._shaking = False
        print(f"[{self.device_id}] Stop shaking (simulated).")

    # TemperatureControllerBackend interface
    @property
    def supports_active_cooling(self) -> bool:
        return False

    async def set_temperature(self, temperature: float) -> None:
        self._target_temperature = temperature
        print(f"[{self.device_id}] Set temperature → {temperature}°C (simulated).")

    async def get_current_temperature(self) -> float:
        # 简单模拟：以 0.5°C/s 向目标靠近
        diff = self._target_temperature - self._current_temperature
        self._current_temperature += min(abs(diff), 0.5) * (1 if diff > 0 else -1)
        return round(self._current_temperature, 1)

    async def get_temperature(self) -> float:
        return await self.get_current_temperature()

    async def deactivate(self) -> None:
        self._target_temperature = 22.0
        print(f"[{self.device_id}] Temperature deactivated.")

    def serialize(self) -> dict:
        return {"type": self.__class__.__name__, "device_id": self.device_id}
