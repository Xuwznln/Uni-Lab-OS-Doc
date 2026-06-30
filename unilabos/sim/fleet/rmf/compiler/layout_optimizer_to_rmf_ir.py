"""layout-optimizer 产物 → RmfMapIR（#18 §9.4 / #21 §3）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfDiagnostic, RmfLaneIR, RmfLevelIR, RmfMapIR, RmfVertexIR
from unilabos.sim.fleet.rmf.layout_optimizer.dock_resolver import resolve_device_xy
from unilabos.sim.fleet.rmf.layout_optimizer.ingest import LayoutOptimizerArtifacts
from unilabos.sim.fleet.rmf.layout_optimizer.slug import build_instance_waypoint_map


def _coarse_graph(artifacts: LayoutOptimizerArtifacts) -> tuple[list[Dict], list[list[int]]]:
    cg = artifacts.aisle_network.get("coarse_graph") or {}
    nodes = list(cg.get("nodes") or [])
    edges = list(cg.get("edges") or [])
    return nodes, edges


def _snap_device_to_nav(
    level: RmfLevelIR,
    device_idx: int,
    nav_indices: Dict[int, int],
    nodes: List[Dict],
    max_dist_m: float = 2.0,
) -> None:
    """把设备 waypoint 连到最近 nav 节点（双向 lane）。"""
    if device_idx < 0 or device_idx >= len(level.vertices):
        return
    dev = level.vertices[device_idx]
    best_nav_id: Optional[int] = None
    best_dist = float("inf")
    for node in nodes:
        nid = int(node["id"])
        nav_idx = nav_indices.get(nid)
        if nav_idx is None:
            continue
        nav = level.vertices[nav_idx]
        dist = math.hypot(dev.x_m - nav.x_m, dev.y_m - nav.y_m)
        if dist < best_dist:
            best_dist = dist
            best_nav_id = nav_idx
    if best_nav_id is None or best_dist > max_dist_m:
        return
    level.lanes.append(
        RmfLaneIR(v1=device_idx, v2=best_nav_id, bidirectional=True, speed_limit=0.5, graph_idx=0)
    )


def _pair_key(a: int, b: int) -> Tuple[int, int]:
    return (min(a, b), max(a, b))


def apply_route_overrides(
    ir: RmfMapIR,
    level: RmfLevelIR,
    route_overrides: Optional[Dict[str, Any]],
    *,
    default_speed_limit: float = 0.5,
) -> None:
    """在已生成的 lanes 上应用最小路线编辑（#21 §7.0 入口 B）。

    `route_overrides` 按 **waypoint 名** 引用顶点（`nav_<id>` / `wp_<...>`）：

    ```jsonc
    {
      "disableLanes":  [["nav_0", "nav_17"]],
      "setSpeedLimit": [{"v1": "nav_0", "v2": "nav_17", "speedLimit": 0.2}],
      "addLanes":      [{"v1": "nav_17", "v2": "nav_34", "bidirectional": true, "speedLimit": 0.4}]
    }
    ```

    无法解析的 waypoint / 未命中的车道会写 `diagnostics`（warning），并汇总一条 info。
    """
    if not route_overrides:
        return

    def _resolve(name: Any) -> Optional[int]:
        idx = level.index_of(str(name))
        if idx is None:
            ir.diagnostics.append(
                RmfDiagnostic(
                    "warning",
                    "route_override_unknown_waypoint",
                    f"route_overrides 引用了不存在的 waypoint: {name}",
                    str(name),
                )
            )
        return idx

    applied = {"disable": 0, "set_speed": 0, "add": 0}

    # 1) 禁用车道（按无向对匹配）
    disable_keys: set[Tuple[int, int]] = set()
    disable_pairs: List[Tuple[int, int, str, str]] = []
    for pair in route_overrides.get("disableLanes") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        ia, ib = _resolve(pair[0]), _resolve(pair[1])
        if ia is None or ib is None:
            continue
        key = _pair_key(ia, ib)
        disable_keys.add(key)
        disable_pairs.append((key[0], key[1], str(pair[0]), str(pair[1])))
    if disable_keys:
        existing = {_pair_key(ln.v1, ln.v2) for ln in level.lanes}
        before = len(level.lanes)
        level.lanes = [ln for ln in level.lanes if _pair_key(ln.v1, ln.v2) not in disable_keys]
        applied["disable"] = before - len(level.lanes)
        for a, b, an, bn in disable_pairs:
            if (a, b) not in existing:
                ir.diagnostics.append(
                    RmfDiagnostic(
                        "warning", "route_override_no_match", f"disableLanes 未命中已有车道: {an}-{bn}", an
                    )
                )

    # 2) 改限速（命中的所有同对 lane 都改）
    for item in route_overrides.get("setSpeedLimit") or []:
        if not isinstance(item, dict):
            continue
        ia, ib = _resolve(item.get("v1")), _resolve(item.get("v2"))
        if ia is None or ib is None:
            continue
        key = _pair_key(ia, ib)
        spd = float(item.get("speedLimit", default_speed_limit))
        matched = False
        for ln in level.lanes:
            if _pair_key(ln.v1, ln.v2) == key:
                ln.speed_limit = spd
                matched = True
        if matched:
            applied["set_speed"] += 1
        else:
            ir.diagnostics.append(
                RmfDiagnostic(
                    "warning",
                    "route_override_no_match",
                    f"setSpeedLimit 未命中车道: {item.get('v1')}-{item.get('v2')}",
                    str(item.get("v1")),
                )
            )

    # 3) 新增车道（已存在则跳过）
    for item in route_overrides.get("addLanes") or []:
        if not isinstance(item, dict):
            continue
        ia, ib = _resolve(item.get("v1")), _resolve(item.get("v2"))
        if ia is None or ib is None:
            continue
        key = _pair_key(ia, ib)
        if any(_pair_key(ln.v1, ln.v2) == key for ln in level.lanes):
            ir.diagnostics.append(
                RmfDiagnostic(
                    "info",
                    "route_override_lane_exists",
                    f"addLanes 已存在车道，跳过: {item.get('v1')}-{item.get('v2')}",
                    str(item.get("v1")),
                )
            )
            continue
        level.lanes.append(
            RmfLaneIR(
                v1=ia,
                v2=ib,
                bidirectional=bool(item.get("bidirectional", True)),
                speed_limit=float(item.get("speedLimit", default_speed_limit)),
                graph_idx=0,
                orientation=str(item.get("orientation", "")),
            )
        )
        applied["add"] += 1

    if any(applied.values()):
        ir.diagnostics.append(
            RmfDiagnostic(
                "info",
                "route_override_applied",
                f"route_overrides 生效: 禁用 {applied['disable']} / 改限速 {applied['set_speed']} / 新增 {applied['add']}",
            )
        )


def connect_lane_components(level: RmfLevelIR, *, speed_limit: float = 0.5) -> int:
    """把不连通的车道分量用「最近顶点对」桥接成单一连通图（#21 §7：让 RMF 能跨分量规划）。

    coarse_graph 在按车身腐蚀后常裂成多个分量（fine 网格其实连通），导致机器人到不了大多数
    设备 dock、车队不出价。这里迭代地把离已连通集合最近的分量并入，新增双向 lane。返回新增数。
    """
    n = len(level.vertices)
    if n <= 1:
        return 0
    adj: Dict[int, set] = {i: set() for i in range(n)}
    for ln in level.lanes:
        adj[ln.v1].add(ln.v2)
        adj[ln.v2].add(ln.v1)

    def _components() -> List[List[int]]:
        seen: set[int] = set()
        comps: List[List[int]] = []
        for s in range(n):
            if s in seen:
                continue
            stack = [s]
            seen.add(s)
            cur: List[int] = []
            while stack:
                u = stack.pop()
                cur.append(u)
                for w in adj[u]:
                    if w not in seen:
                        seen.add(w)
                        stack.append(w)
            comps.append(cur)
        return comps

    added = 0
    comps = _components()
    while len(comps) > 1:
        comps.sort(key=len, reverse=True)
        base = comps[0]
        others = [u for c in comps[1:] for u in c]
        best: Optional[Tuple[float, int, int]] = None
        for b in others:
            vb = level.vertices[b]
            for a in base:
                va = level.vertices[a]
                d = math.hypot(va.x_m - vb.x_m, va.y_m - vb.y_m)
                if best is None or d < best[0]:
                    best = (d, a, b)
        if best is None:
            break
        _, a, b = best
        level.lanes.append(RmfLaneIR(v1=a, v2=b, bidirectional=True, speed_limit=speed_limit, graph_idx=0))
        adj[a].add(b)
        adj[b].add(a)
        added += 1
        comps = _components()
    return added


def _rect_dir(a: Tuple[float, float], b: Tuple[float, float], eps: float = 1e-6) -> Tuple[int, int]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    sx = (dx > eps) - (dx < -eps)
    sy = (dy > eps) - (dy < -eps)
    return (sx, sy)


def _simplify_corners(pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """直角折线只保留拐点（去掉共线中间点），不改变几何。"""
    if len(pts) <= 2:
        return list(pts)
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        if _rect_dir(pts[i - 1], pts[i]) != _rect_dir(pts[i], pts[i + 1]):
            out.append(pts[i])
    out.append(pts[-1])
    return out


def build_ir_from_agv_routes(
    routes_doc: Dict[str, Any],
    *,
    lab_uuid: str = "",
    scene_hash: str = "",
    building_name: str = "building",
    default_level: str = "L1",
    speed_limit: float = 0.5,
    grid_snap_m: float = 0.05,
    connect_components: bool = True,
) -> RmfMapIR:
    """`rmf_agv_routes.json`（黑点 + 直角折线轨迹，#18 §10.6）→ RmfMapIR（**黑点导航图，无 nav_***）。

    - 顶点：命名黑点 `dock_*`（设备接驳点，rmf-web 中唯一可选目标点）+ 走廊折线拐点（无名，仅连通用）。
    - lane：来自 `routes[].geometryM` 的直角折线段（拐点间），相同坐标按 `grid_snap_m` 网格去重共享。
    - 不再生成任何 `nav_*` 顶点 / coarse 网格 lane。
    """
    ir = RmfMapIR(
        lab_uuid=lab_uuid,
        scene_hash=scene_hash,
        building_name=building_name,
        coordinate_system="cartesian_meters",
    )
    level = RmfLevelIR(name=default_level, elevation=0.0)
    ir.levels.append(level)

    waypoints = routes_doc.get("waypoints") or []
    routes = routes_doc.get("routes") or []

    def _key(x: float, y: float) -> Tuple[int, int]:
        return (round(float(x) / grid_snap_m), round(float(y) / grid_snap_m))

    key_to_idx: Dict[Tuple[int, int], int] = {}
    waypoint_to_instance: Dict[str, str] = {}

    # 1) 命名黑点（设备 dock）—— 唯一可选导航/目标点
    for wp in waypoints:
        name = str(wp.get("name") or "")
        if not name:
            continue
        x, y = float(wp.get("x", 0.0)), float(wp.get("y", 0.0))
        kind = str(wp.get("kind") or "device_dock")
        params: Dict[str, Any]
        if kind == "turn_star":
            params = {
                "is_holding_point": True,
            }
        else:
            params = {
                "is_holding_point": True,
                "pickup_dispenser": str(wp.get("pickupDispenser") or f"d_{name}"),
                "dropoff_ingestor": str(wp.get("dropoffIngestor") or f"i_{name}"),
            }
        idx = level.add_vertex(
            RmfVertexIR(
                name=name,
                x_m=x,
                y_m=y,
                z_m=0.0,
                params=params,
            )
        )
        key_to_idx.setdefault(_key(x, y), idx)
        iid = wp.get("instanceId")
        if iid:
            waypoint_to_instance[name] = str(iid)

    # 2) 走廊折线拐点（无名）+ 直角折线 lane（来自 geometryM）
    def _get_or_add(x: float, y: float) -> int:
        k = _key(x, y)
        hit = key_to_idx.get(k)
        if hit is not None:
            return hit
        idx = level.add_vertex(RmfVertexIR(name="", x_m=float(x), y_m=float(y), z_m=0.0, params={}))
        key_to_idx[k] = idx
        return idx

    seen_lanes: set[Tuple[int, int]] = set()
    for r in routes:
        geom = r.get("geometryM") or []
        pts = [(float(p[0]), float(p[1])) for p in geom if isinstance(p, (list, tuple)) and len(p) >= 2]
        pts = _simplify_corners(pts)
        prev: Optional[int] = None
        for x, y in pts:
            idx = _get_or_add(x, y)
            if prev is not None and prev != idx:
                lk = (min(prev, idx), max(prev, idx))
                if lk not in seen_lanes:
                    seen_lanes.add(lk)
                    level.lanes.append(
                        RmfLaneIR(v1=prev, v2=idx, bidirectional=True, speed_limit=speed_limit, graph_idx=0)
                    )
            prev = idx

    # 3) 连通性修复：孤立黑点 / 分量桥接成单一连通图（否则 RMF 跨分量无法规划）
    if connect_components:
        bridges = connect_lane_components(level, speed_limit=speed_limit)
        if bridges:
            ir.diagnostics.append(
                RmfDiagnostic(
                    "info", "lane_components_bridged", f"黑点图桥接 {bridges} 条 lane（连成单一连通图）"
                )
            )

    named = sum(1 for v in level.vertices if v.name)
    ir.diagnostics.append(
        RmfDiagnostic(
            "info",
            "black_dot_map",
            f"黑点导航图：命名黑点 {named} / 走廊节点 {len(level.vertices) - named} / lane {len(level.lanes)}（无 nav_*）",
        )
    )

    ir._waypoint_device_uuid = {}  # type: ignore[attr-defined]
    ir._restricted_zones = []  # type: ignore[attr-defined]
    ir._waypoint_to_instance = waypoint_to_instance  # type: ignore[attr-defined]
    return ir


def build_layout_optimizer_rmf_ir(
    artifacts: LayoutOptimizerArtifacts,
    *,
    lab_uuid: str = "",
    scene_hash: str = "",
    building_name: str = "building",
    default_level: str = "L1",
    include_coarse_nav: bool = True,
    snap_devices_to_nav: bool = True,
    nav_speed_limit: float = 0.5,
    route_overrides: Optional[Dict[str, Any]] = None,
    connect_components: bool = True,
) -> RmfMapIR:
    """placements + aisle_network → RmfMapIR（米制 cartesian_meters）。"""
    ir = RmfMapIR(
        lab_uuid=lab_uuid,
        scene_hash=scene_hash,
        building_name=building_name,
        coordinate_system="cartesian_meters",
    )
    level = RmfLevelIR(name=default_level, elevation=0.0)
    ir.levels.append(level)

    waypoint_map = build_instance_waypoint_map(artifacts.placements)
    placement_by_id = {str(p["instance_id"]): p for p in artifacts.placements if p.get("instance_id")}
    waypoint_to_instance: Dict[str, str] = {v: k for k, v in waypoint_map.items()}
    device_indices: Dict[str, int] = {}

    # 1) 设备接驳 waypoints
    for iid, wp_name in sorted(waypoint_map.items()):
        placement = placement_by_id.get(iid, {})
        x, y = resolve_device_xy(placement) if placement else (0.0, 0.0)
        pickup = f"d_{wp_name}"
        dropoff = f"i_{wp_name}"
        idx = level.add_vertex(
            RmfVertexIR(
                name=wp_name,
                x_m=x,
                y_m=y,
                z_m=0.0,
                params={
                    "is_holding_point": True,
                    "pickup_dispenser": pickup,
                    "dropoff_ingestor": dropoff,
                },
            )
        )
        device_indices[iid] = idx

    # 2) coarse nav 网格 → vertices + lanes
    nav_node_id_to_idx: Dict[int, int] = {}
    if include_coarse_nav:
        nodes, edges = _coarse_graph(artifacts)
        for node in nodes:
            nid = int(node["id"])
            nav_name = f"nav_{nid}"
            nav_idx = level.add_vertex(
                RmfVertexIR(
                    name=nav_name,
                    x_m=float(node["x"]),
                    y_m=float(node["y"]),
                    z_m=0.0,
                    params={},
                )
            )
            nav_node_id_to_idx[nid] = nav_idx

        seen_edges: set[tuple[int, int]] = set()
        for edge in edges:
            if len(edge) != 2:
                continue
            a, b = int(edge[0]), int(edge[1])
            ia, ib = nav_node_id_to_idx.get(a), nav_node_id_to_idx.get(b)
            if ia is None or ib is None:
                continue
            key = (min(ia, ib), max(ia, ib))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            level.lanes.append(
                RmfLaneIR(v1=ia, v2=ib, bidirectional=True, speed_limit=nav_speed_limit, graph_idx=0)
            )

        if snap_devices_to_nav and nav_node_id_to_idx:
            nodes_list, _ = _coarse_graph(artifacts)
            for iid, dev_idx in device_indices.items():
                _snap_device_to_nav(level, dev_idx, nav_node_id_to_idx, nodes_list)

    missing_dock = [iid for iid in waypoint_map if iid not in placement_by_id]
    for iid in missing_dock:
        ir.diagnostics.append(
            RmfDiagnostic("warning", "missing_placement", f"transfers 引用但 placements 缺失: {iid}", iid)
        )

    # 最小路线编辑入口（#21 §7.0 入口 B）：在生成的 lanes 上增删/禁用/改限速
    apply_route_overrides(ir, level, route_overrides, default_speed_limit=nav_speed_limit)

    # 连通性修复：把 coarse_graph 裂开的多个分量桥接成单一连通图（否则 RMF 跨分量无法规划）
    if connect_components:
        bridges = connect_lane_components(level, speed_limit=nav_speed_limit)
        if bridges:
            ir.diagnostics.append(
                RmfDiagnostic(
                    "info",
                    "lane_components_bridged",
                    f"桥接不连通分量，新增 {bridges} 条连通 lane（nav_graph 连成单一连通图）",
                )
            )

    ir._waypoint_device_uuid = {}  # type: ignore[attr-defined]
    ir._restricted_zones = []  # type: ignore[attr-defined]
    ir._waypoint_to_instance = waypoint_to_instance  # type: ignore[attr-defined]
    ir._layout_optimizer_dir = str(artifacts.directory)  # type: ignore[attr-defined]
    return ir


def merge_transfer_plan_into_semantic(
    semantic: Dict[str, Any],
    transfer_plan: Dict[str, Any],
    artifacts: LayoutOptimizerArtifacts,
) -> Dict[str, Any]:
    """把 transfer plan 引用写入 semantic_map（#18 §9.6）。"""
    waypoint_to_instance = {wp["waypointName"]: wp["instanceId"] for wp in transfer_plan.get("deviceWaypoints") or []}
    semantic = dict(semantic)
    semantic["waypoint_to_instance"] = waypoint_to_instance
    meta = transfer_plan.get("meta") or {}
    semantic["transfer_plan_ref"] = {
        "source": "layout_optimizer",
        "directory": str(artifacts.directory),
        "source_scene": meta.get("sourceScene"),
        "transfer_count": meta.get("transferCount"),
        "makespan_min": meta.get("makespanMin"),
    }
    return semantic
