"""读取 building 场景 JSON，解析出布局分布区域(包围盒)与墙体障碍 OBB。

building 几何(墙 ``start/end/thickness``、slab ``polygon``)单位为**米**。
``parse_building_region`` 返回局部帧(``[0,width]×[0,depth]``)下的区域尺寸与墙体 OBB；
``origin`` 偏移用于把优化器局部坐标还原回 building 世界坐标(再 ×1000 转毫米)。
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from .models import WallObstacle


def load_scene_file(path: str) -> Dict[str, Any]:
    """读取本地 building 场景 JSON，返回 ``{nodes, rootNodeIds}``；失败抛 ValueError。"""
    if not path or not os.path.exists(path):
        raise ValueError(f"场景文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"解析场景文件失败 {path}: {e}") from e
    if not isinstance(data, dict) or "nodes" not in data:
        raise ValueError(f"场景文件格式不正确(缺少 nodes): {path}")
    return data


def _xy(pt: Any) -> Optional[Tuple[float, float]]:
    """从 ``[x,y]`` 或 ``{x,y}`` 取 2D 点。"""
    if isinstance(pt, dict):
        return float(pt.get("x", 0.0)), float(pt.get("y", 0.0))
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        return float(pt[0]), float(pt[1])
    return None


def _iter_nodes(scene: Dict[str, Any]):
    nodes = scene.get("nodes") or {}
    return nodes.values() if isinstance(nodes, dict) else nodes


def parse_building_region(
    scene: Optional[Dict[str, Any]],
) -> Optional[Tuple[Tuple[float, float], float, float, List[WallObstacle]]]:
    """从 building 场景解析分布区域 + 墙体障碍(单位米)。

    优先用 ``wall`` / ``slab`` 几何的包围盒；都没有时退回 ``site`` 多边形。

    Returns:
        ``(origin, width, depth, wall_obstacles)``，解析不到任何几何时返回 ``None``。
        - ``origin = (min_x, min_y)``：局部帧原点在 building 世界坐标中的位置。
        - ``width / depth``：区域尺寸(米)。
        - ``wall_obstacles``：``list[WallObstacle]``，中心已平移到局部帧 ``[0,width]×[0,depth]``。
    """
    if not isinstance(scene, dict):
        return None

    pts: List[Tuple[float, float]] = []
    walls: List[Tuple[Tuple[float, float], Tuple[float, float], float]] = []

    for node in _iter_nodes(scene):
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype == "wall":
            s, e = _xy(node.get("start")), _xy(node.get("end"))
            if s and e:
                pts.append(s)
                pts.append(e)
                walls.append((s, e, float(node.get("thickness", 0.1) or 0.1)))
        elif ntype == "slab":
            for p in node.get("polygon") or []:
                q = _xy(p)
                if q:
                    pts.append(q)

    # wall/slab 都没有 → 退回 site 多边形
    if not pts:
        for node in _iter_nodes(scene):
            if isinstance(node, dict) and node.get("type") == "site":
                poly = node.get("polygon") or {}
                for p in (poly.get("points") if isinstance(poly, dict) else poly) or []:
                    q = _xy(p)
                    if q:
                        pts.append(q)

    if not pts:
        return None

    min_x = min(p[0] for p in pts)
    max_x = max(p[0] for p in pts)
    min_y = min(p[1] for p in pts)
    max_y = max(p[1] for p in pts)
    origin = (min_x, min_y)
    width = max(0.0, max_x - min_x)
    depth = max(0.0, max_y - min_y)

    wall_obstacles: List[WallObstacle] = []
    for s, e, thick in walls:
        length = math.hypot(e[0] - s[0], e[1] - s[1])
        if length <= 0:
            continue
        wall_obstacles.append(
            WallObstacle(
                cx=(s[0] + e[0]) / 2 - min_x,
                cy=(s[1] + e[1]) / 2 - min_y,
                length=length,
                thickness=thick,
                yaw=math.atan2(e[1] - s[1], e[0] - s[0]),
            )
        )

    return origin, width, depth, wall_obstacles
