"""designer 路线回放（#22 §3）：把 `rmf_agv_routes.json` 的 per-transfer 直角折线（**lab-local 坐标**）
转换到 nav_graph 的**边缘帧**（米制），供 OS 在 `designer` 规划模式下直接驱动小车沿设计路线行驶。

坐标系：`rmf_agv_routes.json` 的 `waypoints`/`geometryM` 是 layout-optimizer 的 lab-local 米制；
而小车/`nav_graph 0.yaml` 是 building_map_generator 生成的边缘帧米制。两帧用**两边共同的黑点**
（`dock_*`，名字一致）拟合一个轴对齐仿射（`nav = a*lab + b`，x/y 各自，最小二乘）即可互转
（实测约为纯平移、scale≈1）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class DesignerRouteReplay:
    """加载 designer 路线 + nav_graph，提供 `path_edge(from_dock, to_dock)` → 边缘帧折线点。"""

    def __init__(self, routes_path: str, nav_graph_path: str) -> None:
        routes = json.loads(Path(routes_path).read_text(encoding="utf-8"))
        # lab-local 黑点 + per-transfer 折线
        self._lab_dock: Dict[str, Tuple[float, float]] = {
            str(w["name"]): (float(w["x"]), float(w["y"])) for w in (routes.get("waypoints") or [])
        }
        self._routes: Dict[Tuple[str, str], List[List[float]]] = {}
        for r in routes.get("routes") or []:
            f, t = str(r.get("fromWaypoint") or ""), str(r.get("toWaypoint") or "")
            geom = r.get("geometryM") or []
            if f and t and geom:
                self._routes[(f, t)] = geom

        # nav_graph 边缘帧黑点坐标（命名顶点）
        import yaml

        g = yaml.safe_load(Path(nav_graph_path).read_text(encoding="utf-8"))
        self._edge_dock: Dict[str, Tuple[float, float]] = {}
        for v in g["levels"]["L1"]["vertices"]:
            name = v[2].get("name") if len(v) > 2 and isinstance(v[2], dict) else None
            if name:
                self._edge_dock[str(name)] = (float(v[0]), float(v[1]))

        self._affine = self._fit_affine()  # (ax, bx, ay, by) 或 None

    @staticmethod
    def _lsq(xs: List[float], ys: List[float]) -> Tuple[float, float]:
        """最小二乘拟合 y = a*x + b。"""
        n = len(xs)
        sx, sy = sum(xs), sum(ys)
        sxx = sum(v * v for v in xs)
        sxy = sum(a * b for a, b in zip(xs, ys))
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-9:
            return 1.0, (sy / n - sx / n if n else 0.0)
        a = (n * sxy - sx * sy) / denom
        b = (sy - a * sx) / n
        return a, b

    def _fit_affine(self) -> Optional[Tuple[float, float, float, float]]:
        xl, xe, yl, ye = [], [], [], []
        for name, (lx, ly) in self._lab_dock.items():
            e = self._edge_dock.get(name)
            if e:
                xl.append(lx)
                xe.append(e[0])
                yl.append(ly)
                ye.append(e[1])
        if len(xl) < 2:
            return None
        ax, bx = self._lsq(xl, xe)
        ay, by = self._lsq(yl, ye)
        return (ax, bx, ay, by)

    @property
    def ready(self) -> bool:
        return self._affine is not None

    def to_edge(self, x_lab: float, y_lab: float) -> Tuple[float, float]:
        ax, bx, ay, by = self._affine  # type: ignore[misc]
        return (ax * x_lab + bx, ay * y_lab + by)

    def edge_to_lab(self, x_edge: float, y_edge: float) -> Tuple[float, float]:
        """`to_edge` 的逆：边缘帧 → lab-local（供 FloorFrame 求楼层帧，#24.1 §0）。"""
        ax, bx, ay, by = self._affine  # type: ignore[misc]
        ax = ax if abs(ax) > 1e-9 else 1.0
        ay = ay if abs(ay) > 1e-9 else 1.0
        return ((x_edge - bx) / ax, (y_edge - by) / ay)

    def nearest_edge_dock(self, x: float, y: float) -> Optional[str]:
        best, bd = None, float("inf")
        for name, (ex, ey) in self._edge_dock.items():
            d = math.hypot(ex - x, ey - y)
            if d < bd:
                bd, best = d, name
        return best

    def path_edge(self, from_dock: str, to_dock: str) -> Optional[List[Tuple[float, float]]]:
        """designer 路线 from_dock→to_dock 的折线，转到边缘帧；无该预计算路线或无仿射 → None。"""
        geom = self._routes.get((from_dock, to_dock))
        if not geom or self._affine is None:
            return None
        return [self.to_edge(float(p[0]), float(p[1])) for p in geom if isinstance(p, (list, tuple)) and len(p) >= 2]

    def has_route(self, from_dock: str, to_dock: str) -> bool:
        return (from_dock, to_dock) in self._routes
