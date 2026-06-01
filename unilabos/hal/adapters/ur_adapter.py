from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional

from unilabos.hal.base import JointState, RobotHAL
from unilabos.queries.models import Pose


def _rotvec_to_quat_xyzw(rotvec: List[float]) -> List[float]:
    angle = math.sqrt(sum(float(item) * float(item) for item in rotvec))
    if angle == 0.0:
        return [0.0, 0.0, 0.0, 1.0]
    axis = [float(item) / angle for item in rotvec]
    half = angle / 2.0
    scale = math.sin(half)
    return [axis[0] * scale, axis[1] * scale, axis[2] * scale, math.cos(half)]


def _quat_xyzw_to_rotvec(quat_xyzw: List[float]) -> List[float]:
    x, y, z, w = [float(item) for item in quat_xyzw]
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm == 0.0:
        return [0.0, 0.0, 0.0]
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    angle = 2.0 * math.atan2(math.sqrt(x * x + y * y + z * z), w)
    sin_half = math.sin(angle / 2.0)
    if abs(sin_half) < 1e-12:
        return [0.0, 0.0, 0.0]
    axis = [x / sin_half, y / sin_half, z / sin_half]
    return [axis[0] * angle, axis[1] * angle, axis[2] * angle]


def _pose_to_ur_tcp(pose: Pose) -> List[float]:
    return [float(pose.xyz[0]), float(pose.xyz[1]), float(pose.xyz[2]), *_quat_xyzw_to_rotvec(pose.quat_xyzw)]


def _pose_from_ur_tcp(tcp_pose: List[float], frame: str, source: str, metadata: Optional[dict[str, Any]] = None) -> Pose:
    values = [float(item) for item in tcp_pose]
    if len(values) < 6:
        raise ValueError(f"UR TCP pose must contain 6 values, got {len(values)}")
    return Pose(
        xyz=values[:3],
        quat_xyzw=_rotvec_to_quat_xyzw(values[3:6]),
        frame_id=frame,
        source=source,
        metadata=dict(metadata or {}),
    )


@dataclass
class URHAL(RobotHAL):
    """Universal Robots RTDE HAL.

    In real mode this adapter wraps `ur_rtde`. Tests can inject fake RTDE
    objects. In sim mode it talks to a Phase 1-style physics backend through
    `get_observation` and `set_command`.
    """

    host: str
    robot_id: str = "ur"
    mode: str = "real"
    rtde_control: Any = None
    rtde_receive: Any = None
    gripper: Any = None
    sim_backend: Any = None
    acceleration: float = 0.5

    def __post_init__(self) -> None:
        if self.mode not in ("real", "sim"):
            raise ValueError(f"Unsupported URHAL mode: {self.mode}")
        if self.mode == "real" and (self.rtde_control is None or self.rtde_receive is None):
            try:
                from rtde_control import RTDEControlInterface
                from rtde_receive import RTDEReceiveInterface
            except ImportError as exc:
                raise RuntimeError("ur_rtde is required for real URHAL connections") from exc
            self.rtde_control = self.rtde_control or RTDEControlInterface(self.host)
            self.rtde_receive = self.rtde_receive or RTDEReceiveInterface(self.host)

    def get_pose(self, frame: str = "tool0") -> Pose:
        if self.mode == "sim":
            observation = self._sim_observation()
            tcp_pose = observation.get("tcp_pose") or observation.get("tool_pose")
            if tcp_pose is None:
                raise KeyError(f"Sim observation for {self.robot_id} does not include tcp_pose")
            return _pose_from_ur_tcp(tcp_pose, frame=frame, source="ur_hal_sim", metadata={"robot_id": self.robot_id})
        return _pose_from_ur_tcp(
            list(self.rtde_receive.getActualTCPPose()),
            frame=frame,
            source="ur_hal_rtde",
            metadata={"robot_id": self.robot_id, "host": self.host},
        )

    def get_joint_state(self) -> JointState:
        if self.mode == "sim":
            observation = self._sim_observation()
            positions = list(observation.get("joint_positions") or observation.get("q") or [])
            velocities = list(observation.get("joint_velocities") or observation.get("qd") or [0.0] * len(positions))
        else:
            positions = list(self.rtde_receive.getActualQ())
            get_qd = getattr(self.rtde_receive, "getActualQd", None)
            velocities = list(get_qd()) if get_qd is not None else [0.0] * len(positions)
        return JointState(
            names=[f"joint_{index + 1}" for index in range(len(positions))],
            positions=[float(item) for item in positions],
            velocities=[float(item) for item in velocities],
        )

    def move_l(self, pose: Pose, speed: float = 0.1) -> None:
        tcp_pose = _pose_to_ur_tcp(pose)
        if self.mode == "sim":
            self._sim_command({"type": "move_l", "tcp_pose": tcp_pose, "speed": float(speed)})
            return
        self.rtde_control.moveL(tcp_pose, float(speed), float(self.acceleration))

    def move_j(self, joints: List[float], speed: float = 0.5) -> None:
        values = [float(item) for item in joints]
        if self.mode == "sim":
            self._sim_command({"type": "move_j", "joint_positions": values, "speed": float(speed)})
            return
        self.rtde_control.moveJ(values, float(speed), float(self.acceleration))

    def open_gripper(self) -> None:
        if self.mode == "sim":
            self._sim_command({"type": "gripper", "state": "open"})
            return
        if self.gripper is None:
            raise RuntimeError("URHAL gripper is not configured")
        self.gripper.open()

    def close_gripper(self) -> None:
        if self.mode == "sim":
            self._sim_command({"type": "gripper", "state": "closed"})
            return
        if self.gripper is None:
            raise RuntimeError("URHAL gripper is not configured")
        self.gripper.close()

    def _sim_observation(self) -> dict[str, Any]:
        if self.sim_backend is None:
            raise RuntimeError("URHAL sim mode requires sim_backend")
        return dict(self.sim_backend.get_observation(self.robot_id))

    def _sim_command(self, command: dict[str, Any]) -> None:
        if self.sim_backend is None:
            raise RuntimeError("URHAL sim mode requires sim_backend")
        self.sim_backend.set_command(self.robot_id, command)
