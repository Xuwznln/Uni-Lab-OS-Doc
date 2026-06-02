from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from unilabos.queries.models import Pose, QueryAffordance, SafetyZone, State


def _parse_vector(value: Optional[str], default: Tuple[float, float, float]) -> List[float]:
    if not value:
        return list(default)
    items = [float(item) for item in value.split()]
    if len(items) != 3:
        raise ValueError(f"Expected 3-vector, got {value!r}")
    return items


def _identity() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _matmul(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    return [[sum(a[row][k] * b[k][col] for k in range(4)) for col in range(4)] for row in range(4)]


def _translation(xyz: List[float]) -> List[List[float]]:
    result = _identity()
    result[0][3], result[1][3], result[2][3] = xyz
    return result


def _rotation_x(angle: float) -> List[List[float]]:
    c, s = math.cos(angle), math.sin(angle)
    return [[1.0, 0.0, 0.0, 0.0], [0.0, c, -s, 0.0], [0.0, s, c, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _rotation_y(angle: float) -> List[List[float]]:
    c, s = math.cos(angle), math.sin(angle)
    return [[c, 0.0, s, 0.0], [0.0, 1.0, 0.0, 0.0], [-s, 0.0, c, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _rotation_z(angle: float) -> List[List[float]]:
    c, s = math.cos(angle), math.sin(angle)
    return [[c, -s, 0.0, 0.0], [s, c, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _rpy_matrix(rpy: List[float]) -> List[List[float]]:
    return _matmul(_matmul(_rotation_z(rpy[2]), _rotation_y(rpy[1])), _rotation_x(rpy[0]))


def _axis_rotation(axis: List[float], angle: float) -> List[List[float]]:
    norm = math.sqrt(sum(item * item for item in axis))
    if norm == 0.0:
        return _identity()
    x, y, z = [item / norm for item in axis]
    c, s = math.cos(angle), math.sin(angle)
    one = 1.0 - c
    return [
        [c + x * x * one, x * y * one - z * s, x * z * one + y * s, 0.0],
        [y * x * one + z * s, c + y * y * one, y * z * one - x * s, 0.0],
        [z * x * one - y * s, z * y * one + x * s, c + z * z * one, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _quat_xyzw_from_matrix(transform: List[List[float]]) -> List[float]:
    m00, m01, m02 = transform[0][0], transform[0][1], transform[0][2]
    m10, m11, m12 = transform[1][0], transform[1][1], transform[1][2]
    m20, m21, m22 = transform[2][0], transform[2][1], transform[2][2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        return [(m21 - m12) / scale, (m02 - m20) / scale, (m10 - m01) / scale, 0.25 * scale]
    if m00 > m11 and m00 > m22:
        scale = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        return [0.25 * scale, (m01 + m10) / scale, (m02 + m20) / scale, (m21 - m12) / scale]
    if m11 > m22:
        scale = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        return [(m01 + m10) / scale, 0.25 * scale, (m12 + m21) / scale, (m02 - m20) / scale]
    scale = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
    return [(m02 + m20) / scale, (m12 + m21) / scale, 0.25 * scale, (m10 - m01) / scale]


@dataclass(frozen=True)
class URDFJoint:
    name: str
    joint_type: str
    parent: str
    child: str
    origin_xyz: List[float]
    origin_rpy: List[float]
    axis: List[float]


@dataclass
class URDFRobotModelSource:
    name = "urdf_robot_model"

    urdf_path: Path
    robot_id: str = "roboarm_chem"
    joint_positions: Dict[str, float] = field(default_factory=dict)
    root_link: Optional[str] = None
    tool_link: Optional[str] = None
    tool_offset_xyz: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    workspace_center: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.25])
    workspace_size: List[float] = field(default_factory=lambda: [1.2, 1.2, 0.8])

    def __post_init__(self) -> None:
        self.urdf_path = Path(self.urdf_path)
        self._links, self._joints = self._parse_urdf(self.urdf_path)
        self._child_to_joint = {joint.child: joint for joint in self._joints}
        self._children = {joint.child for joint in self._joints}
        roots = [link for link in self._links if link not in self._children]
        if self.root_link is None:
            self.root_link = roots[0] if roots else (self._links[0] if self._links else "base_link")
        if self.tool_link is None:
            self.tool_link = self._links[-1] if self._links else self.root_link

    @classmethod
    def from_file(cls, urdf_path: str | Path, **kwargs) -> "URDFRobotModelSource":
        return cls(urdf_path=Path(urdf_path), **kwargs)

    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        link_name, synthetic_tool = self._resolve_target(target)
        if link_name is None:
            return None
        transform = self.forward_kinematics(link_name)
        if synthetic_tool:
            transform = _matmul(transform, _translation(self.tool_offset_xyz))
        return Pose(
            xyz=[transform[0][3], transform[1][3], transform[2][3]],
            quat_xyzw=_quat_xyzw_from_matrix(transform),
            frame_id=frame or self.root_link or "base_link",
            source=self.name,
            metadata={
                "robot_id": self.robot_id,
                "link": "tool0" if synthetic_tool else link_name,
                "source_link": link_name,
                "urdf_path": str(self.urdf_path),
                "tool_offset_xyz": list(self.tool_offset_xyz) if synthetic_tool else [0.0, 0.0, 0.0],
            },
        )

    def query_state(self, target: str) -> Optional[State]:
        if target != self.robot_id:
            return None
        return State(
            name=self.robot_id,
            values={
                "root_link": self.root_link,
                "tool_link": self.tool_link,
                "links": list(self._links),
                "joints": [joint.name for joint in self._joints],
                "joint_positions": dict(self.joint_positions),
                "urdf_path": str(self.urdf_path),
            },
            source=self.name,
        )

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        if target != self.robot_id or (kind is not None and kind != "end_effector"):
            return []
        pose = self.query_pose(f"{self.robot_id}.tool0")
        return [
            QueryAffordance(
                id="tool0",
                kind="end_effector",
                pose=pose,
                action_primitives=["move_to", "press_button", "pick", "place"],
                target=f"{self.robot_id}.tool0",
                metadata={"source_link": self.tool_link},
            )
        ]

    def query_action_schema(self, action: str):
        return None

    def query_safety_zones(self) -> List[SafetyZone]:
        return [
            SafetyZone(
                id=f"{self.robot_id}.rough_workspace",
                zone_type="workspace",
                frame_id=self.root_link or "base_link",
                bbox_center=list(self.workspace_center),
                bbox_size=list(self.workspace_size),
                source=self.name,
                metadata={"robot_id": self.robot_id, "model": "rough_kinematic_workspace"},
            )
        ]

    def forward_kinematics(self, link_name: str) -> List[List[float]]:
        if link_name not in self._links:
            raise KeyError(f"Unknown URDF link: {link_name}")
        chain: List[URDFJoint] = []
        cursor = link_name
        while cursor != self.root_link:
            joint = self._child_to_joint.get(cursor)
            if joint is None:
                break
            chain.append(joint)
            cursor = joint.parent
        transform = _identity()
        for joint in reversed(chain):
            origin = _matmul(_translation(joint.origin_xyz), _rpy_matrix(joint.origin_rpy))
            value = float(self.joint_positions.get(joint.name, 0.0))
            if joint.joint_type in ("revolute", "continuous"):
                motion = _axis_rotation(joint.axis, value)
            elif joint.joint_type == "prismatic":
                motion = _translation([item * value for item in joint.axis])
            else:
                motion = _identity()
            transform = _matmul(transform, _matmul(origin, motion))
        return transform

    def _resolve_target(self, target: str) -> Tuple[Optional[str], bool]:
        if target in self._links:
            return target, False
        prefix = f"{self.robot_id}."
        if target == self.robot_id:
            return self.root_link, False
        if not target.startswith(prefix):
            return None, False
        item = target[len(prefix) :]
        if item in ("tool0", "end_effector", "ee"):
            return self.tool_link, True
        if item in self._links:
            return item, False
        return None, False

    @staticmethod
    def _parse_urdf(urdf_path: Path) -> Tuple[List[str], List[URDFJoint]]:
        root = ET.parse(urdf_path).getroot()
        links = [item.attrib["name"] for item in root.findall("link")]
        joints: List[URDFJoint] = []
        for item in root.findall("joint"):
            origin = item.find("origin")
            axis = item.find("axis")
            joints.append(
                URDFJoint(
                    name=item.attrib["name"],
                    joint_type=item.attrib.get("type", "fixed"),
                    parent=item.find("parent").attrib["link"],
                    child=item.find("child").attrib["link"],
                    origin_xyz=_parse_vector(origin.attrib.get("xyz") if origin is not None else None, (0.0, 0.0, 0.0)),
                    origin_rpy=_parse_vector(origin.attrib.get("rpy") if origin is not None else None, (0.0, 0.0, 0.0)),
                    axis=_parse_vector(axis.attrib.get("xyz") if axis is not None else None, (0.0, 0.0, 1.0)),
                )
            )
        return links, joints
