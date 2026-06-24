"""RmfMapIR → RMF `building.yaml`（dict / 字符串）写出器（#18 §2.1 / §4.1）。

顶点/lane/wall/floor/door 的 param 一律编码为 `[type_code, value]`
（1=str 2=int 3=double 4=bool），与 `rmf_demos_maps/maps/*/*.building.yaml` 对齐。
"""

from __future__ import annotations

from typing import Any, Dict, List

from unilabos.sim.fleet.rmf.compiler.rmf_ir import (
    PARAM_BOOL,
    PARAM_DOUBLE,
    PARAM_INT,
    PARAM_STR,
    RmfDoorIR,
    RmfLevelIR,
    RmfLiftIR,
    RmfMapIR,
    RmfVertexIR,
)


def _encode_param(value: Any) -> List[Any]:
    """把 Python 值编码为 building.yaml 的 [type_code, value]。bool 必须先于 int 判断。"""
    if isinstance(value, bool):
        return [PARAM_BOOL, value]
    if isinstance(value, int):
        return [PARAM_INT, value]
    if isinstance(value, float):
        return [PARAM_DOUBLE, value]
    return [PARAM_STR, str(value)]


def _encode_params(params: Dict[str, Any]) -> Dict[str, List[Any]]:
    return {k: _encode_param(v) for k, v in sorted(params.items())}


def _vertex_row(vertex: RmfVertexIR) -> List[Any]:
    row: List[Any] = [vertex.x_m, vertex.y_m, vertex.z_m, vertex.name]
    if vertex.params:
        row.append(_encode_params(vertex.params))
    return row


def _door_row(door: RmfDoorIR) -> List[Any]:
    return [
        door.v1,
        door.v2,
        _encode_params(
            {
                "name": door.name,
                "type": door.door_type,
                "motion_axis": door.motion_axis,
                "motion_degrees": float(door.motion_degrees),
                "motion_direction": int(door.motion_direction),
                "plugin": door.plugin,
            }
        ),
    ]


def _level_dict(level: RmfLevelIR) -> Dict[str, Any]:
    return {
        "elevation": level.elevation,
        "vertices": [_vertex_row(v) for v in level.vertices],
        "lanes": [
            [
                lane.v1,
                lane.v2,
                _encode_params(
                    {
                        "bidirectional": bool(lane.bidirectional),
                        "graph_idx": int(lane.graph_idx),
                        "orientation": lane.orientation,
                        "speed_limit": float(lane.speed_limit),
                    }
                ),
            ]
            for lane in level.lanes
        ],
        "walls": [[w[0], w[1], _encode_params({"texture_name": "wall_white"})] for w in level.walls],
        "floors": [{"vertices": list(poly), "parameters": {}} for poly in level.floors],
        "doors": [_door_row(d) for d in level.doors],
    }


def _lift_dict(lift: RmfLiftIR) -> Dict[str, Any]:
    return {
        "x": lift.x_m,
        "y": lift.y_m,
        "yaw": lift.yaw,
        "width": lift.width,
        "depth": lift.depth,
        "lowest_floor": lift.lowest_floor,
        "highest_floor": lift.highest_floor,
        "initial_floor_name": lift.initial_floor_name or lift.lowest_floor,
        "reference_floor_name": lift.initial_floor_name or lift.lowest_floor,
        "level_doors": {k: list(v) for k, v in lift.level_doors.items()},
        "doors": {k: dict(v) for k, v in lift.doors.items()},
        "plugins": True,
    }


def build_building_dict(ir: RmfMapIR) -> Dict[str, Any]:
    """RmfMapIR → building.yaml 顶层 dict。"""
    return {
        "name": ir.building_name,
        "coordinate_system": ir.coordinate_system,
        "levels": {level.name: _level_dict(level) for level in ir.levels},
        "lifts": {lift.name: _lift_dict(lift) for lift in ir.lifts},
    }


def dump_building_yaml(ir: RmfMapIR) -> str:
    """序列化为 YAML 字符串（编译产物落盘用）。"""
    import yaml

    return yaml.safe_dump(build_building_dict(ir), sort_keys=True, allow_unicode=True, default_flow_style=False)
