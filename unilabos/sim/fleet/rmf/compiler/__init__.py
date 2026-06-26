"""RMF 编译器：Pascal 发布版 scene → building.yaml / semantic_map.json（#17 §6.3 / #18 §4.1）。

nav_graph 不在此生成——它是 RMF 官方 CLI `building_map_generator nav` 的构建期产物
（见 gateway/supervisor）。本包只产出 building.yaml + semantic_map + 诊断。
"""

from __future__ import annotations

from unilabos.sim.fleet.rmf.compiler.layout_optimizer_to_rmf_ir import (
    apply_route_overrides,
    build_ir_from_agv_routes,
    build_layout_optimizer_rmf_ir,
    merge_transfer_plan_into_semantic,
)
from unilabos.sim.fleet.rmf.compiler.pascal_to_rmf_ir import build_rmf_map_ir
from unilabos.sim.fleet.rmf.compiler.rmf_building_yaml_writer import build_building_dict, dump_building_yaml
from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfMapIR
from unilabos.sim.fleet.rmf.compiler.semantic_map_writer import build_semantic_map, dump_semantic_map_json
from unilabos.sim.fleet.rmf.compiler.validation import validate_ir
from unilabos.sim.fleet.rmf.layout_optimizer.ingest import load_layout_optimizer_dir
from unilabos.sim.fleet.rmf.layout_optimizer.transfer_plan_builder import build_transfer_plan

__all__ = [
    "build_rmf_map_ir",
    "build_layout_optimizer_rmf_ir",
    "build_ir_from_agv_routes",
    "apply_route_overrides",
    "validate_ir",
    "build_building_dict",
    "dump_building_yaml",
    "build_semantic_map",
    "dump_semantic_map_json",
    "RmfMapIR",
    "compile_scene",
    "compile_layout_optimizer_dir",
    "load_layout_optimizer_dir",
    "build_transfer_plan",
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


def compile_layout_optimizer_dir(
    directory,
    robots=None,
    *,
    lab_uuid: str = "",
    scene_hash: str = "",
    include_coarse_nav: bool = True,
    snap_devices_to_nav: bool = True,
    route_overrides=None,
    **kwargs,
):
    """layout-optimizer 输出目录 → (RmfMapIR, building_dict, semantic_map_dict, transfer_plan)。

    `route_overrides`：可选的最小路线编辑（#21 §7.0 入口 B），形状见 `apply_route_overrides`。
    """
    artifacts = load_layout_optimizer_dir(directory)
    transfer_plan = build_transfer_plan(artifacts)
    ir = build_layout_optimizer_rmf_ir(
        artifacts,
        lab_uuid=lab_uuid,
        scene_hash=scene_hash,
        include_coarse_nav=include_coarse_nav,
        snap_devices_to_nav=snap_devices_to_nav,
        route_overrides=route_overrides,
        **kwargs,
    )
    if robots:
        from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfRobotIR

        for rcfg in robots:
            ir.robots.append(
                RmfRobotIR(
                    robot_name=rcfg.get("robot_name") or rcfg.get("id") or "agv",
                    fleet_name=rcfg.get("fleet_name", "unilab_agv"),
                    kind=rcfg.get("kind", "sim"),
                    footprint_radius=float(rcfg.get("footprint_radius", 0.35)),
                    charger_waypoint=rcfg.get("charger_waypoint", ""),
                    initial_waypoint=rcfg.get("initial_waypoint", ""),
                    spawn_robot_type=rcfg.get("spawn_robot_type", "Open-RMF/TinyRobot"),
                    target_map=dict(rcfg.get("target_map", {}) or {}),
                )
            )
    validate_ir(ir)
    building = build_building_dict(ir)
    semantic = build_semantic_map(
        ir,
        waypoint_to_instance=getattr(ir, "_waypoint_to_instance", {}),
    )
    semantic = merge_transfer_plan_into_semantic(semantic, transfer_plan, artifacts)
    return ir, building, semantic, transfer_plan
