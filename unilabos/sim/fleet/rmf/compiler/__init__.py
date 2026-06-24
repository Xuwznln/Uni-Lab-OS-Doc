"""RMF 编译器：Pascal 发布版 scene → building.yaml / semantic_map.json（#17 §6.3 / #18 §4.1）。

nav_graph 不在此生成——它是 RMF 官方 CLI `building_map_generator nav` 的构建期产物
（见 gateway/supervisor）。本包只产出 building.yaml + semantic_map + 诊断。
"""

from __future__ import annotations

from unilabos.sim.fleet.rmf.compiler.pascal_to_rmf_ir import build_rmf_map_ir
from unilabos.sim.fleet.rmf.compiler.rmf_building_yaml_writer import build_building_dict, dump_building_yaml
from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfMapIR
from unilabos.sim.fleet.rmf.compiler.semantic_map_writer import build_semantic_map, dump_semantic_map_json
from unilabos.sim.fleet.rmf.compiler.validation import validate_ir

__all__ = [
    "build_rmf_map_ir",
    "validate_ir",
    "build_building_dict",
    "dump_building_yaml",
    "build_semantic_map",
    "dump_semantic_map_json",
    "RmfMapIR",
    "compile_scene",
]


def compile_scene(scene, robots=None, *, lab_uuid="", scene_hash="", **kwargs):
    """便捷入口：scene → (RmfMapIR, building_dict, semantic_map_dict)，已跑校验。"""
    ir = build_rmf_map_ir(scene, robots, lab_uuid=lab_uuid, scene_hash=scene_hash, **kwargs)
    validate_ir(ir)
    waypoint_device_uuid = getattr(ir, "_waypoint_device_uuid", {})
    restricted_zones = getattr(ir, "_restricted_zones", [])
    building = build_building_dict(ir)
    semantic = build_semantic_map(ir, waypoint_device_uuid=waypoint_device_uuid, restricted_zones=restricted_zones)
    return ir, building, semantic
