"""
Brooks / Azenta XPeel 仿真 RViz Backend

设备功能：自动揭膜机（高速去除微孔板密封膜）
模型完整度：⭐⭐⭐ — 单体 mesh + socketTypeGenericSbsFootprint（板位坐标）。

仿真逻辑（两关节）：
  feed_joint   prismatic  沿 Y 轴  [0, 0.29]  — 板水平推入机器
  peel_joint   prismatic  沿 Z 轴  [0, -0.02] — 揭膜头垂直下压

完整揭膜流程：
  1. open()   → feed_joint = 0（板托归位/等待）
  2. peel()   → feed_joint 推入 → peel_joint 下压 → 停顿 → peel_joint 回 → feed_joint 退出

关节定义（需同步更新 modal.xacro）：
    feed_joint  prismatic  Y  [0, 0.29]
    peel_joint  prismatic  Z  [0, -0.02]

依赖：
    unilabos/devices/ros_dev/simple_joint_publisher_node.py
"""

import asyncio
import threading
from typing import Optional

import rclpy

from unilabos.devices.ros_dev.simple_joint_publisher_node import SimpleJointPublisher

try:
    from pylabrobot.peeling.backend import PeelerBackend
    _PLR_AVAILABLE = True
except ImportError:
    class PeelerBackend:  # type: ignore
        async def setup(self): pass
        async def stop(self): pass
    _PLR_AVAILABLE = False


# 行程参数（米）
_FEED_INSERT = 0.29    # 板完全推入
_FEED_HOME   = 0.0     # 板托归位
_PEEL_DOWN   = -0.02   # 揭膜头下压量
_PEEL_UP     = 0.0     # 揭膜头抬起
_FEED_SPEED  = 0.05    # m/s
_PEEL_SPEED  = 0.005   # m/s（缓慢下压，模拟撕膜力）

# 撕膜停留时间（秒）
_PEEL_DWELL  = 1.5


class XPeelRvizBackend(PeelerBackend):
    """
    Brooks XPeel 揭膜机仿真 Backend。

    Parameters
    ----------
    device_id : str
        与 URDF robot name 对应，默认 "brooks_xpeel"。
    """

    def __init__(self, device_id: str = "brooks_xpeel"):
        if _PLR_AVAILABLE:
            super().__init__()
        self.device_id = device_id

        self._publisher: Optional[SimpleJointPublisher] = None
        self._executor = None
        self._executor_thread = None

    # ------------------------------------------------------------------
    # MachineBackend interface
    # ------------------------------------------------------------------

    async def setup(self, **kwargs) -> None:
        if not rclpy.ok():
            rclpy.init()

        self._publisher = SimpleJointPublisher(
            device_id=self.device_id,
            joint_names=["feed_joint", "peel_joint"],
            rate=50,
        )
        self._executor = rclpy.executors.MultiThreadedExecutor()
        self._executor.add_node(self._publisher)
        self._executor_thread = threading.Thread(
            target=self._executor.spin, daemon=True
        )
        self._executor_thread.start()

        # 初始化：板托在外，揭膜头抬起
        self._publisher.set_immediate({"feed_joint": _FEED_HOME, "peel_joint": _PEEL_UP})
        print(f"[{self.device_id}] Setup complete. Ready for plate.")

    async def stop(self, **kwargs) -> None:
        if self._executor and self._publisher:
            self._executor.remove_node(self._publisher)
        if self._executor_thread and self._executor_thread.is_alive():
            self._executor.shutdown()
        print(f"[{self.device_id}] Stopped.")

    # ------------------------------------------------------------------
    # PeelerBackend interface
    # ------------------------------------------------------------------

    async def peel(
        self,
        begin_location: float = 0,
        fast: bool = False,
        adhere_time: float = 2.5,
        **kwargs,
    ) -> None:
        """
        执行完整揭膜流程：
        1. 推板入机
        2. 揭膜头下压
        3. 保持停顿（撕膜）
        4. 揭膜头抬起
        5. 板退出
        """
        loop = asyncio.get_event_loop()

        # Step 1: 推板入机
        print(f"[{self.device_id}] [1/4] Feeding plate in...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to({"feed_joint": _FEED_INSERT}, speed=_FEED_SPEED),
        )

        # Step 2: 揭膜头下压
        print(f"[{self.device_id}] [2/4] Peeling head pressing down...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to({"peel_joint": _PEEL_DOWN}, speed=_PEEL_SPEED),
        )

        # Step 3: 撕膜停顿
        dwell = adhere_time if adhere_time is not None else _PEEL_DWELL
        print(f"[{self.device_id}] [3/4] Holding for peel ({dwell}s)...")
        await asyncio.sleep(dwell)

        # Step 4: 揭膜头抬起
        print(f"[{self.device_id}] [4/4] Peeling head lifting and plate exiting...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"peel_joint": _PEEL_UP, "feed_joint": _FEED_HOME},
                speed=_FEED_SPEED,
            ),
        )
        print(f"[{self.device_id}] Peel complete.")

    async def restart(self, **kwargs) -> None:
        """复位所有关节到初始位置。"""
        loop = asyncio.get_event_loop()
        print(f"[{self.device_id}] Restarting (homing)...")
        await loop.run_in_executor(
            None,
            lambda: self._publisher.move_to(
                {"feed_joint": _FEED_HOME, "peel_joint": _PEEL_UP},
                speed=_FEED_SPEED,
            ),
        )
        print(f"[{self.device_id}] Restart complete.")

    def serialize(self) -> dict:
        return {"type": self.__class__.__name__, "device_id": self.device_id}
