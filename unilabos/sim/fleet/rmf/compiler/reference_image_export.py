"""米制 RmfMapIR → reference_image building.yaml（Dashboard 底图兼容）。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from unilabos.sim.fleet.rmf.compiler.floorplan_image import (
    FloorplanBounds,
    bounds_from_lab,
    generate_floorplan_png,
)
from unilabos.sim.fleet.rmf.compiler.rmf_building_yaml_writer import (
    PARAM_DOUBLE,
    PARAM_INT,
    _door_row,
    _encode_params,
)
from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfLevelIR, RmfMapIR, RmfVertexIR

DEFAULT_PPM = 20
FLOORPLAN_FILENAME = "L1_floorplan.png"


def meter_to_reference_pixel(x_m: float, y_m: float, bounds: FloorplanBounds, ppm: int) -> Tuple[float, float]:
    """米制 (x 右, y 上) → traffic_editor reference_image 像素坐标（y 向下）。"""
    x_px = (x_m - bounds.xmin) * ppm
    y_px = (bounds.ymax - y_m) * ppm
    return x_px, y_px


def _vertex_row_reference(
    vertex: RmfVertexIR,
    bounds: FloorplanBounds,
    ppm: int,
    *,
    coord_offset: Tuple[float, float] = (0.0, 0.0),
) -> List[Any]:
    x_m = vertex.x_m + coord_offset[0]
    y_m = vertex.y_m + coord_offset[1]
    x_px, y_px = meter_to_reference_pixel(x_m, y_m, bounds, ppm)
    row: List[Any] = [x_px, y_px, vertex.z_m, vertex.name]
    if vertex.params:
        row.append(_encode_params(vertex.params))
    return row


def _pick_calibration_measurement(
    level: RmfLevelIR,
    bounds: FloorplanBounds,
    ppm: int,
    *,
    min_dist_m: float = 5.0,
) -> Tuple[int, int, float]:
    """选一对相距足够远的顶点作比例尺标定。"""
    best: Tuple[float, int, int] | None = None
    for i, va in enumerate(level.vertices):
        for j in range(i + 1, len(level.vertices)):
            vb = level.vertices[j]
            dist_m = math.hypot(va.x_m - vb.x_m, va.y_m - vb.y_m)
            if dist_m < min_dist_m:
                continue
            if best is None or dist_m > best[0]:
                best = (dist_m, i, j)
    if best is None:
        # 退化：用房间宽度作标定
        i, j = 0, min(1, len(level.vertices) - 1)
        return i, j, bounds.width_m
    return best[1], best[2], best[0]


def _level_dict_reference(
    level: RmfLevelIR,
    bounds: FloorplanBounds,
    ppm: int,
    *,
    drawing_filename: str,
    coord_offset: Tuple[float, float] = (0.0, 0.0),
) -> Dict[str, Any]:
    v1, v2, dist_m = _pick_calibration_measurement(level, bounds, ppm)
    return {
        "drawing": {"filename": drawing_filename},
        "elevation": level.elevation,
        "vertices": [_vertex_row_reference(v, bounds, ppm, coord_offset=coord_offset) for v in level.vertices],
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
        "measurements": [[v1, v2, {f"distance": [PARAM_DOUBLE, float(dist_m)]}]],
    }


def build_reference_image_building_dict(
    ir: RmfMapIR,
    bounds: FloorplanBounds,
    ppm: int,
    *,
    drawing_filename: str = FLOORPLAN_FILENAME,
    coord_offset: Tuple[float, float] = (0.0, 0.0),
) -> Dict[str, Any]:
    from unilabos.sim.fleet.rmf.compiler.rmf_building_yaml_writer import _lift_dict

    return {
        "name": ir.building_name,
        "coordinate_system": "reference_image",
        "levels": {
            level.name: _level_dict_reference(
                level, bounds, ppm, drawing_filename=drawing_filename, coord_offset=coord_offset
            )
            for level in ir.levels
        },
        "lifts": {lift.name: _lift_dict(lift) for lift in ir.lifts},
    }


def finalize_building_for_dashboard(
    ir: RmfMapIR,
    out_dir: Path,
    *,
    lab: dict | None = None,
    placements: list | None = None,
    layout_dir: Path | None = None,
    scene_path: Path | None = None,
    ppm: int | None = None,
    drawing_filename: str = FLOORPLAN_FILENAME,
) -> Tuple[Dict[str, Any], Path, FloorplanBounds]:
    """生成底图 PNG + reference_image building dict（楼层坐标系）。"""
    from unilabos.sim.fleet.rmf.compiler.scene_floorplan_image import (
        bounds_from_floor_meta,
        generate_scene_floorplan_png,
        lab_origin,
        resolve_scene_path,
    )

    if not ir.levels:
        raise ValueError("RmfMapIR 无 level")
    level = ir.levels[0]

    bounds = bounds_from_floor_meta(lab)
    if bounds is None:
        bounds = bounds_from_lab(lab, level)

    resolved_scene = scene_path or resolve_scene_path(lab, layout_dir)
    use_scene = resolved_scene is not None and resolved_scene.is_file() and placements is not None
    if ppm is None:
        # 完整楼层约 100m 宽：10px/m → ~1040px，兼顾清晰度与体积
        ppm = 10 if use_scene else DEFAULT_PPM

    png_path = out_dir / drawing_filename
    if use_scene:
        generate_scene_floorplan_png(
            png_path,
            scene_path=resolved_scene,
            lab=lab or {},
            placements=placements,
            bounds=bounds,
            ppm=ppm,
        )
    else:
        generate_floorplan_png(level, png_path, bounds=bounds, ppm=ppm, lab=lab)

    ox, oy = lab_origin(lab) if use_scene else (0.0, 0.0)
    building = build_reference_image_building_dict(
        ir,
        bounds,
        ppm,
        drawing_filename=drawing_filename,
        coord_offset=(ox, oy),
    )
    return building, png_path, bounds
