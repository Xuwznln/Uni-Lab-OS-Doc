"""RmfNavPathCatalog 编辑/校验/重算（path_studio 后端依赖，#21 §7 P2）。

catalog 结构（`rmf_nav_path_catalog.json`）：
  meta            : source/sourceScene/pathCount/transferCount/navNodeCount/labOrigin/layoutOptimizerDir...
  navGraph        : { nodes:[{id,name,x,y}], lanes:[{v1,v2,bidirectional,speedLimit}] }（id 引用，name=nav_<k>）
  deviceDockMap   : [{instanceId,dockNav,dockX,dockY,...}]
  placementsOverlay: [{instanceId,center,footprintKey,deviceType}]
  paths           : [{pathId,fromInstance,toInstance,fromNav,toNav,navSequence:[name],weight,turnCostM,locked,geometryM}]
  transferBindings: [{transferId,pathId,taskId,readyTimeMin,deadlineMin,priority}]

注意：历史 paths 的 navSequence 是 fine 网格采样得到的 nav 点序列，**相邻两点未必是 coarse 车道相邻**，
因此 `validate_catalog_paths` 不强校验相邻（否则会拒绝既有数据）；`recompute_paths_in_catalog`
则在 navGraph 上按欧氏距离 Dijkstra 重算出一条**沿车道相邻**的路径（快，毫秒级，不碰 fine 网格）。
"""

from __future__ import annotations

import heapq
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from unilabos.sim.fleet.rmf.layout_optimizer.ingest import (
    LayoutOptimizerArtifacts,
    load_layout_optimizer_dir,
)


def _nav_index(catalog: Dict[str, Any]) -> Tuple[Dict[str, set], Dict[str, Tuple[float, float]]]:
    """从 navGraph 构建 name 邻接表 + name→(x,y)。lanes 用节点 id，需先映射成 name。"""
    ng = catalog.get("navGraph") or {}
    nodes = ng.get("nodes") or []
    id_to_name: Dict[str, str] = {str(n.get("id")): str(n.get("name")) for n in nodes}
    name_to_xy: Dict[str, Tuple[float, float]] = {
        str(n.get("name")): (float(n.get("x", 0.0)), float(n.get("y", 0.0))) for n in nodes
    }
    adj: Dict[str, set] = {str(n.get("name")): set() for n in nodes}
    for lane in ng.get("lanes") or []:
        a = id_to_name.get(str(lane.get("v1")))
        b = id_to_name.get(str(lane.get("v2")))
        if not a or not b:
            continue
        adj.setdefault(a, set()).add(b)
        if lane.get("bidirectional", True):
            adj.setdefault(b, set()).add(a)
        else:
            adj.setdefault(b, set())
    return adj, name_to_xy


def validate_catalog_paths(catalog: Dict[str, Any]) -> List[str]:
    """返回阻断性错误列表（空=通过）。宽松校验：空序列 / 未知 nav 点 / 端点不匹配。"""
    errors: List[str] = []
    _, name_to_xy = _nav_index(catalog)
    known = set(name_to_xy.keys())
    seen_ids: set[str] = set()
    for p in catalog.get("paths") or []:
        pid = str(p.get("pathId") or "?")
        if pid in seen_ids:
            errors.append(f"{pid}: pathId 重复")
        seen_ids.add(pid)
        seq = list(p.get("navSequence") or [])
        if not seq:
            errors.append(f"{pid}: navSequence 为空")
            continue
        unknown = [n for n in seq if n not in known]
        if unknown:
            errors.append(f"{pid}: 未知 nav 点 {unknown[:3]}")
        if p.get("fromNav") and seq[0] != p.get("fromNav"):
            errors.append(f"{pid}: 起点 {seq[0]} != fromNav {p.get('fromNav')}")
        if p.get("toNav") and seq[-1] != p.get("toNav"):
            errors.append(f"{pid}: 终点 {seq[-1]} != toNav {p.get('toNav')}")
    return errors


def refresh_catalog_meta(catalog: Dict[str, Any]) -> None:
    """更新 meta 统计与时间戳（保存前调用）。"""
    meta = catalog.setdefault("meta", {})
    meta["pathCount"] = len(catalog.get("paths") or [])
    meta["transferCount"] = len(catalog.get("transferBindings") or [])
    ng = catalog.get("navGraph") or {}
    meta["navNodeCount"] = len(ng.get("nodes") or [])
    meta["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load_artifacts_for_catalog(catalog: Dict[str, Any]) -> Optional[LayoutOptimizerArtifacts]:
    """按 meta.layoutOptimizerDir 加载 layout-optimizer 产物（找不到返回 None）。"""
    directory = (catalog.get("meta") or {}).get("layoutOptimizerDir")
    if not directory or not Path(directory).is_dir():
        return None
    try:
        return load_layout_optimizer_dir(directory)
    except Exception:  # noqa: BLE001
        return None


def _dijkstra(adj: Dict[str, set], xy: Dict[str, Tuple[float, float]], start: str, goal: str) -> Optional[List[str]]:
    """navGraph 上按欧氏距离最短路（毫秒级，节点量 ~727）。"""
    if start not in xy or goal not in xy:
        return None
    if start == goal:
        return [start]
    dist: Dict[str, float] = {start: 0.0}
    prev: Dict[str, Optional[str]] = {start: None}
    pq: List[Tuple[float, str]] = [(0.0, start)]
    while pq:
        d, cur = heapq.heappop(pq)
        if cur == goal:
            break
        if d > dist.get(cur, math.inf):
            continue
        cx, cy = xy[cur]
        for nxt in adj.get(cur, ()):
            if nxt not in xy:
                continue
            nx, ny = xy[nxt]
            nd = d + math.hypot(nx - cx, ny - cy)
            if nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                prev[nxt] = cur
                heapq.heappush(pq, (nd, nxt))
    if goal not in prev:
        return None
    path: List[str] = [goal]
    while prev[path[-1]] is not None:
        path.append(prev[path[-1]])  # type: ignore[arg-type]
    return list(reversed(path))


def recompute_paths_in_catalog(
    catalog: Dict[str, Any],
    artifacts: Optional[LayoutOptimizerArtifacts] = None,  # 兼容签名；coarse 重算不需要
    path_ids: Optional[List[str]] = None,
) -> int:
    """在 navGraph 上重算指定路径的 navSequence + geometryM（沿车道相邻，快）。返回更新条数。"""
    adj, xy = _nav_index(catalog)
    targets = set(path_ids) if path_ids else None
    updated = 0
    for p in catalog.get("paths") or []:
        if targets is not None and str(p.get("pathId")) not in targets:
            continue
        if p.get("locked"):
            continue
        start = p.get("fromNav") or (p.get("navSequence") or [None])[0]
        goal = p.get("toNav") or (p.get("navSequence") or [None])[-1]
        if not start or not goal:
            continue
        seq = _dijkstra(adj, xy, str(start), str(goal))
        if not seq:
            continue
        p["navSequence"] = seq
        p["geometryM"] = [[round(xy[n][0], 4), round(xy[n][1], 4)] for n in seq if n in xy]
        updated += 1
    return updated
