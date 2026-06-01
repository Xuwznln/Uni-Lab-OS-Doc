from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.request import urlopen

from unilabos.hal.base import JointState, RobotHAL
from unilabos.queries.models import Pose


DEFAULT_FEETECH_JOINTS = ["base", "s1", "s2", "e1", "e2", "wrist_p", "wrist_r", "gripper"]
DEFAULT_FEETECH_CENTERS = {
    "base": 2048,
    "s1": 2048,
    "s2": 2048,
    "e1": 2048,
    "e2": 2048,
    "wrist_p": 2048,
    "wrist_r": 2048,
    "gripper": 2000,
}


def ticks_to_radians(ticks: float, center: float = 2048.0, ticks_per_rev: float = 4096.0) -> float:
    return (float(ticks) - float(center)) * 2.0 * math.pi / float(ticks_per_rev)


@dataclass
class FeetechRoboArmHAL(RobotHAL):
    """Read-only HAL for the local Feetech/RDK chemistry roboarm.

    The adapter consumes the JSON endpoint exposed by
    `teleop_leader_server.py`. It intentionally exposes only state reads by
    default; motion methods raise unless a future motion-capable adapter is
    implemented behind explicit safety gates.
    """

    endpoint_url: str
    robot_id: str = "roboarm_leader"
    joint_names: List[str] = field(default_factory=lambda: list(DEFAULT_FEETECH_JOINTS))
    centers: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FEETECH_CENTERS))
    ticks_per_rev: float = 4096.0
    timeout_s: float = 0.5
    fixed_pose: Optional[Pose] = None
    payload_provider: Optional[Callable[[], Dict[str, Any]]] = field(default=None, repr=False)
    _last_payload: Optional[Dict[str, Any]] = field(default=None, init=False, repr=False)

    def get_pose(self, frame: str = "tool0") -> Pose:
        if self.fixed_pose is None:
            raise NotImplementedError(
                "FeetechRoboArmHAL needs calibrated URDF/FK before Cartesian pose queries"
            )
        return Pose(
            xyz=list(self.fixed_pose.xyz),
            quat_xyzw=list(self.fixed_pose.quat_xyzw),
            frame_id=frame,
            source="feetech_roboarm_hal",
            metadata={**dict(self.fixed_pose.metadata), "robot_id": self.robot_id},
        )

    def get_joint_state(self) -> JointState:
        payload = self.read_state()
        positions = payload.get("position") or payload.get("positions")
        if not isinstance(positions, dict):
            raise RuntimeError("Feetech state payload has no position dict")

        speed = payload.get("speed") or {}
        current = payload.get("current") or {}

        names: List[str] = []
        joint_positions: List[float] = []
        joint_velocities: List[float] = []
        joint_efforts: List[float] = []
        for joint in self.joint_names:
            if joint not in positions:
                continue
            names.append(joint)
            joint_positions.append(
                ticks_to_radians(
                    positions[joint],
                    center=self.centers.get(joint, 2048.0),
                    ticks_per_rev=self.ticks_per_rev,
                )
            )
            joint_velocities.append(float(speed.get(joint, 0.0)))
            joint_efforts.append(float(current.get(joint, 0.0)))

        if not names:
            raise RuntimeError("Feetech state payload did not include any configured joints")

        return JointState(names=names, positions=joint_positions, velocities=joint_velocities, efforts=joint_efforts)

    def get_state_values(self) -> Dict[str, Any]:
        payload = self._last_payload or self.read_state()
        positions = payload.get("position") or payload.get("positions") or {}
        received_at = payload.get("_received_at_epoch")
        return {
            "robot_id": self.robot_id,
            "endpoint_url": self.endpoint_url,
            "role": payload.get("role"),
            "robot_type": payload.get("robot_type"),
            "sample_count": payload.get("sample_count"),
            "leader_timestamp": payload.get("timestamp"),
            "leader_monotonic": payload.get("monotonic"),
            "received_at_epoch": received_at,
            "age_ms": None if received_at is None else round((time.time() - float(received_at)) * 1000.0, 3),
            "torque_enabled": payload.get("torque_enabled"),
            "raw_position_ticks": {str(key): int(value) for key, value in positions.items()},
            "raw_speed": dict(payload.get("speed") or {}),
            "raw_current": dict(payload.get("current") or {}),
            "motion_enabled": False,
        }

    def read_state(self) -> Dict[str, Any]:
        if self.payload_provider is not None:
            payload = dict(self.payload_provider())
        else:
            with urlopen(self.endpoint_url, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))

        if not payload.get("ok", False):
            raise RuntimeError(payload.get("error", "Feetech endpoint returned ok=false"))
        payload["_received_at_epoch"] = time.time()
        self._last_payload = payload
        return payload

    def move_l(self, pose: Pose, speed: float = 0.1) -> None:
        self._raise_read_only("move_l")

    def move_j(self, joints: List[float], speed: float = 0.5) -> None:
        self._raise_read_only("move_j")

    def open_gripper(self) -> None:
        self._raise_read_only("open_gripper")

    def close_gripper(self) -> None:
        self._raise_read_only("close_gripper")

    def _raise_read_only(self, method: str) -> None:
        raise RuntimeError(
            f"{type(self).__name__}.{method} is read-only in this validation adapter; "
            "use an explicit motion adapter with safety gates for actuation"
        )
