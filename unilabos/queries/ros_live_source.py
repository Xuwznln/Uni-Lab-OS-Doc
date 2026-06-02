"""Live edge query source: bridges the Phase 1a edge runtime into the Phase 3 query API.

The other ``QuerySource`` implementations read *static* scene data (LSD / URDF /
LabUtopia / resource maps). This source instead holds a small in-memory cache that
is fed from the running edge: ROS2 ``/joint_states`` (robot motion), an optional
pose topic, and any direct ``update_pose`` / ``update_state`` calls (e.g. from the
``ResourceMeshManager`` ``/resource_pose`` callback or the TwinBridge).

Registered FIRST in a ``QueryEngine``, it makes ``query_pose`` / ``query_state``
return the real-time edge/sim world; when a target is not live-cached it returns
``None`` and the engine falls through to the static sources.

The cache + update API is dependency-free (unit-testable without ROS2). rclpy /
message types are imported lazily inside ``attach_ros``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from unilabos.queries.models import ActionSchema, Pose, QueryAffordance, SafetyZone, State, utc_timestamp


class RosLiveSource:
    name = "ros_live"

    def __init__(self, max_age_s: Optional[float] = None):
        # name -> (model, monotonic_ts)
        self._poses: Dict[str, Tuple[Pose, float]] = {}
        self._states: Dict[str, Tuple[State, float]] = {}
        self.max_age_s = max_age_s
        self._node = None
        self._subs: List[Any] = []

    # ------------------------------------------------------------------ feed API
    def update_pose(
        self,
        name: str,
        xyz: List[float],
        quat_xyzw: Optional[List[float]] = None,
        frame_id: str = "lab_world",
        source: str = "ros_live",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        pose = Pose(
            xyz=list(xyz),
            quat_xyzw=list(quat_xyzw) if quat_xyzw is not None else [0.0, 0.0, 0.0, 1.0],
            frame_id=frame_id,
            stamp=utc_timestamp(),
            source=source,
            metadata=dict(metadata or {}),
        )
        self._poses[name] = (pose, time.monotonic())

    def update_state(self, name: str, values: Dict[str, Any], source: str = "ros_live") -> None:
        state = State(name=name, values=dict(values), stamp=utc_timestamp(), source=source)
        self._states[name] = (state, time.monotonic())

    def _fresh(self, ts: float) -> bool:
        return self.max_age_s is None or (time.monotonic() - ts) <= self.max_age_s

    # -------------------------------------------------------------- QuerySource
    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        entry = self._poses.get(target)
        if entry is None or not self._fresh(entry[1]):
            return None
        pose = entry[0]
        if frame is not None and pose.frame_id != frame:
            # frame mismatch: live source does not transform; let static sources try
            return None
        return pose

    def query_state(self, target: str) -> Optional[State]:
        entry = self._states.get(target)
        if entry is None or not self._fresh(entry[1]):
            return None
        return entry[0]

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        # affordances are static scene knowledge; live source contributes none
        return []

    def query_action_schema(self, action: str) -> Optional[ActionSchema]:
        return None

    def query_safety_zones(self) -> List[SafetyZone]:
        return []

    # --------------------------------------------------------------- ROS wiring
    def attach_ros(
        self,
        node: Any,
        joint_states_topic: str = "/joint_states",
        pose_topic: Optional[str] = None,
    ) -> None:
        """Subscribe to edge topics. rclpy/message types imported lazily.

        - ``joint_states_topic`` (sensor_msgs/JointState) -> cached as State per
          device name (msg.name[0] prefix or the topic).
        - ``pose_topic`` (geometry_msgs/PoseStamped, optional) -> cached pose
          keyed by ``frame_id``.
        """
        from sensor_msgs.msg import JointState

        self._node = node
        self._subs.append(
            node.create_subscription(JointState, joint_states_topic, self._on_joint_states, 10)
        )
        if pose_topic is not None:
            from geometry_msgs.msg import PoseStamped

            self._subs.append(
                node.create_subscription(PoseStamped, pose_topic, self._on_pose_stamped, 10)
            )

    def _on_joint_states(self, msg: Any) -> None:
        names = list(getattr(msg, "name", []) or [])
        positions = list(getattr(msg, "position", []) or [])
        velocities = list(getattr(msg, "velocity", []) or [])
        # key by header frame_id if present else a generic name
        key = getattr(getattr(msg, "header", None), "frame_id", "") or "joint_states"
        self.update_state(
            key,
            {"joint_names": names, "positions": positions, "velocities": velocities},
            source="ros_live:/joint_states",
        )
        # also index individual joints for convenience
        for jname, pos in zip(names, positions):
            self.update_state(jname, {"position": pos}, source="ros_live:/joint_states")

    def _on_pose_stamped(self, msg: Any) -> None:
        pos = msg.pose.position
        ori = msg.pose.orientation
        frame_id = getattr(msg.header, "frame_id", "lab_world") or "lab_world"
        self.update_pose(
            frame_id,
            [pos.x, pos.y, pos.z],
            [ori.x, ori.y, ori.z, ori.w],
            frame_id=frame_id,
            source="ros_live:pose_topic",
        )

    def detach(self) -> None:
        if self._node is not None:
            for sub in self._subs:
                try:
                    self._node.destroy_subscription(sub)
                except Exception:  # noqa: BLE001
                    pass
        self._subs = []
        self._node = None


def build_live_query_engine(
    node: Any = None,
    static_sources: Optional[List[Any]] = None,
    hal_registry: Any = None,
    attach: bool = True,
    joint_states_topic: str = "/joint_states",
    pose_topic: Optional[str] = None,
) -> Tuple["RosLiveSource", Any]:
    """Build a QueryEngine whose live edge source takes priority over static ones.

    Returns ``(live_source, engine)``. When ``node`` is given and ``attach`` is
    True, the live source subscribes to the edge topics immediately.
    """
    from unilabos.queries.engine import QueryEngine

    live = RosLiveSource()
    sources: List[Any] = [live]
    sources.extend(static_sources or [])
    engine = QueryEngine(sources=sources, hal_registry=hal_registry)
    if node is not None and attach:
        live.attach_ros(node, joint_states_topic=joint_states_topic, pose_topic=pose_topic)
    return live, engine
