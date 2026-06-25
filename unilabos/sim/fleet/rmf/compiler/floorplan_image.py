"""从 RmfLevelIR 生成 Dashboard 底图 PNG（#21 floorplan）。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from PIL import Image, ImageDraw

from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfLevelIR


@dataclass(frozen=True)
class FloorplanBounds:
    """底图世界坐标边界（米，y 向上）。"""

    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def width_m(self) -> float:
        return self.xmax - self.xmin

    @property
    def height_m(self) -> float:
        return self.ymax - self.ymin

    def to_pixel(self, x_m: float, y_m: float, ppm: int) -> Tuple[int, int]:
        """世界米制坐标 → PIL 像素（左上为原点，y 向下）。"""
        px = int(round((x_m - self.xmin) * ppm))
        py = int(round((self.ymax - y_m) * ppm))
        return px, py


def bounds_from_level(level: RmfLevelIR, *, margin_m: float = 0.5) -> FloorplanBounds:
    xs = [v.x_m for v in level.vertices]
    ys = [v.y_m for v in level.vertices]
    if not xs:
        return FloorplanBounds(0.0, 0.0, 20.0, 20.0)
    return FloorplanBounds(
        min(xs) - margin_m,
        min(ys) - margin_m,
        max(xs) + margin_m,
        max(ys) + margin_m,
    )


def bounds_from_lab(lab: dict | None, level: RmfLevelIR, *, margin_m: float = 0.5) -> FloorplanBounds:
    """优先用 lab.json 房间尺寸，否则回退到顶点外包框。"""
    lab_obj = (lab or {}).get("lab") or {}
    width = float(lab_obj.get("width") or 0.0)
    height = float(lab_obj.get("height") or 0.0)
    if width > 0 and height > 0:
        vertex_bounds = bounds_from_level(level, margin_m=0.0)
        return FloorplanBounds(
            vertex_bounds.xmin - margin_m,
            vertex_bounds.ymin - margin_m,
            vertex_bounds.xmin + width + margin_m,
            vertex_bounds.ymin + height + margin_m,
        )
    return bounds_from_level(level, margin_m=margin_m)


def _iter_lane_segments(level: RmfLevelIR) -> Iterable[Tuple[Tuple[float, float], Tuple[float, float]]]:
    for lane in level.lanes:
        if lane.v1 < 0 or lane.v2 < 0:
            continue
        if lane.v1 >= len(level.vertices) or lane.v2 >= len(level.vertices):
            continue
        a = level.vertices[lane.v1]
        b = level.vertices[lane.v2]
        yield (a.x_m, a.y_m), (b.x_m, b.y_m)


def generate_floorplan_png(
    level: RmfLevelIR,
    out_path: Path,
    *,
    bounds: FloorplanBounds | None = None,
    ppm: int = 20,
    lab: dict | None = None,
) -> FloorplanBounds:
    """绘制楼层平面底图并写入 PNG。"""
    bounds = bounds or bounds_from_lab(lab, level)
    width_px = max(1, int(math.ceil(bounds.width_m * ppm)))
    height_px = max(1, int(math.ceil(bounds.height_m * ppm)))

    image = Image.new("RGB", (width_px, height_px), (230, 235, 245))
    draw = ImageDraw.Draw(image)

    # 房间外框
    room_rect = [
        bounds.to_pixel(bounds.xmin, bounds.ymax, ppm),
        bounds.to_pixel(bounds.xmax, bounds.ymin, ppm),
    ]
    draw.rectangle(room_rect, outline=(80, 100, 140), width=3, fill=(245, 248, 252))

    # 导航通道
    for (x1, y1), (x2, y2) in _iter_lane_segments(level):
        draw.line(
            [bounds.to_pixel(x1, y1, ppm), bounds.to_pixel(x2, y2, ppm)],
            fill=(190, 190, 190),
            width=2,
        )

    # 导航点
    for vertex in level.vertices:
        if not vertex.name.startswith("nav_"):
            continue
        px, py = bounds.to_pixel(vertex.x_m, vertex.y_m, ppm)
        r = 3
        draw.ellipse((px - r, py - r, px + r, py + r), fill=(210, 180, 120), outline=(150, 120, 70))

    # 设备接驳点
    for vertex in level.vertices:
        if vertex.name.startswith("nav_"):
            continue
        px, py = bounds.to_pixel(vertex.x_m, vertex.y_m, ppm)
        r = 4
        draw.rectangle((px - r, py - r, px + r, py + r), fill=(100, 149, 237), outline=(50, 90, 170))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, format="PNG")
    return bounds
