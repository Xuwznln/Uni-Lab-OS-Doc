"""
VirtualLiconicStx110 — LiCONiC STX110 转盘孵育器仿真驱动（RViz 引擎适配）。

08v2 sim 替换的虚拟驱动：sim 模式下替换真实设备 incubator.liconic_stx110。
通过 SimpleJointPublisher 发布 /joint_states 驱动 RViz 中转盘（0_carousel_joint）旋转，
模拟 5 个货架（72° 间隔）的存/取板动作。

自标记（M-8 / 08v2）：
  driver_runtime_kind = virtual
  virtual_driver_kind = engine_adapter
  sim_engine          = rviz
"""

import asyncio
import logging
import math
import threading
from typing import Any, Dict, Optional

from unilabos.registry.decorators import action, device, not_action
from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode

# 5 个货架角度（弧度），从正前方逆时针，间隔 72°
_SLOT_ANGLES = {i: math.radians((i - 1) * 72) for i in range(1, 6)}
_TRANSFER_ANGLE = 0.0  # 出口/传输窗口角度
_CAROUSEL_SPEED = 0.8  # rad/s
_CAROUSEL_JOINT = "0_carousel_joint"


@device(
    id="virtual_liconic_stx110",
    display_name="STX110 转盘孵育器(仿真/RViz)",
    category=["virtual_device"],
    description="LiCONiC STX110 carousel incubator simulation driving the RViz carousel joint.",
    driver_runtime_kind="virtual",
    virtual_driver_kind="engine_adapter",
    sim_engine="rviz",
)
class VirtualLiconicStx110:
    """STX110 孵育器仿真：在 RViz 中驱动转盘旋转，模拟板的存取。"""

    _ros_node: BaseROS2DeviceNode

    def __init__(
        self,
        device_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        初始化虚拟 STX110。

        Args:
            device_id[设备ID]: 设备实例 ID，默认 liconic_stx110，须与 URDF 关节前缀一致。
            config[设备配置]: 可含 target_temperature。
        """
        if device_id is None and "id" in kwargs:
            device_id = kwargs.pop("id")
        if config is None and "config" in kwargs:
            config = kwargs.pop("config")

        self.device_id = device_id or "liconic_stx110"
        self.config = config or {}
        self.logger = logging.getLogger(f"VirtualLiconicStx110.{self.device_id}")
        self.data: Dict[str, Any] = {}

        self._target_temperature = float(self.config.get("target_temperature", 37.0))
        self._current_temperature = 22.0
        self._carousel_angle = _TRANSFER_ANGLE

        self._publisher = None
        self._executor = None
        self._executor_thread = None

        for key, value in kwargs.items():
            if not hasattr(self, key):
                setattr(self, key, value)

        self.logger.info(f"=== 虚拟 STX110 孵育器 {self.device_id} 已创建 ===")

    @not_action
    def post_init(self, ros_node: BaseROS2DeviceNode):
        self._ros_node = ros_node

    @not_action
    def _ensure_publisher(self) -> None:
        """懒加载关节发布器（整机内设备节点不保证自动调 initialize）。"""
        if self._publisher is not None:
            return
        import rclpy

        from unilabos.devices.ros_dev.simple_joint_publisher_node import SimpleJointPublisher

        if not rclpy.ok():
            rclpy.init()
        # 独立节点名，避免与设备 ROS 节点（名为 device_id）重名
        self._publisher = SimpleJointPublisher(
            device_id=self.device_id,
            joint_names=[_CAROUSEL_JOINT],
            rate=50,
            node_name=f"{self.device_id}_carousel_pub",
        )
        self._executor = rclpy.executors.MultiThreadedExecutor()
        self._executor.add_node(self._publisher)
        self._executor_thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._executor_thread.start()
        self._publisher.set_immediate({_CAROUSEL_JOINT: self._carousel_angle})
        self.logger.info(f"STX110 {self.device_id} 关节发布器已启动。")

    @not_action
    def initialize(self) -> bool:
        """启动关节发布器并复位转盘到出口位置。"""
        self._carousel_angle = _TRANSFER_ANGLE
        self._ensure_publisher()
        self.data.update(
            {
                "status": "Ready",
                "carousel_angle": 0.0,
                "slot": None,
                "temperature": self._current_temperature,
            }
        )
        self.logger.info(f"STX110 {self.device_id} 初始化完成，转盘在出口位置。")
        return True

    @not_action
    def cleanup(self) -> bool:
        if self._executor and self._publisher:
            self._executor.remove_node(self._publisher)
        if self._executor:
            self._executor.shutdown()
        self.data.update({"status": "Offline"})
        self.logger.info(f"STX110 {self.device_id} 已清理。")
        return True

    async def _rotate_to(self, angle: float) -> None:
        # move_to 为阻塞插值循环；整机 action 执行上下文无运行中的 asyncio loop，直接阻塞调用。
        self._ensure_publisher()
        self._publisher.move_to({_CAROUSEL_JOINT: angle}, _CAROUSEL_SPEED)
        self._carousel_angle = angle
        self.data["carousel_angle"] = round(math.degrees(angle), 1)

    @action(description="存板：转盘旋转使指定货架对准传输窗口")
    async def load_plate(self, slot: int = 1) -> bool:
        """
        将转盘旋转使 slot 对准传输窗口，模拟放板。

        Args:
            slot[货架编号]: 目标货架 1–5。
        """
        slot = int(slot)
        if slot not in _SLOT_ANGLES:
            self.data.update({"status": f"Error: invalid slot {slot}"})
            return False
        angle = _SLOT_ANGLES[slot]
        self.data.update({"status": f"Loading slot {slot}", "slot": slot})
        self.logger.info(f"转盘 → 货架 {slot} ({math.degrees(angle):.0f}°)")
        await self._rotate_to(angle)
        self.data.update({"status": f"Slot {slot} aligned"})
        return True

    @action(description="取板：转盘旋转到指定货架取出后回到出口")
    async def unload_plate(self, slot: int = 1) -> bool:
        """
        旋转转盘取出指定 slot 的板，随后回到出口。

        Args:
            slot[货架编号]: 目标货架 1–5。
        """
        slot = int(slot)
        if slot not in _SLOT_ANGLES:
            self.data.update({"status": f"Error: invalid slot {slot}"})
            return False
        angle = _SLOT_ANGLES[slot]
        self.data.update({"status": f"Unloading slot {slot}", "slot": slot})
        self.logger.info(f"转盘 → 货架 {slot} 取板 ({math.degrees(angle):.0f}°)")
        await self._rotate_to(angle)
        await self._rotate_to(_TRANSFER_ANGLE)
        self.data.update({"status": "At transfer", "slot": None})
        return True

    @action(description="设置孵育温度（仿真，无运动）")
    async def set_temperature(self, temperature: float = 37.0) -> bool:
        """
        设置目标孵育温度。

        Args:
            temperature[目标温度]: 摄氏度。
        """
        self._target_temperature = float(temperature)
        self.data.update({"temperature": self._target_temperature})
        self.logger.info(f"设置温度 → {temperature}°C（仿真）")
        return True
