from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from unilabos.hal.base import JointState, RobotHAL
from unilabos.queries.models import Pose


@dataclass
class MockHAL(RobotHAL):
    robot_id: str = "mock_robot"
    pose: Pose = field(default_factory=lambda: Pose(xyz=[0.0, 0.0, 0.0], source="mock_hal"))
    joint_state: JointState = field(
        default_factory=lambda: JointState(
            names=["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6"],
            positions=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            velocities=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        )
    )
    gripper_open: bool = True
    calls: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)

    def get_pose(self, frame: str = "tool0") -> Pose:
        self.calls.append(("get_pose", {"frame": frame}))
        return Pose(
            xyz=list(self.pose.xyz),
            quat_xyzw=list(self.pose.quat_xyzw),
            frame_id=frame,
            source="mock_hal",
            metadata={**dict(self.pose.metadata), "robot_id": self.robot_id},
        )

    def get_joint_state(self) -> JointState:
        self.calls.append(("get_joint_state", {}))
        return self.joint_state

    def move_l(self, pose: Pose, speed: float = 0.1) -> None:
        self.calls.append(("move_l", {"pose": pose.to_dict(), "speed": speed}))
        self.pose = pose

    def move_j(self, joints: List[float], speed: float = 0.5) -> None:
        self.calls.append(("move_j", {"joints": list(joints), "speed": speed}))
        self.joint_state = JointState(
            names=list(self.joint_state.names),
            positions=list(joints),
            velocities=list(self.joint_state.velocities),
            efforts=list(self.joint_state.efforts),
        )

    def open_gripper(self) -> None:
        self.calls.append(("open_gripper", {}))
        self.gripper_open = True

    def close_gripper(self) -> None:
        self.calls.append(("close_gripper", {}))
        self.gripper_open = False

    def skill(self, name: str, **kwargs) -> Any:
        self.calls.append(("skill", {"name": name, **kwargs}))
        return {"ok": True, "skill": name, "kwargs": kwargs}
