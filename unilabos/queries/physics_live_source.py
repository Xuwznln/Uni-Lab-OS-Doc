from __future__ import annotations

import math
from typing import Any, Optional

from unilabos.queries.models import ActionSchema, Pose, QueryAffordance, SafetyZone, State, utc_timestamp


def _rotvec_to_quat_xyzw(rotvec: list[float]) -> list[float]:
    angle = math.sqrt(sum(float(item) * float(item) for item in rotvec))
    if angle == 0.0:
        return [0.0, 0.0, 0.0, 1.0]
    axis = [float(item) / angle for item in rotvec]
    half = angle / 2.0
    scale = math.sin(half)
    return [axis[0] * scale, axis[1] * scale, axis[2] * scale, math.cos(half)]


class PhysicsLiveSource:
    name = "physics_live"

    def __init__(self, physics_backend: Any):
        self.physics_backend = physics_backend

    @property
    def _source_name(self) -> str:
        return f"physics_live:{getattr(self.physics_backend, 'name', 'unknown')}"

    def _observation(self, target: str) -> Optional[dict[str, Any]]:
        try:
            return dict(self.physics_backend.get_observation(target))
        except Exception:
            return None

    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        obs = self._observation(target)
        if not obs:
            return None
        pose_payload = obs.get("pose")
        if isinstance(pose_payload, dict):
            frame_id = str(pose_payload.get("frame_id") or obs.get("frame_id") or "world")
            if frame is not None and frame_id != frame:
                return None
            return Pose(
                xyz=[float(item) for item in pose_payload.get("xyz", [0.0, 0.0, 0.0])],
                quat_xyzw=[float(item) for item in pose_payload.get("quat_xyzw", [0.0, 0.0, 0.0, 1.0])],
                frame_id=frame_id,
                stamp=utc_timestamp(),
                source=self._source_name,
                metadata={"target": target},
            )
        tcp_pose = obs.get("tcp_pose") or obs.get("tool_pose")
        if tcp_pose is None:
            return None
        values = [float(item) for item in tcp_pose]
        if len(values) < 6:
            return None
        frame_id = str(obs.get("frame_id") or "world")
        if frame is not None and frame_id != frame:
            return None
        return Pose(
            xyz=values[:3],
            quat_xyzw=_rotvec_to_quat_xyzw(values[3:6]),
            frame_id=frame_id,
            stamp=utc_timestamp(),
            source=self._source_name,
            metadata={"target": target, "pose_format": "tcp_pose"},
        )

    def query_state(self, target: str) -> Optional[State]:
        obs = self._observation(target)
        if not obs:
            return None
        return State(name=target, values=obs, stamp=utc_timestamp(), source=self._source_name)

    def query_affordance(self, target: str, kind: Optional[str] = None) -> list[QueryAffordance]:
        return []

    def query_action_schema(self, action: str) -> Optional[ActionSchema]:
        return None

    def query_safety_zones(self) -> list[SafetyZone]:
        return []
