"""坐标帧转换：RMF 边缘帧 → layout 楼层帧（floor frame, 米）（#24.1 §0）。

三类东西要在同一 3D 场景对齐：设备（`placements.center_floor`，楼层帧）、小车（`pose`，RMF
边缘帧）、导航点/路径（`nav_graphs/0.yaml`，RMF 边缘帧）。统一到**楼层帧**：设备原生不转；
小车 pose 与 nav 在 edge 侧由边缘帧 → 楼层帧后再上报。

关系：`edge = a·lab + b`（仿射，复用 `DesignerRouteReplay`，公共 `dock_*` 拟合，实测纯平移）；
`floor = lab + lab.origin`（`lab.origin` 取 `rmf_agv_routes.json` 的 `meta.labOrigin`）。
"""

from __future__ import annotations

import json
import os
from typing import Optional, Tuple


class FloorFrame:
    """RMF 边缘帧 → 楼层帧（米）。复用 designer_route 的仿射 + rmf_agv_routes 的 labOrigin。"""

    def __init__(self, generated_map_dir: str) -> None:
        self._ready = False
        self.ox, self.oy = 0.0, 0.0
        self._replay = None
        routes_path = os.path.join(generated_map_dir, "rmf_agv_routes.json")
        nav_path = os.path.join(generated_map_dir, "nav_graphs", "0.yaml")
        try:
            from unilabos.sim.fleet.rmf.designer_route import DesignerRouteReplay

            self._replay = DesignerRouteReplay(routes_path, nav_path)
            self.ox, self.oy = self._load_lab_origin(routes_path)
            self._ready = bool(self._replay.ready)
        except Exception:  # noqa: BLE001
            self._ready = False

    @staticmethod
    def _load_lab_origin(routes_path: str) -> Tuple[float, float]:
        data = json.loads(open(routes_path, encoding="utf-8").read())
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else data
        origin = meta.get("labOrigin") or data.get("labOrigin") or [0.0, 0.0]
        return (float(origin[0]), float(origin[1]))

    @property
    def ready(self) -> bool:
        return self._ready

    def edge_to_floor(self, x_edge: float, y_edge: float) -> Tuple[float, float]:
        """边缘帧 (米) → 楼层帧 (米)。未就绪则原样返回（降级，不崩）。"""
        if not self._ready or self._replay is None:
            return (x_edge, y_edge)
        lx, ly = self._replay.edge_to_lab(x_edge, y_edge)
        return (lx + self.ox, ly + self.oy)

    def edge_to_floor_mm(self, x_edge: float, y_edge: float) -> Tuple[float, float]:
        """边缘帧 (米) → 楼层帧 (毫米)，前端 RmfWaypoint/pose.position 用 mm。"""
        fx, fy = self.edge_to_floor(x_edge, y_edge)
        return (fx * 1000.0, fy * 1000.0)
