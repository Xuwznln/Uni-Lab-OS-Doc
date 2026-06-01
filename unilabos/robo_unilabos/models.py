from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


LAB_WORLD_FRAME = "lab_world"


def _mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _xyz(value: Any) -> Dict[str, float]:
    data = _mapping(value)
    return {
        "x": _number(data.get("x")),
        "y": _number(data.get("y")),
        "z": _number(data.get("z")),
    }


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Pose3D:
    """A resource pose in a named coordinate frame."""

    frame_id: str = LAB_WORLD_FRAME
    position: Dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0})
    orientation_rpy: Dict[str, float] = field(default_factory=lambda: {"roll": 0.0, "pitch": 0.0, "yaw": 0.0})
    unit: str = "mm"

    @classmethod
    def from_mapping(cls, value: Any, fallback_frame: str = LAB_WORLD_FRAME) -> "Pose3D":
        data = _mapping(value)
        position = data.get("position", data)
        orientation = data.get("orientation_rpy", data.get("rotation", data.get("orientation", {})))
        return cls(
            frame_id=str(data.get("frame_id", fallback_frame)),
            position=_xyz(position),
            orientation_rpy={
                "roll": _number(_mapping(orientation).get("roll", _mapping(orientation).get("x"))),
                "pitch": _number(_mapping(orientation).get("pitch", _mapping(orientation).get("y"))),
                "yaw": _number(_mapping(orientation).get("yaw", _mapping(orientation).get("z"))),
            },
            unit=str(data.get("unit", "mm")),
        )

    @classmethod
    def from_graph_node(cls, node: Dict[str, Any], fallback_frame: str = LAB_WORLD_FRAME) -> "Pose3D":
        pose = _mapping(node.get("pose"))
        if pose:
            return cls.from_mapping(pose, fallback_frame=fallback_frame)
        position = node.get("position", {})
        return cls.from_mapping(position, fallback_frame=fallback_frame)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "position": dict(self.position),
            "orientation_rpy": dict(self.orientation_rpy),
            "unit": self.unit,
        }


@dataclass(frozen=True)
class AccessZone:
    """A robot approach or keepout zone for a resource."""

    id: str
    frame_id: str
    pose: Pose3D
    reachable_by: List[str] = field(default_factory=list)
    keepout: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Dict[str, Any], fallback_frame: str) -> "AccessZone":
        frame_id = str(value.get("frame_id", fallback_frame))
        return cls(
            id=str(value["id"]),
            frame_id=frame_id,
            pose=Pose3D.from_mapping(value.get("pose", {}), fallback_frame=frame_id),
            reachable_by=_string_list(value.get("reachable_by")),
            keepout=bool(value.get("keepout", False)),
            metadata=_mapping(value.get("metadata")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "frame_id": self.frame_id,
            "pose": self.pose.to_dict(),
            "reachable_by": list(self.reachable_by),
            "keepout": self.keepout,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Affordance:
    """An operable part or region of a laboratory resource."""

    id: str
    kind: str
    frame_id: str
    pose: Pose3D
    action_primitives: List[str] = field(default_factory=list)
    reachable_by: List[str] = field(default_factory=list)
    access_zone: Optional[str] = None
    constraints: Dict[str, Any] = field(default_factory=dict)
    device_endpoint: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Dict[str, Any], fallback_frame: str) -> "Affordance":
        frame_id = str(value.get("frame_id", fallback_frame))
        return cls(
            id=str(value["id"]),
            kind=str(value.get("kind", "operable_region")),
            frame_id=frame_id,
            pose=Pose3D.from_mapping(value.get("pose", {}), fallback_frame=frame_id),
            action_primitives=_string_list(value.get("action_primitives")),
            reachable_by=_string_list(value.get("reachable_by")),
            access_zone=value.get("access_zone"),
            constraints=_mapping(value.get("constraints")),
            device_endpoint=_mapping(value.get("device_endpoint")),
            metadata=_mapping(value.get("metadata")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "frame_id": self.frame_id,
            "pose": self.pose.to_dict(),
            "action_primitives": list(self.action_primitives),
            "reachable_by": list(self.reachable_by),
            "access_zone": self.access_zone,
            "constraints": dict(self.constraints),
            "device_endpoint": dict(self.device_endpoint),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RobotOperableResource:
    """A Uni-Lab-OS graph node enriched with robot-facing semantics."""

    id: str
    name: str
    resource_type: str
    class_name: str
    pose: Pose3D
    affordances: List[Affordance] = field(default_factory=list)
    access_zones: List[AccessZone] = field(default_factory=list)
    reachable_by: List[str] = field(default_factory=list)
    state_variables: Dict[str, Any] = field(default_factory=dict)
    device_endpoints: Dict[str, Any] = field(default_factory=dict)
    robot_bindings: Dict[str, Any] = field(default_factory=dict)
    locks: Dict[str, Any] = field(default_factory=dict)
    safety: Dict[str, Any] = field(default_factory=dict)
    calibration: Dict[str, Any] = field(default_factory=dict)
    source_node: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_graph_node(cls, node: Dict[str, Any]) -> "RobotOperableResource":
        extra = _mapping(node.get("extra"))
        robo = _mapping(extra.get("robo_unilabos") or extra.get("robot_operable"))
        frame_id = str(robo.get("frame_id", f"{node.get('id', node.get('name', 'resource'))}_frame"))
        affordances = [
            Affordance.from_mapping(item, fallback_frame=frame_id)
            for item in _list(robo.get("affordances"))
            if isinstance(item, dict) and item.get("id")
        ]
        access_zones = [
            AccessZone.from_mapping(item, fallback_frame=frame_id)
            for item in _list(robo.get("access_zones"))
            if isinstance(item, dict) and item.get("id")
        ]
        return cls(
            id=str(node.get("id", node.get("name", ""))),
            name=str(node.get("name", node.get("id", ""))),
            resource_type=str(robo.get("resource_type", node.get("type", ""))),
            class_name=str(node.get("class", node.get("klass", ""))),
            pose=Pose3D.from_mapping(robo.get("pose"), fallback_frame=frame_id)
            if robo.get("pose")
            else Pose3D.from_graph_node(node, fallback_frame=frame_id),
            affordances=affordances,
            access_zones=access_zones,
            reachable_by=_string_list(robo.get("reachable_by")),
            state_variables=_mapping(robo.get("state_variables")),
            device_endpoints=_mapping(robo.get("device_endpoints")),
            robot_bindings=_mapping(robo.get("robot_bindings")),
            locks=_mapping(robo.get("locks")),
            safety=_mapping(robo.get("safety")),
            calibration=_mapping(robo.get("calibration")),
            source_node=node,
        )

    @property
    def is_robot_operable(self) -> bool:
        return bool(self.affordances or self.access_zones or self.robot_bindings or self.reachable_by)

    def affordance(self, affordance_id: str) -> Optional[Affordance]:
        for affordance in self.affordances:
            if affordance.id == affordance_id:
                return affordance
        return None

    def access_zone(self, zone_id: str) -> Optional[AccessZone]:
        for zone in self.access_zones:
            if zone.id == zone_id:
                return zone
        return None

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "resource_type": self.resource_type,
            "class": self.class_name,
            "robot_operable": self.is_robot_operable,
            "affordances": [affordance.id for affordance in self.affordances],
            "access_zones": [zone.id for zone in self.access_zones],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.summary(),
            "pose": self.pose.to_dict(),
            "affordance_details": [affordance.to_dict() for affordance in self.affordances],
            "access_zone_details": [zone.to_dict() for zone in self.access_zones],
            "reachable_by": list(self.reachable_by),
            "state_variables": dict(self.state_variables),
            "device_endpoints": dict(self.device_endpoints),
            "robot_bindings": dict(self.robot_bindings),
            "locks": dict(self.locks),
            "safety": dict(self.safety),
            "calibration": dict(self.calibration),
        }


@dataclass(frozen=True)
class CommandResult:
    """Machine-readable result object returned by robot-facing commands."""

    ok: bool
    command: str
    observations: Dict[str, Any] = field(default_factory=dict)
    transaction_id: Optional[str] = None
    resource_locks: List[str] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "ok": self.ok,
            "command": self.command,
            "transaction_id": self.transaction_id,
            "resource_locks": list(self.resource_locks),
            "observations": dict(self.observations),
            "provenance": dict(self.provenance),
        }
        if self.error:
            payload["error"] = self.error
        return payload

