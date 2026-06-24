"""RMF 中间表示（IR）—— Pascal 发布版 scene 编译为 building.yaml 的中转结构（#18 §4.1 / #17 §6.3）。

用 dataclass（与 `unilabos/queries/models.py` 一致，依赖轻、可单测），坐标统一为
RMF 米制（由 `coordinate_transform.pascal_to_rmf` 换算后填入）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# RMF building.yaml param 的类型码（1=str 2=int 3=double 4=bool）
PARAM_STR = 1
PARAM_INT = 2
PARAM_DOUBLE = 3
PARAM_BOOL = 4


@dataclass
class RmfVertexIR:
    """RMF 顶点（waypoint）。`params` 为扁平语义键，写盘时编码为 [type_code, value]。"""

    name: str
    x_m: float
    y_m: float
    z_m: float = 0.0
    # 语义参数：is_charger / is_holding_point / is_parking_spot /
    # pickup_dispenser / dropoff_ingestor / spawn_robot_name / spawn_robot_type 等
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RmfLaneIR:
    """RMF 可通行 lane（顶点索引对）。"""

    v1: int
    v2: int
    bidirectional: bool = True
    speed_limit: float = 0.0
    graph_idx: int = 0
    orientation: str = ""


@dataclass
class RmfDoorIR:
    name: str
    v1: int
    v2: int
    door_type: str = "hinged"  # hinged / double_hinged / sliding / double_sliding
    motion_degrees: float = 90.0
    motion_direction: int = 1
    motion_axis: str = "start"
    plugin: str = "normal"


@dataclass
class RmfLevelIR:
    name: str
    elevation: float = 0.0
    vertices: List[RmfVertexIR] = field(default_factory=list)
    lanes: List[RmfLaneIR] = field(default_factory=list)
    # walls / floors 用顶点索引表达
    walls: List[List[int]] = field(default_factory=list)  # [[v1, v2], ...]
    floors: List[List[int]] = field(default_factory=list)  # [[idx, idx, ...], ...]
    doors: List[RmfDoorIR] = field(default_factory=list)

    def add_vertex(self, vertex: RmfVertexIR) -> int:
        """加入顶点并返回其索引（供 lane/wall/floor/door 引用）。"""
        self.vertices.append(vertex)
        return len(self.vertices) - 1

    def index_of(self, name: str) -> Optional[int]:
        for i, v in enumerate(self.vertices):
            if v.name == name:
                return i
        return None


@dataclass
class RmfLiftIR:
    name: str
    x_m: float
    y_m: float
    yaw: float = 0.0
    width: float = 1.0
    depth: float = 1.0
    lowest_floor: str = ""
    highest_floor: str = ""
    initial_floor_name: str = ""
    level_doors: Dict[str, List[str]] = field(default_factory=dict)
    doors: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class RmfRobotIR:
    """fleet 中的机器人，来源 graph 的 AGV 节点 config（非 scene 几何）。"""

    robot_name: str
    fleet_name: str
    kind: str = "sim"  # sim | real
    footprint_radius: float = 0.35
    charger_waypoint: str = ""
    initial_waypoint: str = ""
    spawn_robot_type: str = "Open-RMF/TinyRobot"
    # real (SEER) 专用：waypoint -> SEER target id
    target_map: Dict[str, str] = field(default_factory=dict)


@dataclass
class RmfDiagnostic:
    level: str  # info | warning | error
    code: str
    message: str
    entity_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"level": self.level, "code": self.code, "message": self.message, "entity_id": self.entity_id}


@dataclass
class RmfMapIR:
    lab_uuid: str
    scene_hash: str
    building_name: str = "building"
    coordinate_system: str = "cartesian_meters"
    levels: List[RmfLevelIR] = field(default_factory=list)
    lifts: List[RmfLiftIR] = field(default_factory=list)
    robots: List[RmfRobotIR] = field(default_factory=list)
    diagnostics: List[RmfDiagnostic] = field(default_factory=list)

    def has_errors(self) -> bool:
        return any(d.level == "error" for d in self.diagnostics)

    def diagnostics_as_dicts(self) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self.diagnostics]
