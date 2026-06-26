"""
BMG Labtech CLARIOstar Plus 仿真 RViz Backend

设备功能：多模式酶标仪（吸光度 / 荧光 / 化学发光）
模型完整度：⭐⭐⭐ — 单体 mesh（base）+ socketTypeGenericSbsFootprint 定义了
            板的坐标位置（xyz="-0.09 -0.304 0.08"）。

仿真逻辑：
  - open()  → tray_joint（棱柱关节）伸出，板托平移至读取位外侧 130 mm
  - close() → tray_joint 缩回到 0，板进入光学测量腔
  - read_absorbance / read_fluorescence / read_luminescence
            → open → 模拟等待 → close → 返回随机占位数据

关节定义（需同步更新 modal.xacro）：
    tray_joint  prismatic  沿 X 轴  range [0, 0.13]  (米)

依赖：
    unilabos/devices/ros_dev/simple_joint_publisher_node.py
"""

import asyncio
import random
import threading
from typing import Any, Dict, List, Optional

import rclpy

from unilabos.devices.ros_dev.simple_joint_publisher_node import SimpleJointPublisher

try:
    from pylabrobot.plate_reading.backend import PlateReaderBackend
    from pylabrobot.resources.plate import Plate
    from pylabrobot.resources.well import Well
    _PLR_AVAILABLE = True
except ImportError:
    class PlateReaderBackend:  # type: ignore
        async def setup(self): pass
        async def stop(self): pass
    Plate = object
    Well = object
    _PLR_AVAILABLE = False


# 板托行程（米）
_TRAY_OPEN  = 0.13   # 完全伸出
_TRAY_CLOSE = 0.0    # 完全缩回（测量位）
_TRAY_SPEED = 0.04   # m/s（50 Hz 下每帧 0.8 mm）

# 模拟测量耗时（秒/孔）
_READ_TIME_PER_WELL = 0.02


class CLARIOstarPlusRvizBackend(PlateReaderBackend):
    """
    BMG CLARIOstar Plus 酶标仪仿真 Backend。

    Parameters
    ----------
    device_id : str
        与 URDF robot name 对应，默认 "bmg_labtech_clariostar_plus"。
    """

    def __init__(self, device_id: str = "bmg_labtech_clariostar_plus"):
        if _PLR_AVAILABLE:
            super().__init__()
        self.device_id = device_id
        self._tray_open: bool = True   # 初始状态：开（板托在外）

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
            joint_names=["tray_joint"],
            rate=50,
        )
        self._executor = rclpy.executors.MultiThreadedExecutor()
        self._executor.add_node(self._publisher)
        self._executor_thread = threading.Thread(
            target=self._executor.spin, daemon=True
        )
        self._executor_thread.start()

        # 初始状态：板托伸出（等待加板）
        self._publisher.set_immediate({"tray_joint": _TRAY_OPEN})
        self._tray_open = True
        print(f"[{self.device_id}] Setup complete. Tray extended.")

    async def stop(self) -> None:
        if self._executor and self._publisher:
            self._executor.remove_node(self._publisher)
        if self._executor_thread and self._executor_thread.is_alive():
            self._executor.shutdown()
        print(f"[{self.device_id}] Stopped.")

    # ------------------------------------------------------------------
    # PlateReaderBackend interface
    # ------------------------------------------------------------------

    async def open(self, **kwargs: Any) -> None:
        """板托伸出，等待放/取板。"""
        if self._tray_open:
            return
        print(f"[{self.device_id}] Opening tray...")
        await self._move_tray(_TRAY_OPEN)
        self._tray_open = True
        print(f"[{self.device_id}] Tray open.")

    async def close(self, plate: Optional[object] = None, **kwargs: Any) -> None:
        """板托缩回，板进入测量腔。"""
        if not self._tray_open:
            return
        print(f"[{self.device_id}] Closing tray...")
        await self._move_tray(_TRAY_CLOSE)
        self._tray_open = False
        print(f"[{self.device_id}] Tray closed. Plate loaded.")

    async def read_absorbance(
        self,
        plate: Optional[object] = None,
        wells: Optional[List[object]] = None,
        wavelength: int = 0,
        report: str = "OD",
        **kwargs: Any,
    ) -> List[Dict]:
        """模拟吸光度测量，返回随机占位数据。"""
        wells = self._resolve_wells(plate, wells)
        await self._run_measurement("absorbance", len(wells))
        return [
            {
                "wavelength": wavelength,
                "report": report,
                "time": 0.0,
                "temperature": 25.0,
                "data": [[round(random.uniform(0.0, 2.0), 4) for _ in range(12)] for _ in range(8)],
            }
        ]

    async def read_fluorescence(
        self,
        plate: Optional[object] = None,
        wells: Optional[List[object]] = None,
        excitation_wavelength: int = 0,
        emission_wavelength: int = 0,
        focal_height: float = 13,
        **kwargs: Any,
    ) -> List[Dict]:
        """模拟荧光测量，返回随机占位数据。"""
        wells = self._resolve_wells(plate, wells)
        await self._run_measurement("fluorescence", len(wells))
        return [
            {
                "ex_wavelength": excitation_wavelength,
                "em_wavelength": emission_wavelength,
                "time": 0.0,
                "temperature": 25.0,
                "data": [[round(random.uniform(0.0, 65535.0), 1) for _ in range(12)] for _ in range(8)],
            }
        ]

    async def read_luminescence(
        self,
        plate: Optional[object] = None,
        wells: Optional[List[object]] = None,
        focal_height: float = 13,
        **kwargs: Any,
    ) -> List[Dict]:
        """模拟化学发光测量，返回随机占位数据。"""
        wells = self._resolve_wells(plate, wells)
        await self._run_measurement("luminescence", len(wells))
        return [
            {
                "time": 0.0,
                "temperature": 25.0,
                "data": [[round(random.uniform(0.0, 1e6), 1) for _ in range(12)] for _ in range(8)],
            }
        ]

    async def get_stat(self) -> str:
        """兼容旧的 backend-only registry；仿真后端始终返回 ready。"""
        return "ready"

    def serialize(self) -> dict:
        return {"type": self.__class__.__name__, "device_id": self.device_id}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _move_tray(self, target: float) -> None:
        """异步包装板托平移动作。"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to({"tray_joint": target}, speed=_TRAY_SPEED),
        )

    def _resolve_wells(
        self, plate: Optional[object], wells: Optional[List[object]]
    ) -> List[object]:
        if wells is not None:
            return wells
        if plate is not None and hasattr(plate, "get_all_items"):
            return list(plate.get_all_items())
        return [object()] * 96

    async def _run_measurement(self, mode: str, num_wells: int) -> None:
        """通用测量流程：关托 → 等待 → 开托。"""
        await self.close()
        duration = num_wells * _READ_TIME_PER_WELL
        print(f"[{self.device_id}] Reading {mode} ({num_wells} wells, ~{duration:.1f}s)...")
        await asyncio.sleep(duration)
        await self.open()
