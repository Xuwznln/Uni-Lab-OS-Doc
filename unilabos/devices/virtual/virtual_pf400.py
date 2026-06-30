"""
VirtualPf400 — PreciseFlex PF400 机械臂仿真驱动（RViz 引擎适配，Plan 21）。

08v2 sim 替换的虚拟驱动：sim 模式下替换真实设备 robotic_arm_pf400。
通过 SimpleJointPublisher 发布 /joint_states 驱动 RViz 中机械臂 5 个独立关节运动，
右指（5_grasp_right_joint）由 robot_state_publisher 按 URDF <mimic> 自动镜像。

关节语义名对齐 PyLabRobot PFAxis：
  base(BASE) shoulder(SHOULDER) elbow(ELBOW) wrist(WRIST) gripper(GRIPPER)
局部 URDF 关节名由 registry model.joints 提供（Plan 20 关节契约单一真源），
本驱动只引用语义名；registry 不可用时回退 _DEFAULT_JOINTS。

自标记（M-8 / 08v2）：
  driver_runtime_kind = virtual
  virtual_driver_kind = engine_adapter
  sim_engine          = rviz
"""

import logging
import threading
from typing import Any, Dict, Optional

from unilabos.registry.decorators import action, device, not_action
from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode

# 5 个独立可动关节的语义名（与 PLR PFAxis 对齐；右指为 mimic，不在此列）
_JOINT_SEMANTICS = ["base", "shoulder", "elbow", "wrist", "gripper"]

# Plan 20：registry 不可用时的兜底映射（唯一 fallback，非业务逻辑重复）
_DEFAULT_JOINTS = {
    "base": "0_shoulder_elevation_joint",
    "shoulder": "1_shoulder_joint",
    "elbow": "2_elbow_joint",
    "wrist": "3_wrist_joint",
    "gripper": "4_grasp_left_joint",
}

# URDF <limit> 限位（用于夹紧，避免 RViz 报超限）
_LIMITS = {
    "base": (0.0, 0.4),
    "shoulder": (-1.5708, 1.5708),
    "elbow": (0.21, 6.07),
    "wrist": (-16.9297, 16.9297),
    "gripper": (0.0345, 0.067),
}

# 复位/初始安全姿态
_HOME = {"base": 0.1, "shoulder": 0.0, "elbow": 1.0, "wrist": 0.0, "gripper": 0.0345}
_JOINT_SPEED = 0.6  # rad(or m)/s 插值步长基准


def _clamp(name: str, value: float) -> float:
    lo, hi = _LIMITS.get(name, (None, None))
    if lo is None:
        return value
    return max(lo, min(hi, value))


@device(
    id="virtual_pf400",
    display_name="PF400 机械臂(仿真/RViz)",
    category=["virtual_device"],
    description="PreciseFlex PF400 arm simulation driving RViz joints via SimpleJointPublisher.",
    driver_runtime_kind="virtual",
    virtual_driver_kind="engine_adapter",
    sim_engine="rviz",
)
class VirtualPf400:
    """PF400 机械臂仿真：在 RViz 中驱动 5 个关节运动，右指由 URDF mimic 镜像。"""

    _ros_node: BaseROS2DeviceNode

    def __init__(
        self,
        device_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        初始化虚拟 PF400。

        Args:
            device_id[设备ID]: 设备实例 ID，须与 URDF 关节前缀一致。
            config[设备配置]: 预留。
        """
        if device_id is None and "id" in kwargs:
            device_id = kwargs.pop("id")
        if config is None and "config" in kwargs:
            config = kwargs.pop("config")

        self.device_id = device_id or "robotic_arm_pf400"
        self.config = config or {}
        self.logger = logging.getLogger(f"VirtualPf400.{self.device_id}")
        self.data: Dict[str, Any] = {}

        self._positions = dict(_HOME)

        self._publisher = None
        self._executor = None
        self._executor_thread = None

        for key, value in kwargs.items():
            if not hasattr(self, key):
                setattr(self, key, value)

        self.logger.info(f"=== 虚拟 PF400 机械臂 {self.device_id} 已创建 ===")

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
        # 用真实 ROS 节点 id 作关节前缀（否则与装配 URDF 关节名不符 -> 臂不动）
        did = getattr(getattr(self, "_ros_node", None), "device_id", None) or self.device_id
        self.device_id = did
        # Plan 20：语义名→局部名映射，registry(model.joints) 为权威源，缺省回退默认。
        joint_map = dict(_DEFAULT_JOINTS)
        try:
            from unilabos.device_mesh.joint_contract import get_joint_map

            reg_jm = get_joint_map(did)  # 本部署 did==真实模板 class robotic_arm_pf400
            if reg_jm:
                joint_map = reg_jm
        except Exception as e:
            self.logger.warning(f"读取 registry joints 失败,回退默认关节映射: {e}")
        self._publisher = SimpleJointPublisher(
            device_id=did,
            joint_names=list(_JOINT_SEMANTICS),
            rate=50,
            node_name=f"{did}_arm_pub",
            joint_map=joint_map,
        )
        self._executor = rclpy.executors.MultiThreadedExecutor()
        self._executor.add_node(self._publisher)
        self._executor_thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._executor_thread.start()
        self._publisher.set_immediate(dict(self._positions))
        self.logger.info(f"PF400 {self.device_id} 关节发布器已启动(关节映射={joint_map})。")

    @not_action
    def initialize(self) -> bool:
        """启动关节发布器并复位到安全姿态。"""
        self._positions = dict(_HOME)
        self._ensure_publisher()
        self.data.update({"status": "Ready", **self._positions})
        self.logger.info(f"PF400 {self.device_id} 初始化完成，已在安全姿态。")
        return True

    @not_action
    def cleanup(self) -> bool:
        if self._executor and self._publisher:
            self._executor.remove_node(self._publisher)
        if self._executor:
            self._executor.shutdown()
        self.data.update({"status": "Offline"})
        self.logger.info(f"PF400 {self.device_id} 已清理。")
        return True

    @not_action
    def _apply(self, targets: Dict[str, float]) -> None:
        self._ensure_publisher()
        clamped = {k: _clamp(k, float(v)) for k, v in targets.items() if k in _JOINT_SEMANTICS}
        self._publisher.move_to(clamped, _JOINT_SPEED)
        self._positions.update(clamped)
        self.data.update(self._positions)

    @action(description="关节角运动：按语义名设置各关节目标值（在 URDF 限位内）")
    async def move_joints(
        self,
        base: float = 0.1,
        shoulder: float = 0.0,
        elbow: float = 1.0,
        wrist: float = 0.0,
        gripper: float = 0.0345,
    ) -> bool:
        """
        将 5 个关节平滑运动到目标值（超出限位会被夹紧）。

        Args:
            base[升降]: 0~0.4 m。
            shoulder[肩]: -1.5708~1.5708 rad。
            elbow[肘]: 0.21~6.07 rad。
            wrist[腕]: -16.93~16.93 rad。
            gripper[夹爪开度]: 0.0345~0.067 m（右指 mimic 镜像）。
        """
        targets = {
            "base": base,
            "shoulder": shoulder,
            "elbow": elbow,
            "wrist": wrist,
            "gripper": gripper,
        }
        self.data.update({"status": "Moving"})
        self.logger.info(f"PF400 关节运动 → {targets}")
        self._apply(targets)
        self.data.update({"status": "Idle"})
        return True

    @action(description="设置夹爪开度（右指由 mimic 自动镜像）")
    async def set_gripper(self, width: float = 0.05) -> bool:
        """
        设置夹爪开度。

        Args:
            width[夹爪开度]: 0.0345~0.067 m。
        """
        self.logger.info(f"PF400 夹爪 → {width} m")
        self._apply({"gripper": float(width)})
        return True
