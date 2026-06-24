"""
SimpleJointPublisher — 通用单设备关节状态发布节点

供 rviz_backend 系列仿真驱动使用。
相比 LiquidHandlerJointPublisher，本节点功能更简单：
- 不做逆运动学（IK），直接接受关节角度/位移目标值
- 支持线性插值平滑运动（与液体处理站节点相同策略）
- 不依赖 TfUpdate Action Server（适合独立设备仿真）

用法示例：
    publisher = SimpleJointPublisher(
        device_id="bmg_labtech_clariostar_plus",
        joint_names=["tray_joint"],
        rate=50,
    )
    publisher.move_to({"tray_joint": 0.08}, speed=0.02)
"""

import copy
import threading
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState


class SimpleJointPublisher(Node):
    """
    通用关节状态发布器。

    Parameters
    ----------
    device_id : str
        设备 ID，也作为 ROS 节点名和关节名称前缀。
    joint_names : list[str]
        关节名称列表（不含前缀），例如 ["tray_joint", "lid_joint"]。
    rate : int
        发布频率（Hz），默认 50。
    """

    def __init__(self, device_id: str, joint_names: list[str], rate: int = 50):
        super().__init__(device_id)

        self.device_id = device_id
        self.rate = rate
        self._lock = threading.Lock()

        prefixed = [f"{device_id}_{name}" for name in joint_names]
        self._j_msg = JointState(
            name=prefixed,
            position=[0.0] * len(joint_names),
            velocity=[0.0] * len(joint_names),
            effort=[0.0] * len(joint_names),
        )
        self._joint_index = {name: i for i, name in enumerate(joint_names)}

        self._pub = self.create_publisher(JointState, "/joint_states", 10)
        self.create_timer(1.0 / rate, self._publish_callback)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def move_to(self, targets: dict[str, float], speed: float = 0.05) -> None:
        """
        将指定关节平滑插值运动到目标位置。

        Parameters
        ----------
        targets : dict[str, float]
            {关节名: 目标位置（弧度 or 米）}，关节名不含设备前缀。
        speed : float
            每帧最大步长（弧度/米），默认 0.05。
        """
        target_positions = copy.deepcopy(self._j_msg.position)
        for name, value in targets.items():
            idx = self._joint_index.get(name)
            if idx is None:
                self.get_logger().warn(f"Unknown joint: {name}")
                continue
            target_positions[idx] = value

        self._interpolate_to(target_positions, speed)

    def set_immediate(self, targets: dict[str, float]) -> None:
        """直接跳变到目标位置，不做插值（用于初始化/复位）。"""
        with self._lock:
            for name, value in targets.items():
                idx = self._joint_index.get(name)
                if idx is not None:
                    self._j_msg.position[idx] = value

    def get_position(self, joint_name: str) -> float:
        """获取指定关节当前位置。"""
        idx = self._joint_index.get(joint_name)
        if idx is None:
            raise KeyError(f"Unknown joint: {joint_name}")
        return self._j_msg.position[idx]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _interpolate_to(self, target: list[float], speed: float) -> None:
        """线性插值运动循环，阻塞直到到达目标。"""
        dt = 1.0 / self.rate
        while True:
            done = 0
            with self._lock:
                current = list(self._j_msg.position)
                for i, tgt in enumerate(target):
                    dist = tgt - current[i]
                    if abs(dist) <= speed * dt:
                        self._j_msg.position[i] = tgt
                        done += 1
                    else:
                        self._j_msg.position[i] += (dist / abs(dist)) * speed * dt
            if done == len(target):
                break
            time.sleep(dt)

    def _publish_callback(self) -> None:
        with self._lock:
            self._j_msg.header.stamp = self.get_clock().now().to_msg()
            self._pub.publish(self._j_msg)
