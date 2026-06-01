from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_mapping(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class Pose:
    xyz: List[float]
    quat_xyzw: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    frame_id: str = "lab_world"
    stamp: str = field(default_factory=utc_timestamp)
    source: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "xyz": list(self.xyz),
            "quat_xyzw": list(self.quat_xyzw),
            "frame_id": self.frame_id,
            "stamp": self.stamp,
            "source": self.source,
            "metadata": _copy_mapping(self.metadata),
        }


@dataclass(frozen=True)
class State:
    name: str
    values: Dict[str, Any] = field(default_factory=dict)
    stamp: str = field(default_factory=utc_timestamp)
    source: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "values": _copy_mapping(self.values),
            "stamp": self.stamp,
            "source": self.source,
        }


@dataclass(frozen=True)
class QueryAffordance:
    id: str
    kind: str
    pose: Optional[Pose] = None
    action_primitives: List[str] = field(default_factory=list)
    target: Optional[str] = None
    constraints: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "action_primitives": list(self.action_primitives),
            "target": self.target,
            "constraints": _copy_mapping(self.constraints),
            "metadata": _copy_mapping(self.metadata),
        }
        if self.pose is not None:
            payload["pose"] = self.pose.to_dict()
        return payload


@dataclass(frozen=True)
class SafetyZone:
    id: str
    zone_type: str
    frame_id: str = "lab_world"
    bbox_center: List[float] = field(default_factory=list)
    bbox_size: List[float] = field(default_factory=list)
    source: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "zone_type": self.zone_type,
            "frame_id": self.frame_id,
            "bbox_center": list(self.bbox_center),
            "bbox_size": list(self.bbox_size),
            "source": self.source,
            "metadata": _copy_mapping(self.metadata),
        }


@dataclass(frozen=True)
class ActionSchema:
    action: str
    args: Dict[str, Any] = field(default_factory=dict)
    preconditions: List[Dict[str, Any]] = field(default_factory=list)
    postconditions: List[Dict[str, Any]] = field(default_factory=list)
    policy_preference: List[str] = field(default_factory=list)
    timeout_s: float = 60.0
    schema_version: str = "0.1"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Dict[str, Any]) -> "ActionSchema":
        return cls(
            action=str(data["action"]),
            args=_copy_mapping(data.get("args")),
            preconditions=[dict(item) for item in data.get("preconditions", []) if isinstance(item, dict)],
            postconditions=[dict(item) for item in data.get("postconditions", []) if isinstance(item, dict)],
            policy_preference=[str(item) for item in data.get("policy_preference", [])],
            timeout_s=float(data.get("timeout_s", 60.0)),
            schema_version=str(data.get("schema_version", "0.1")),
            metadata=_copy_mapping(data.get("metadata")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action": self.action,
            "args": _copy_mapping(self.args),
            "preconditions": [dict(item) for item in self.preconditions],
            "postconditions": [dict(item) for item in self.postconditions],
            "policy_preference": list(self.policy_preference),
            "timeout_s": self.timeout_s,
            "metadata": _copy_mapping(self.metadata),
        }


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    task_id: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    stamp: str = field(default_factory=utc_timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "task_id": self.task_id,
            "evidence": _copy_mapping(self.evidence),
            "failures": [dict(item) for item in self.failures],
            "stamp": self.stamp,
        }
