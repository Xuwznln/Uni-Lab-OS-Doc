"""fine 网格直角折线路由（path_studio「点两设备 → 直角最短路」，#21 §7 P3）。

复用 `uni-lab-designer/layout_optimizer/agv-only` 的 `build_drivable_grid` + `turn_aware_path`
+ `dock_for`（与 `agv_trajectory.png` 同源算法），在按车身腐蚀后的 0.1m 可行驶网格上，于两台设备的
接驳 cell 之间求**转弯感知最短路**——4 连通 → 天然横平竖直的直角折线。网格构建一次后缓存（~20ms）。

设计：catalog 里的 coarse navGraph 有 8 个不连通分量、无法跨分量路由（477/538 设备对算不出），
故必须在 fine 网格上算；fine 网格本身连通（components=1）。
"""

from __future__ import annotations

import json
import math
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_AGV_DIR: Optional[Path] = None


def _ensure_agv_on_path() -> Path:
    """把 uni-lab-designer/layout_optimizer/agv-only 加入 sys.path 并返回其路径。"""
    global _AGV_DIR
    if _AGV_DIR is not None:
        return _AGV_DIR
    here = Path(__file__).resolve()
    repo_root = here.parents[6]  # .../LeapLab
    agv = repo_root / "uni-lab-designer" / "layout_optimizer" / "agv-only"
    if not agv.is_dir():
        raise FileNotFoundError(f"agv-only 路由模块目录不存在: {agv}")
    if str(agv) not in sys.path:
        sys.path.insert(0, str(agv))
    _AGV_DIR = agv
    return agv


def _load_footprints(agv_dir: Path) -> Dict[str, Any]:
    fp = agv_dir.parent / "footprints.json"
    if not fp.is_file():
        return {}
    return json.loads(fp.read_text(encoding="utf-8"))


class FineGridRouter:
    """按 layout-optimizer 目录构建一次 fine 可行驶网格 + 设备 dock，缓存后多次路由。"""

    def __init__(
        self,
        layout_dir: str,
        *,
        grid_step: float = 0.1,
        turn_cost: float = 2.0,
        agv_radius: float = 1.5,
        aisle_width: float = 0.8,
    ) -> None:
        self.layout_dir = str(layout_dir)
        self.grid_step = grid_step
        self.turn_cost = turn_cost
        self._agv_radius = agv_radius
        self._aisle_width = aisle_width
        self._grid: Any = None
        self._robot: Any = None
        self._dock_cell: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def _build(self) -> None:
        if self._grid is not None:
            return
        with self._lock:
            if self._grid is not None:
                return
            agv = _ensure_agv_on_path()
            from distance import turn_aware_path  # noqa: F401  确保算法模块可导入
            from geometry import Placement, RobotSpec, normalize_openings
            from grid import build_drivable_grid
            from lab_io import resolve_lab
            from reachability import dock_for

            directory = Path(self.layout_dir)
            placements_data = json.loads((directory / "placements.json").read_text(encoding="utf-8"))
            flow = json.loads((directory / "flow_matrix.json").read_text(encoding="utf-8"))
            instances = flow.get("instances") or []
            footprints = _load_footprints(agv)

            lab = resolve_lab(
                lab_json=str(directory / "lab.json"),
                size=None,
                default_device_clearance=0.2,
                default_agv_clearance=0.2,
            )
            robot = RobotSpec(
                working_radius=self._agv_radius,
                aisle_width=self._aisle_width,
                grid_step=self.grid_step,
                drive_clearance=None,
                turn_cost=self.turn_cost,
            )
            by_id = {str(i.get("instance_id")): i for i in instances}
            placements = []
            for item in placements_data:
                iid = str(item.get("instance_id"))
                inst = by_id.get(iid, item)
                fp_key = item.get("footprint_key") or inst.get("footprint_key")
                bbox = item.get("bbox") or inst.get("bbox")
                if not bbox:
                    continue
                openings_raw = footprints.get(fp_key, {}).get("openings", []) if fp_key else []
                placements.append(
                    Placement(
                        instance_id=iid,
                        device_type=item.get("device_type") or inst.get("device_type", ""),
                        bbox=(float(bbox[0]), float(bbox[1])),
                        center=(float(item["center"][0]), float(item["center"][1])),
                        rotation_deg=int(item.get("rotation_deg", 0)),
                        footprint_key=fp_key,
                        openings=normalize_openings(openings_raw),
                    )
                )
            grid = build_drivable_grid(lab, placements, robot.grid_step, robot.clearance())
            dock_cell: Dict[str, Any] = {}
            for p in placements:
                try:
                    dock_cell[p.instance_id] = dock_for(p, grid, robot, None).dock_cell
                except Exception:  # noqa: BLE001
                    dock_cell[p.instance_id] = None
            self._grid = grid
            self._robot = robot
            self._dock_cell = dock_cell

    def dock_xy(self, instance: str) -> Optional[Tuple[float, float]]:
        """返回设备的真实接驳点坐标（fine 网格 dock cell 中心，= agv_trajectory.png 的黑点）。"""
        self._build()
        cell = self._dock_cell.get(str(instance))
        if cell is None:
            return None
        x, y = self._grid.cell_center(cell)
        return (round(float(x), 3), round(float(y), 3))

    def route(self, from_instance: str, to_instance: str) -> Optional[Dict[str, Any]]:
        """返回 {geometryM:[[x,y]...]}（lab_local_m 直角折线）；无法路由返回 None。"""
        self._build()
        from distance import turn_aware_path

        ca = self._dock_cell.get(str(from_instance))
        cb = self._dock_cell.get(str(to_instance))
        if ca is None or cb is None:
            return None
        cells = turn_aware_path(self._grid, ca, cb, self._robot.turn_cost)
        if not cells or len(cells) < 2:
            return None
        poly: List[List[float]] = [
            [round(float(x), 3), round(float(y), 3)] for (x, y) in (self._grid.cell_center(c) for c in cells)
        ]
        return {"geometryM": poly}


def polyline_corners(poly: List[List[float]]) -> List[List[float]]:
    """提取折线拐点（含首尾），去掉共线中间点。"""
    if len(poly) <= 2:
        return list(poly)
    out = [poly[0]]
    for i in range(1, len(poly) - 1):
        ax, ay = poly[i - 1]
        bx, by = poly[i]
        cx, cy = poly[i + 1]
        # 叉积≈0 即共线，跳过
        if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) > 1e-6:
            out.append(poly[i])
    out.append(poly[-1])
    return out


def snap_polyline_to_nav(
    poly: List[List[float]],
    nav_nodes: List[Dict[str, Any]],
    from_nav: Optional[str],
    to_nav: Optional[str],
) -> List[str]:
    """折线拐点吸附到最近 navGraph 节点名，首尾强制为 from/toNav，去重相邻。"""
    if not poly or not nav_nodes:
        return [n for n in (from_nav, to_nav) if n]
    names = [str(n.get("name")) for n in nav_nodes]
    xs = [float(n.get("x", 0.0)) for n in nav_nodes]
    ys = [float(n.get("y", 0.0)) for n in nav_nodes]

    def nearest(px: float, py: float) -> str:
        best_i = 0
        best_d = math.inf
        for i in range(len(names)):
            d = (xs[i] - px) ** 2 + (ys[i] - py) ** 2
            if d < best_d:
                best_d = d
                best_i = i
        return names[best_i]

    seq: List[str] = []
    for px, py in polyline_corners(poly):
        nm = nearest(px, py)
        if not seq or seq[-1] != nm:
            seq.append(nm)
    if from_nav:
        if seq and seq[0] != from_nav:
            seq.insert(0, from_nav)
        elif not seq:
            seq.append(from_nav)
    if to_nav and (not seq or seq[-1] != to_nav):
        seq.append(to_nav)
    # 去重相邻
    dedup: List[str] = []
    for nm in seq:
        if not dedup or dedup[-1] != nm:
            dedup.append(nm)
    return dedup
