"""完整场景楼层底图（墙体 + AGV 房间 + 设备），与 layout_optimizer scene_layout.py 对齐。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from PIL import Image, ImageDraw

from unilabos.sim.fleet.rmf.compiler.floorplan_image import FloorplanBounds


def resolve_scene_path(lab: dict | None, layout_dir: Path | None) -> Path | None:
    """从 lab.json _meta.source_scene 解析场景 JSON 路径。"""
    if not lab or not layout_dir:
        return None
    meta = lab.get("_meta") or {}
    name = str(meta.get("source_scene") or "").strip()
    if not name:
        return None
    root = layout_dir.resolve()
    for candidate in (
        root / name,
        root.parent / name,
        root.parent.parent / name,
    ):
        if candidate.is_file():
            return candidate
    return None


def lab_origin(lab: dict | None) -> Tuple[float, float]:
    origin = (lab or {}).get("lab", {}).get("origin") or [0.0, 0.0]
    return float(origin[0]), float(origin[1])


def bounds_from_floor_meta(lab: dict | None, *, margin_m: float = 2.0) -> FloorplanBounds | None:
    """楼层外包框（与 scene_layout xlim/ylim ±2m 一致）。"""
    meta = (lab or {}).get("_meta") or {}
    fb = meta.get("floor_bbox")
    if not fb or len(fb) != 4:
        return None
    xmin, ymin, xmax, ymax = (float(v) for v in fb)
    return FloorplanBounds(xmin - margin_m, ymin - margin_m, xmax + margin_m, ymax + margin_m)


def _wall_polygon(start: Sequence[float], end: Sequence[float], thickness: float) -> List[Tuple[float, float]]:
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return []
    nx, ny = -dy / length, dx / length
    h = max(thickness, 0.05) / 2.0
    return [
        (x0 + nx * h, y0 + ny * h),
        (x1 + nx * h, y1 + ny * h),
        (x1 - nx * h, y1 - ny * h),
        (x0 - nx * h, y0 - ny * h),
    ]


def _device_colors(types: List[str]) -> Dict[str, Tuple[int, int, int]]:
    """近似 matplotlib tab20 的离散色。"""
    base = [
        (31, 119, 180),
        (255, 127, 14),
        (44, 160, 44),
        (214, 39, 40),
        (148, 103, 189),
        (140, 86, 75),
        (227, 119, 194),
        (127, 127, 127),
        (188, 189, 34),
        (23, 190, 207),
        (174, 199, 232),
        (255, 187, 120),
        (152, 223, 138),
        (255, 152, 150),
        (197, 176, 213),
        (196, 156, 148),
        (247, 182, 210),
        (199, 199, 199),
        (219, 219, 141),
        (158, 218, 229),
    ]
    return {t: base[i % len(base)] for i, t in enumerate(sorted(types))}


def _half_extents(bbox: Sequence[float], rot_deg: int) -> Tuple[float, float]:
    a, b = float(bbox[0]) / 2.0, float(bbox[1]) / 2.0
    if int(rot_deg) % 180 == 90:
        a, b = b, a
    return a, b


def generate_scene_floorplan_png(
    out_path: Path,
    *,
    scene_path: Path,
    lab: dict,
    placements: List[dict],
    bounds: FloorplanBounds,
    ppm: int = 10,
) -> None:
    """渲染完整楼层场景底图（楼层坐标系，y 向上）。"""
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    walls = [n for n in scene.get("nodes", {}).values() if n.get("type") == "wall"]

    lab_obj = lab.get("lab") or {}
    lw = float(lab_obj.get("width") or 0.0)
    lh = float(lab_obj.get("height") or 0.0)
    ox, oy = lab_origin(lab)

    width_px = max(1, int(math.ceil(bounds.width_m * ppm)))
    height_px = max(1, int(math.ceil(bounds.height_m * ppm)))
    image = Image.new("RGB", (width_px, height_px), (252, 252, 250))
    draw = ImageDraw.Draw(image)

    def to_px(x_m: float, y_m: float) -> Tuple[int, int]:
        px = int(round((x_m - bounds.xmin) * ppm))
        py = int(round((bounds.ymax - y_m) * ppm))
        return px, py

    def draw_poly(points: List[Tuple[float, float]], fill: Tuple[int, int, int], outline: Tuple[int, int, int] | None = None):
        if len(points) < 3:
            return
        flat = [c for p in points for c in to_px(p[0], p[1])]
        draw.polygon(flat, fill=fill, outline=outline)

    # 墙体
    for wall in walls:
        start, end = wall.get("start"), wall.get("end")
        if not (isinstance(start, list) and isinstance(end, list)):
            continue
        poly = _wall_polygon(start, end, float(wall.get("thickness", 0.2)))
        if poly:
            draw_poly(poly, fill=(77, 77, 77))

    # AGV 布局房间（浅蓝底 + 蓝框）
    if lw > 0 and lh > 0:
        room_pts = [(ox, oy), (ox + lw, oy), (ox + lw, oy + lh), (ox, oy + lh)]
        draw_poly(room_pts, fill=(207, 232, 255), outline=(31, 111, 214))

    # 设备
    types = sorted({str(p.get("device_type") or "") for p in placements if p.get("device_type")})
    colors = _device_colors(types)
    for placement in placements:
        center = placement.get("center_floor")
        bbox = placement.get("bbox")
        if not (isinstance(center, list) and len(center) >= 2 and isinstance(bbox, list) and len(bbox) >= 2):
            continue
        cx, cy = float(center[0]), float(center[1])
        a, b = _half_extents(bbox, int(placement.get("rotation_deg", 0)))
        device_type = str(placement.get("device_type") or "")
        color = colors.get(device_type, (120, 120, 120))
        x0, y0 = cx - a, cy - b
        x1, y1 = cx + a, cy + b
        p0 = to_px(x0, y1)
        p1 = to_px(x1, y0)
        draw.rectangle([p0[0], p0[1], p1[0], p1[1]], fill=color, outline=(20, 20, 20))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, format="PNG")
