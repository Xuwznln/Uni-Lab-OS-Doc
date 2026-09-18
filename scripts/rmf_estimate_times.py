#!/usr/bin/env python3
"""脱离 edge，用 RMF 估算多机规划 makespan（每段时间 + 总时间）→ JSON + 自包含 HTML 报告。

设计见 product_designs/simulation_assets_and_embodied_AI/24.9-rmf-route-time-estimation-standalone.md

工作方式（RMF 驱动，连续、无瞬移、沿 nav_graph=designer 的 A-Y-Y'-B 走廊）：
- 默认（实时避让 / traffic-accurate）：不 cancel，轮询到 task 终态收实际 finish
  （含车-车避让/等待，适合看真实交通冲突成本）。
- --planned-only（可选）：仅做分配 + 排队估时；不执行任务，读估时后 cancel（更快，但不含实时避让）。

注：曾有 --per-route（单条逐路估时）已移除——RMF 约束下要么须把车瞬移到 route 起点（禁止），要么
    绕开 RMF 纯几何（违背"用 RMF 估算"）；不瞬移则退化成 multi 的顺序驱动，故不再单列（见 24.9 §8）。

用法：
    conda activate unilab
    python scripts/rmf_estimate_times.py --paths ../.rmf_run_logs/maps/latest/rmf_transfer_paths.json \
        --robots 3 --max 20 --out ../.rmf_run_logs/maps/latest/rmf_time_estimates.json
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]  # .../Uni-Lab-OS
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from unilabos.sim.fleet.rmf.task_dispatcher import (  # noqa: E402
    build_delivery_request,
    build_go_to_request,
    build_patrol_request,
)
from unilabos.sim.fleet.rmf.runtime.launcher import (  # noqa: E402
    RmfRuntimeLauncher,
    RmfRuntimeOptions,
)

# 与 rmf_os_read_tasks.py / rmf_dispatch_transfer_paths.py 同款 stub JWT（本地 api-server 鉴权）。
JWT_DEFAULT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJzdHViIiwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWRtaW4iLCJpYXQiOjE1MTYyMzkwMjIsImF1ZCI6InJtZl9hcGlfc2VydmVyIiwiaXNzIjoic3R1YiIsImV4cCI6MjA1MTIyMjQwMH0."
    "zzX3zXp467ldkzmLVIadQ_AHr8M5uWVV43n4wEB0OhE"
)

# fleet_config 车辆参数默认（与 .rmf_run_logs/maps/latest/fleet_config.yaml 一致，仅作 fallback）
FLEET_DEFAULTS = {
    "maxLinearSpeed": 1.5,
    "linearAccel": 0.75,
    "maxAngularSpeed": 0.6,
    "angularAccel": 2.0,
    "footprint": 0.35,
}


# ============================================================ REST 小工具
def _headers(token: str) -> Dict[str, str]:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _get(api: str, path: str, token: str, timeout: float = 15.0) -> Any:
    resp = requests.get(f"{api.rstrip('/')}{path}", headers=_headers(token), timeout=timeout)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _post(api: str, path: str, body: Any, token: str, timeout: float = 20.0) -> Tuple[int, Any]:
    resp = requests.post(f"{api.rstrip('/')}{path}", json=body, headers=_headers(token), timeout=timeout)
    try:
        data = resp.json() if resp.content else {}
    except Exception:  # noqa: BLE001
        data = {"_raw": resp.text[:400]}
    return resp.status_code, data


def api_reachable(api: str, token: str) -> bool:
    try:
        requests.get(f"{api.rstrip('/')}/tasks?limit=1", headers=_headers(token), timeout=4)
        return True
    except Exception:  # noqa: BLE001
        return False


def edge_state(edge_url: str, robot: str) -> Optional[Dict[str, Any]]:
    """GET {edge}/agv/state?robot= → mock 车实时位姿（边缘帧），失败 None。"""
    try:
        r = requests.get(f"{edge_url.rstrip('/')}/agv/state", params={"robot": robot}, timeout=2)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def tasks_batch(api: str, token: str, task_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """GET /tasks?limit=1000 → {booking.id: TaskState}（仅保留关心的 task_ids）。"""
    want = set(task_ids)
    out: Dict[str, Dict[str, Any]] = {}
    try:
        rows = _get(api, "/tasks?limit=1000", token, timeout=12.0)
        rows = rows.get("tasks") if isinstance(rows, dict) else rows
        for row in rows or []:
            if isinstance(row, dict):
                bid = str((row.get("booking") or {}).get("id") or "")
                if bid in want:
                    out[bid] = row
    except Exception:  # noqa: BLE001
        pass
    return out


def query_fleet_robots(api: str, token: str) -> Dict[str, List[str]]:
    """GET /fleets → {fleet_name: [robot_name, ...]}。失败返回空。"""
    try:
        fleets = _get(api, "/fleets", token) or []
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, List[str]] = {}
    for f in fleets if isinstance(fleets, list) else []:
        out[str(f.get("name") or "?")] = sorted((f.get("robots") or {}).keys())
    return out


def wait_for_fleet(api: str, token: str, *, min_robots: int = 1, timeout_s: float = 150.0, poll_s: float = 3.0) -> Dict[str, List[str]]:
    """轮询 GET /fleets 直到 ≥min_robots 台车注册（edge/fleet_adapter 起来要时间）或超时。"""
    deadline = time.time() + max(2.0, timeout_s)
    fleets: Dict[str, List[str]] = {}
    while time.time() < deadline:
        fleets = query_fleet_robots(api, token)
        if sum(len(v) for v in fleets.values()) >= min_robots:
            return fleets
        time.sleep(poll_s)
    return fleets


_ROBOT_TASK_FALLBACK_WARNED = False


def _booking_id_from_body(body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    state = body.get("state") if isinstance(body.get("state"), dict) else body
    bid = str(((state or {}).get("booking") or {}).get("id") or "")
    return bid or None


def dispatch_envelope(api: str, token: str, env: Dict[str, Any]) -> Optional[str]:
    """下发信封，返回 booking id。

    优先 robot_task_request（直派，无竞标）；若接口不可用/超时，自动降级到 dispatch_task_request。
    """
    global _ROBOT_TASK_FALLBACK_WARNED
    if env.get("type") == "robot_task_request":
        code, body = _post(api, "/tasks/robot_task", env, token)
        bid = _booking_id_from_body(body) if code == 200 else None
        if bid:
            return bid
        # 回退：当前 api-server 可能未暴露 robot_task（实测会 500 timeout）
        req = dict(env.get("request") or {})
        fleet = str(env.get("fleet") or "").strip()
        if fleet:
            req.setdefault("fleet_name", fleet)
        fb_env = {"type": "dispatch_task_request", "request": req}
        fb_code, fb_body = _post(api, "/tasks/dispatch_task", fb_env, token)
        fb_bid = _booking_id_from_body(fb_body) if fb_code == 200 else None
        if fb_bid and not _ROBOT_TASK_FALLBACK_WARNED:
            _ROBOT_TASK_FALLBACK_WARNED = True
            print("[warn] /tasks/robot_task 不可用，已自动回退到 /tasks/dispatch_task（可能受竞标窗口影响）", flush=True)
        return fb_bid

    code, body = _post(api, "/tasks/dispatch_task", env, token)
    return _booking_id_from_body(body) if code == 200 else None


def query_task(api: str, token: str, task_id: str) -> Optional[Dict[str, Any]]:
    """GET /tasks/{id} → TaskState；fallback GET /tasks?limit=N 按 booking.id 匹配。"""
    tid = str(task_id or "").strip()
    if not tid:
        return None
    try:
        got = _get(api, f"/tasks/{tid}", token, timeout=8.0)
        if isinstance(got, dict) and str((got.get("booking") or {}).get("id") or "") == tid:
            return got
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = _get(api, "/tasks?limit=1000", token, timeout=12.0)
        rows = rows.get("tasks") if isinstance(rows, dict) else rows
        for row in rows or []:
            if isinstance(row, dict) and str((row.get("booking") or {}).get("id") or "") == tid:
                return row
    except Exception:  # noqa: BLE001
        return None
    return None


def cancel_task(api: str, token: str, task_id: str) -> bool:
    try:
        code, _ = _post(api, "/tasks/cancel_task", {"type": "cancel_task_request", "task_id": task_id}, token, timeout=6.0)
        return code == 200
    except Exception:  # noqa: BLE001
        return False


# ============================================================ TaskState 解析
_TERMINAL = {"completed", "done", "failed", "canceled", "cancelled", "killed", "skipped"}


def _assigned_robot(state: Dict[str, Any]) -> str:
    a = state.get("assigned_to") if isinstance(state.get("assigned_to"), dict) else {}
    return str(a.get("name") or "")


def _is_planned(state: Dict[str, Any]) -> bool:
    """已被指派且有完成时刻估计 → 分配+排队估时就绪（无需执行）。"""
    return bool(_assigned_robot(state)) and bool(state.get("unix_millis_finish_time"))


def _finish_ms(state: Dict[str, Any]) -> Optional[int]:
    v = state.get("unix_millis_finish_time")
    return int(v) if isinstance(v, (int, float)) and v else None


def _estimate_sec(state: Dict[str, Any]) -> Optional[float]:
    for key in ("original_estimate_millis", "estimate_millis"):
        v = state.get(key)
        if isinstance(v, (int, float)) and v:
            return round(float(v) / 1000.0, 1)
    return None


# ============================================================ 输入加载
def _load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _transfer_endpoint_keys(tr: Dict[str, Any]) -> Tuple[str, str]:
    """兼容多种 transfer 字段命名，取源/目的实例键。"""
    from_key = str(
        tr.get("fromInstance")
        or tr.get("from_device")
        or tr.get("fromDevice")
        or tr.get("from")
        or ""
    ).strip()
    to_key = str(
        tr.get("toInstance")
        or tr.get("to_device")
        or tr.get("toDevice")
        or tr.get("to")
        or ""
    ).strip()
    return from_key, to_key


def _transfer_dock_endpoints(tr: Dict[str, Any], dock_map: Dict[str, str]) -> Tuple[Optional[str], Optional[str]]:
    """兼容多种 transfer 字段命名，解析 RMF dock 起终点。"""
    from_key, to_key = _transfer_endpoint_keys(tr)
    return dock_map.get(from_key), dock_map.get(to_key)


def _resolve_routes_doc(args: argparse.Namespace) -> Dict[str, Any]:
    """multi 模式取 rmf_agv_routes.json（instanceId→dock 名映射用）：--routes 优先，否则 map-dir 下。"""
    if args.routes and Path(args.routes).exists():
        return _load_json(args.routes)
    cand = Path(args.map_dir) / "rmf_agv_routes.json"
    return _load_json(str(cand)) if cand.exists() else {}


def _instance_dock_map(routes_doc: Dict[str, Any]) -> Dict[str, str]:
    """rmf_agv_routes.json waypoints[].instanceId → name（RMF nav_graph 里的 dock 名）。"""
    out: Dict[str, str] = {}
    for w in routes_doc.get("waypoints") or []:
        iid = str(w.get("instanceId") or "").strip()
        name = str(w.get("name") or "").strip()
        if iid and name:
            out[iid] = name
    return out


def load_fleet_params(map_dir: str, args: argparse.Namespace) -> Dict[str, Any]:
    """读 fleet_config.yaml 默认 + CLI 覆盖 → 报告用的车辆参数（"requested"）。"""
    params = dict(FLEET_DEFAULTS)
    cfg_path = Path(map_dir) / "fleet_config.yaml"
    used = "FLEET_DEFAULTS"
    if cfg_path.exists():
        try:
            import yaml

            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            limits = ((cfg.get("rmf_fleet") or {}).get("limits") or {})
            lin = limits.get("linear") or []
            ang = limits.get("angular") or []
            if len(lin) >= 2:
                params["maxLinearSpeed"], params["linearAccel"] = float(lin[0]), float(lin[1])
            if len(ang) >= 2:
                params["maxAngularSpeed"], params["angularAccel"] = float(ang[0]), float(ang[1])
            fp = ((cfg.get("rmf_fleet") or {}).get("profile") or {}).get("footprint")
            if fp is not None:
                params["footprint"] = float(fp)
            used = str(cfg_path)
        except Exception:  # noqa: BLE001
            pass
    # CLI 覆盖
    for cli, key in (
        ("max_linear_speed", "maxLinearSpeed"),
        ("linear_accel", "linearAccel"),
        ("max_angular_speed", "maxAngularSpeed"),
        ("angular_accel", "angularAccel"),
        ("footprint", "footprint"),
    ):
        v = getattr(args, cli, None)
        if v is not None:
            params[key] = float(v)
    params["usedDefaultsFrom"] = used
    return params


# ============================================================ 派发 + 轮询
def _wait_until(
    api: str,
    token: str,
    task_ids: List[str],
    *,
    predicate,
    timeout_s: float,
    poll_s: float = 1.5,
) -> Dict[str, Dict[str, Any]]:
    """轮询 task_ids 直到全部满足 predicate(state) 或超时；返回 {tid: state(最近一次)}。"""
    states: Dict[str, Dict[str, Any]] = {}
    deadline = time.time() + max(2.0, timeout_s)
    pending = set(task_ids)
    while pending and time.time() < deadline:
        # 批量抓取 task 态（单次 /tasks?limit=1000），避免大批量 task 时逐条 GET 过慢。
        batch = tasks_batch(api, token, list(pending))
        if batch:
            for tid, st in batch.items():
                states[tid] = st
                if predicate(st):
                    pending.discard(tid)
        else:
            # 兜底：批量失败时至少抽样更新一部分，避免完全停摆。
            for tid in list(pending)[:20]:
                st = query_task(api, token, tid)
                if st is not None:
                    states[tid] = st
                    if predicate(st):
                        pending.discard(tid)
        if pending:
            time.sleep(poll_s)
    return states


# ============================================================ 可视化数据（--execute 交通级回放）
def _fit_linear(xs: List[float], ys: List[float]) -> Optional[Tuple[float, float]]:
    """最小二乘线性拟合 y = a*x + b。"""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    den = n * sxx - sx * sx
    if abs(den) < 1e-12:
        return None
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    return a, b


def _png_size(path: Path) -> Optional[Tuple[int, int]]:
    """读 PNG 宽高（无需 Pillow）。"""
    try:
        raw = path.read_bytes()
    except Exception:  # noqa: BLE001
        return None
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w = int.from_bytes(raw[16:20], "big")
    h = int.from_bytes(raw[20:24], "big")
    return (w, h) if w > 0 and h > 0 else None


def _layout_overlay(map_dir: str, nav_by_name: Dict[str, Tuple[float, float]]) -> Optional[Dict[str, Any]]:
    """读取 floorplan 并估计像素→边缘帧映射，返回可视化底图叠加信息。"""
    import yaml

    md_path = Path(map_dir) / "manifest.json"
    floor_path = Path(map_dir) / "L1_floorplan.png"
    building_path = Path(map_dir) / "building.yaml"
    if md_path.exists():
        try:
            md = json.loads(md_path.read_text(encoding="utf-8")) or {}
            fp = str(md.get("floorplan") or "").strip()
            by = str(md.get("building_yaml") or "").strip()
            if fp:
                floor_path = Path(fp)
            if by:
                building_path = Path(by)
        except Exception:  # noqa: BLE001
            pass
    if not floor_path.exists():
        return None

    size = _png_size(floor_path)
    if not size:
        return None
    img_w, img_h = size

    # 默认值（布局优化这套图通常是 x=0.1*px, y=-0.1*py）
    x_a, x_b = 0.1, 0.0
    y_a, y_b = -0.1, 0.0
    samples = 0

    if building_path.exists() and nav_by_name:
        try:
            b = yaml.safe_load(building_path.read_text(encoding="utf-8")) or {}
            verts = (((b.get("levels") or {}).get("L1") or {}).get("vertices") or [])
            pxs: List[float] = []
            xws: List[float] = []
            pys: List[float] = []
            yws: List[float] = []
            for v in verts:
                if not (isinstance(v, list) and len(v) >= 4 and isinstance(v[3], str) and v[3]):
                    continue
                nm = str(v[3])
                if nm not in nav_by_name:
                    continue
                px, py = float(v[0]), float(v[1])
                xw, yw = nav_by_name[nm]
                pxs.append(px)
                xws.append(xw)
                pys.append(py)
                yws.append(yw)
            fx = _fit_linear(pxs, xws)
            fy = _fit_linear(pys, yws)
            if fx and fy:
                x_a, x_b = fx
                y_a, y_b = fy
                samples = len(pxs)
        except Exception:  # noqa: BLE001
            pass

    corners = [(0.0, 0.0), (float(img_w), 0.0), (0.0, float(img_h)), (float(img_w), float(img_h))]
    wx = [x_a * cx + x_b for cx, _ in corners]
    wy = [y_a * cy + y_b for _, cy in corners]
    bounds = {"minX": round(min(wx), 3), "maxX": round(max(wx), 3), "minY": round(min(wy), 3), "maxY": round(max(wy), 3)}

    mime = "image/png"
    raw = floor_path.read_bytes()
    uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return {
        "imageDataUri": uri,
        "bounds": bounds,
        "opacity": 0.62,
        "sizePx": {"w": img_w, "h": img_h},
        "transform": {"xScale": round(x_a, 6), "xBias": round(x_b, 6), "yScale": round(y_a, 6), "yBias": round(y_b, 6),
                      "samplePoints": samples},
        "source": str(floor_path),
    }


def _map_schematic(map_dir: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], Dict[str, float]]:
    """从 nav_graph + semantic_map + floorplan 提取示意图点、设备点、底图与包围盒。

    kind: dock（工位停靠点 dock_*）| turn（星点/走廊/转折点，其余顶点）。
    """
    pts: List[Dict[str, Any]] = []
    devices: List[Dict[str, Any]] = []
    xs: List[float] = []
    ys: List[float] = []
    layout: Dict[str, Any] = {}
    try:
        import yaml

        g = yaml.safe_load((Path(map_dir) / "nav_graphs" / "0.yaml").read_text(encoding="utf-8"))
        by_name: Dict[str, Tuple[float, float]] = {}
        for v in g["levels"]["L1"]["vertices"]:
            x, y = float(v[0]), float(v[1])
            name = ""
            if len(v) > 2 and isinstance(v[2], dict):
                name = str(v[2].get("name") or "")
            kind = "dock" if name.startswith("dock_") else "turn"
            pts.append({"name": name, "x": round(x, 3), "y": round(y, 3), "kind": kind})
            if name:
                by_name[name] = (x, y)
            xs.append(x)
            ys.append(y)

        # 设备点位：优先来自 semantic_map waypoint_to_instance（设备名），坐标取 nav_graph 对应 dock_* 顶点。
        sm = {}
        sm_path = Path(map_dir) / "semantic_map.json"
        if sm_path.exists():
            sm = json.loads(sm_path.read_text(encoding="utf-8")) or {}
        wp2inst = sm.get("waypoint_to_instance") if isinstance(sm, dict) else {}
        for wp, inst in (wp2inst.items() if isinstance(wp2inst, dict) else []):
            if not isinstance(wp, str) or not wp:
                continue
            dock_name = f"dock_{wp[3:]}" if wp.startswith("wp_") else wp
            pos = by_name.get(dock_name) or by_name.get(wp)
            if pos is None:
                continue
            x, y = pos
            devices.append({
                "name": str(inst or dock_name),
                "waypoint": wp,
                "dock": dock_name,
                "x": round(x, 3),
                "y": round(y, 3),
            })

        lay = _layout_overlay(map_dir, by_name)
        if lay:
            layout = lay
            lb = lay.get("bounds") if isinstance(lay, dict) else None
            if isinstance(lb, dict):
                xs += [float(lb.get("minX", 0.0)), float(lb.get("maxX", 0.0))]
                ys += [float(lb.get("minY", 0.0)), float(lb.get("maxY", 0.0))]
    except Exception:  # noqa: BLE001
        pass
    bounds = {
        "minX": round(min(xs), 2) if xs else 0.0, "maxX": round(max(xs), 2) if xs else 1.0,
        "minY": round(min(ys), 2) if ys else 0.0, "maxY": round(max(ys), 2) if ys else 1.0,
    }
    return pts, devices, layout, bounds


def _ang_diff(a: float, b: float) -> float:
    d = a - b
    while d > math.pi:
        d -= 2.0 * math.pi
    while d < -math.pi:
        d += 2.0 * math.pi
    return abs(d)


def _motion_in_window(samples: List[List[float]], start_t: float, end_t: float) -> Tuple[float, float]:
    """统计时间窗内累计位移与累计转角。"""
    win = [s for s in samples if start_t <= s[0] <= end_t]
    if len(win) < 2:
        return 0.0, 0.0
    pos = 0.0
    yaw = 0.0
    for i in range(1, len(win)):
        pos += math.hypot(win[i][1] - win[i - 1][1], win[i][2] - win[i - 1][2])
        yaw += _ang_diff(float(win[i][3]), float(win[i - 1][3]))
    return pos, yaw


def _traj_distance(samples: List[List[float]]) -> float:
    """轨迹折线长度（米）。"""
    if len(samples) < 2:
        return 0.0
    dist = 0.0
    for i in range(1, len(samples)):
        dist += math.hypot(samples[i][1] - samples[i - 1][1], samples[i][2] - samples[i - 1][2])
    return dist


def _nearest_dock_hit(
    samples: List[List[float]],
    dock_xy: Dict[str, Tuple[float, float]],
    *,
    start_idx: int = 0,
    dock_eps: float = 0.45,
) -> Optional[Tuple[int, str]]:
    """在 samples[start_idx:] 中找首个靠近任一 dock 的样本。"""
    if not samples or not dock_xy:
        return None
    for i in range(max(0, start_idx), len(samples)):
        x, y = samples[i][1], samples[i][2]
        for name, (dx, dy) in dock_xy.items():
            if math.hypot(x - dx, y - dy) <= dock_eps:
                return i, name
    return None


def _split_task_samples(
    samples: List[List[float]],
    *,
    from_dock: str,
    to_dock: str,
    dock_xy: Dict[str, Tuple[float, float]],
    dock_eps: float = 0.45,
    move_eps: float = 0.08,
) -> Tuple[List[List[float]], List[List[float]]]:
    """把任务窗轨迹切成 [补位段, 任务执行段]（与前端 splitTaskPath 同逻辑）。"""
    if len(samples) < 2:
        return [], samples
    i_a = 0
    i_b = len(samples) - 1
    from_xy = dock_xy.get(from_dock)
    to_xy = dock_xy.get(to_dock)

    if from_xy is not None:
        fx, fy = from_xy
        for i, s in enumerate(samples):
            if math.hypot(s[1] - fx, s[2] - fy) <= dock_eps:
                i_a = i
                break
    else:
        hit = _nearest_dock_hit(samples, dock_xy, start_idx=0, dock_eps=dock_eps)
        if hit is not None:
            i_a = hit[0]

    if to_xy is not None:
        tx, ty = to_xy
        for i in range(max(0, i_a), len(samples)):
            s = samples[i]
            if math.hypot(s[1] - tx, s[2] - ty) <= dock_eps:
                i_b = i
                break
    else:
        hit2 = _nearest_dock_hit(samples, dock_xy, start_idx=max(0, i_a + 1), dock_eps=dock_eps)
        if hit2 is not None:
            i_b = hit2[0]

    i_a = max(0, min(len(samples) - 1, i_a))
    i_b = max(i_a, min(len(samples) - 1, i_b))
    reloc = samples[: i_a + 1] if i_a > 0 else []
    task = samples[i_a : i_b + 1]
    if len(task) < 2 or _traj_distance(task) < move_eps:
        return [], samples
    return reloc, task


def _overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    return max(a0, b0) < min(a1, b1)


def _near_task_endpoints(wait: Dict[str, Any], tasks: List[Dict[str, Any]], *, guard_s: float = 1.2) -> bool:
    """等待段若贴着任务起止点，视为靠站/收敛停顿，不标调度等待。"""
    w0 = float(wait.get("t0", 0.0))
    w1 = float(wait.get("t1", 0.0))
    for t in tasks:
        s = float(t.get("startSec", 0.0))
        f = float(t.get("finishSec", 0.0))
        if _overlap(w0, w1, s - guard_s, s + guard_s) or _overlap(w0, w1, f - guard_s, f + guard_s):
            return True
    return False


def _has_conflict_evidence(
    *,
    robot: str,
    wait: Dict[str, Any],
    traj_by_robot: Dict[str, List[List[float]]],
    near_dist: float = 2.4,
    time_pad: float = 0.8,
    move_eps: float = 0.06,
) -> bool:
    """判断等待段是否存在他车交通冲突证据（近时空 + 他车在动）。"""
    w0 = float(wait.get("t0", 0.0))
    w1 = float(wait.get("t1", 0.0))
    wx = float(wait.get("x", 0.0))
    wy = float(wait.get("y", 0.0))
    for other, traj in traj_by_robot.items():
        if other == robot or len(traj) < 2:
            continue
        win = [s for s in traj if (w0 - time_pad) <= s[0] <= (w1 + time_pad)]
        if len(win) < 2:
            continue
        min_d = min(math.hypot(s[1] - wx, s[2] - wy) for s in win)
        if min_d > near_dist:
            continue
        pos, _yaw = _motion_in_window(win, win[0][0], win[-1][0])
        has_move_flag = any(len(s) > 4 for s in win)
        moving_flag = any(int(s[4]) == 1 for s in win) if has_move_flag else False
        if pos >= move_eps or moving_flag:
            return True
    return False


def _filter_sched_waits(
    *,
    robot: str,
    waits: List[Dict[str, Any]],
    tasks: List[Dict[str, Any]],
    traj_by_robot: Dict[str, List[List[float]]],
) -> List[Dict[str, Any]]:
    """只保留有冲突证据、且不贴任务起终点的等待段。"""
    kept: List[Dict[str, Any]] = []
    for w in waits:
        if _near_task_endpoints(w, tasks):
            continue
        if not _has_conflict_evidence(robot=robot, wait=w, traj_by_robot=traj_by_robot):
            continue
        kept.append(w)
    return kept


def _nearest_dock_name(
    x: float,
    y: float,
    dock_xy: Dict[str, Tuple[float, float]],
    *,
    max_dist: float = 3.0,
) -> Optional[str]:
    if not dock_xy:
        return None
    best_name = None
    best_d = None
    for name, (dx, dy) in dock_xy.items():
        d = math.hypot(x - dx, y - dy)
        if best_d is None or d < best_d:
            best_name, best_d = name, d
    if best_d is None or best_d > max_dist:
        return None
    return best_name


def _list_named_docks(map_dir: str) -> List[Tuple[str, float, float]]:
    """读取 nav_graph 里的 dock_* 名称与坐标。"""
    docks: List[Tuple[str, float, float]] = []
    try:
        import yaml

        g = yaml.safe_load((Path(map_dir) / "nav_graphs" / "0.yaml").read_text(encoding="utf-8")) or {}
        verts = ((((g.get("levels") or {}).get("L1") or {}).get("vertices") or []))
        for v in verts:
            if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
                continue
            name = str(v[2].get("name") or "").strip()
            if not name.startswith("dock_"):
                continue
            docks.append((name, float(v[0]), float(v[1])))
    except Exception:  # noqa: BLE001
        return []
    return docks


def _pick_charge_docks(map_dir: str, n: int, preferred: List[str], dock_xy: Dict[str, Tuple[float, float]]) -> List[str]:
    """选 n 个充电点：优先用用户指定，否则自动选右上角相对空闲的 dock。"""
    n = max(1, int(n or 1))
    valid = set(dock_xy.keys())
    picked: List[str] = []

    # 1) 用户指定优先
    for name in preferred:
        nm = str(name or "").strip()
        if nm and nm in valid and nm not in picked:
            picked.append(nm)
        if len(picked) >= n:
            return picked

    # 2) 自动：右上角优先（x 大、y 大），并尽量拉开距离，避免同点扎堆
    docks = [(nm, *dock_xy[nm]) for nm in valid] if valid else _list_named_docks(map_dir)
    docks = sorted(docks, key=lambda t: (float(t[1]), float(t[2])), reverse=True)
    min_sep = 0.6
    for nm, x, y in docks:
        if nm in picked:
            continue
        ok = True
        for chosen in picked:
            cx, cy = dock_xy.get(chosen, (None, None))
            if cx is None:
                continue
            if math.hypot(float(cx) - float(x), float(cy) - float(y)) < min_sep:
                ok = False
                break
        if ok:
            picked.append(nm)
        if len(picked) >= n:
            break

    # 3) 兜底补齐
    if len(picked) < n:
        for nm in sorted(valid):
            if nm not in picked:
                picked.append(nm)
            if len(picked) >= n:
                break
    while len(picked) < n:
        picked.append(picked[-1] if picked else "dock_96_0")
    return picked


def _detect_waits(traj: List[List[float]], t0: float, t1: float,
                  eps: float = 0.06, yaw_eps: float = 0.08,
                  move_pos_eps: float = 0.03, move_yaw_eps: float = 0.05,
                  min_dur: float = 0.8,
                  turn_guard_s: float = 1.2, turn_guard_pos: float = 0.22,
                  turn_guard_yaw: float = 0.20) -> List[Dict[str, Any]]:
    """从轨迹样本 [[t,x,y,yaw,moving], ...] 检出**行进途中的静止段**（= 因调度原地停等）。

    只在"首次移动 → 末次移动"之间找静止段（排除起步前/跑完后的静止，那不算调度等待）；
    静止 = 与段起点位移 < eps 且航向变化 < yaw_eps；若样本含 moving 标记，仅统计 moving=0（非运动）段；
    另加转弯邻域过滤：若等待段前后紧邻"原地转向"（转角明显、位移极小），视为转弯停顿而非调度等待。
    仅取任务窗 [t0,t1] 内、时长 ≥ min_dur 的段。
    """
    seg = [s for s in traj if t0 <= s[0] <= t1]
    if len(seg) < 3:
        return []
    fm = lm = None  # 首次/末次"发生移动"的样本索引
    for i in range(1, len(seg)):
        pos_move = math.hypot(seg[i][1] - seg[i - 1][1], seg[i][2] - seg[i - 1][2]) > move_pos_eps
        yaw_move = _ang_diff(float(seg[i][3]), float(seg[i - 1][3])) > move_yaw_eps
        if pos_move or yaw_move:
            if fm is None:
                fm = i - 1
            lm = i
    if fm is None:
        return []
    waits: List[Dict[str, Any]] = []
    has_move_flag = any(len(s) > 4 for s in seg)
    i = fm
    while i < lm:
        if has_move_flag and int(seg[i][4]) != 0:
            i += 1
            continue
        t_i, x_i, y_i, yaw_i = seg[i][0], seg[i][1], seg[i][2], float(seg[i][3])
        j = i
        while (
            j + 1 <= lm
            and math.hypot(seg[j + 1][1] - x_i, seg[j + 1][2] - y_i) < eps
            and _ang_diff(float(seg[j + 1][3]), yaw_i) < yaw_eps
            and (not has_move_flag or int(seg[j + 1][4]) == 0)
        ):
            j += 1
        dur = seg[j][0] - t_i
        if j > i and dur >= min_dur:
            # 过滤转弯关联停顿：等待段前后若出现“位移很小但转角明显”的片段，则当作动力学转弯停顿。
            pre_pos, pre_yaw = _motion_in_window(seg, max(seg[fm][0], t_i - turn_guard_s), t_i)
            post_pos, post_yaw = _motion_in_window(seg, seg[j][0], min(seg[lm][0], seg[j][0] + turn_guard_s))
            turn_related = (
                (pre_yaw >= turn_guard_yaw and pre_pos <= turn_guard_pos)
                or (post_yaw >= turn_guard_yaw and post_pos <= turn_guard_pos)
            )
            if not turn_related:
                waits.append({"t0": round(t_i, 1), "t1": round(seg[j][0], 1),
                              "x": round(x_i, 2), "y": round(y_i, 2), "durationSec": round(dur, 1)})
        i = j + 1
    return waits


def run_multi(api: str, token: str, args: argparse.Namespace, fleet_params: Dict[str, Any]) -> Dict[str, Any]:
    """多机规划：全量 dispatch → 等分配稳定 → 读估时 → makespan → cancel。"""
    paths = _load_json(args.paths)
    transfers = paths.get("transfers") or []
    transfer_count = len(transfers)
    max_count = args.max if args.max and args.max > 0 else None

    # 关键：transfer_paths.json 的 navSequence 是内部 nav_* 节点 id，不是 RMF 图里的 waypoint 名，
    # 直接拿去 patrol 会 plan 失败（实测 5/5 failed）。必须经 fromInstance/toInstance → dock 名映射
    # （rmf_agv_routes.json），这些 dock_* 才是 RMF nav_graph 的合法 waypoint。
    routes_doc = _resolve_routes_doc(args)
    dock_map = _instance_dock_map(routes_doc)
    if not dock_map:
        print("[error] 找不到 instanceId→dock 映射（需 rmf_agv_routes.json，--routes 或 --map-dir 下）", file=sys.stderr)
        sys.exit(2)
    route_seq_map: Dict[Tuple[str, str], List[str]] = {}
    for r in routes_doc.get("routes") or []:
        f = str(r.get("fromWaypoint") or "").strip()
        t = str(r.get("toWaypoint") or "").strip()
        if not f or not t:
            continue
        raw = r.get("waypointSeq") or []
        seq = [str(x).strip() for x in raw if str(x).strip()]
        if not seq:
            seq = [f, t]
        if seq[0] != f:
            seq = [f, *seq]
        if seq[-1] != t:
            seq = [*seq, t]
        seq = [x for i, x in enumerate(seq) if i == 0 or x != seq[i - 1]]
        route_seq_map[(f, t)] = seq

    # 统一走 RMF dispatch_task 拍卖分配（execute/planned 一致），
    # 避免脚本侧固定派车策略对全场覆盖与交通协商造成人为限制。
    assign_robots: List[str] = []
    fleet_name = ""
    edge_url = str(getattr(args, "edge_url", "") or "http://127.0.0.1:8090").rstrip("/")
    waypoints: List[Dict[str, Any]] = []
    devices: List[Dict[str, Any]] = []
    layout: Dict[str, Any] = {}
    bounds: Dict[str, float] = {"minX": 0.0, "maxX": 1.0, "minY": 0.0, "maxY": 1.0}
    dock_xy: Dict[str, Tuple[float, float]] = {}
    if args.execute:
        waypoints, devices, layout, bounds = _map_schematic(args.map_dir)
        dock_xy = {
            str(p.get("name") or ""): (float(p.get("x", 0.0)), float(p.get("y", 0.0)))
            for p in waypoints
            if p.get("kind") == "dock" and str(p.get("name") or "")
        }
        for fn, robs in sorted(query_fleet_robots(api, token).items()):
            if robs:
                fleet_name, assign_robots = fn, list(robs)
                break

    robot_cursor_dock: Dict[str, str] = {}
    robot_start_dock: Dict[str, str] = {}
    if args.execute and assign_robots:
        fallback_dock = "dock_96_0" if "dock_96_0" in dock_xy else (sorted(dock_xy.keys())[0] if dock_xy else "")
        for r in assign_robots:
            cursor = fallback_dock
            st = edge_state(edge_url, r)
            if st is not None and dock_xy:
                nd = _nearest_dock_name(float(st.get("x", 0.0)), float(st.get("y", 0.0)), dock_xy, max_dist=3.2)
                if nd:
                    cursor = nd
            robot_cursor_dock[r] = cursor
        robot_start_dock = dict(robot_cursor_dock)

    envelope_rows: List[Dict[str, Any]] = []
    skipped = 0
    for tr in transfers:
        fr, to = _transfer_dock_endpoints(tr, dock_map)
        if not fr or not to or fr == to:
            skipped += 1
            continue
        src_from, src_to = _transfer_endpoint_keys(tr)
        # execute/planned：不绑车，统一走 RMF 拍卖最优分配
        rob = None
        reloc_hint = 0.0
        task_hint = 0.0
        flt = None
        seq_hint = [fr, to]
        if args.dispatch_mode == "delivery":
            env = build_delivery_request(fr, f"d_{fr}", to, f"i_{to}", payload=list(tr.get("payload") or []), fleet=flt, robot=rob)
        else:
            patrol_places = [fr, to]
            seq_hint = list(route_seq_map.get((fr, to)) or patrol_places)
            env = build_patrol_request(patrol_places, rounds=1, fleet=flt, robot=rob)
        envelope_rows.append({
            "env": env,
            "fromDock": fr,
            "toDock": to,
            "fromInstance": src_from,
            "toInstance": src_to,
            "dispatchRobotHint": rob or "",
            "dispatchFleetHint": flt or "",
            "dispatchRelocHintM": round(reloc_hint, 3),
            "dispatchTaskHintM": round(task_hint, 3),
            "waypointSeqHint": seq_hint,
        })
        if max_count and len(envelope_rows) >= max_count:
            break

    mode_desc = "拍卖最优分配"
    print(f"[multi] 生成 {len(envelope_rows)} 个 {args.dispatch_mode} 信封（源 transfer={transfer_count}，跳过未映射 {skipped}；{mode_desc}）→ dispatch ...", flush=True)
    task_ids: List[str] = []
    task_meta: Dict[str, Dict[str, Any]] = {}
    # RMF 拍卖分配：--dispatch-gap 可用于放缓派发节奏（缺省 0）
    gap = args.dispatch_gap if getattr(args, "dispatch_gap", -1.0) >= 0 else 0.0
    # 纯 RMF：一次性下发全部任务，车-车避让完全交给 RMF traffic scheduler。
    # 不做脚本侧“每车 1 个活跃任务 + 走廊/停靠点冲突门控”这类兜底（那会绕过 RMF 调度）。
    execute_stream_dispatch = False
    dispatch_queues: Dict[str, List[Dict[str, Any]]] = {}
    robot_active_tid: Dict[str, str] = {}
    robot_hold_dock: Dict[str, str] = {}
    dispatched_count = 0

    def _record_dispatched(tid: str, row: Dict[str, Any]) -> None:
        task_ids.append(tid)
        task_meta[tid] = {
            "fromDock": row["fromDock"],
            "toDock": row["toDock"],
            "fromInstance": row["fromInstance"],
            "toInstance": row["toInstance"],
            "dispatchRobotHint": row["dispatchRobotHint"],
            "dispatchFleetHint": row["dispatchFleetHint"],
            "dispatchRelocHintM": row.get("dispatchRelocHintM", 0.0),
            "dispatchTaskHintM": row.get("dispatchTaskHintM", 0.0),
            "waypointSeqHint": list(row.get("waypointSeqHint") or []),
            "runtimeSeqHint": list(row.get("runtimeSeqHint") or row.get("waypointSeqHint") or []),
            "dispatchOrder": len(task_ids) - 1,
        }

    def _dispatch_row(row: Dict[str, Any]) -> str:
        nonlocal dispatched_count
        tid = dispatch_envelope(api, token, row["env"])
        if tid:
            _record_dispatched(tid, row)
            dispatched_count += 1
            if dispatched_count % 50 == 0 or dispatched_count == len(envelope_rows):
                print(f"  dispatched {dispatched_count}/{len(envelope_rows)}", flush=True)
        return tid

    def _row_conflicts_with_active(row: Dict[str, Any], robot: str) -> bool:
        seq_list = [str(x) for x in list(row.get("runtimeSeqHint") or row.get("waypointSeqHint") or []) if str(x)]
        seq = set(seq_list)
        uses_star = any(x.startswith("star_") for x in seq_list)
        if not seq:
            return False
        for rr, tid in robot_active_tid.items():
            if rr == robot:
                continue
            other_meta = task_meta.get(tid) or {}
            other_seq_list = [
                str(x)
                for x in list(other_meta.get("runtimeSeqHint") or other_meta.get("waypointSeqHint") or [])
                if str(x)
            ]
            other_seq = set(other_seq_list)
            if other_seq and (seq & other_seq):
                return True
            # 主干 star_* 走廊采用全局互斥，避免双车在星形骨干中对向贴身。
            if uses_star and any(x.startswith("star_") for x in other_seq_list):
                return True
        # 防止“活跃车穿过另一台空闲车停靠点”造成同点重叠。
        for rr, hold in robot_hold_dock.items():
            if rr == robot or rr in robot_active_tid:
                continue
            if hold and hold in seq:
                return True
        return False

    def _dispatch_next_for_robot(robot: str) -> str:
        q = dispatch_queues.get(robot)
        if not q:
            return ""
        scanned = 0
        while scanned < len(q):
            row = q[scanned]
            if _row_conflicts_with_active(row, robot):
                scanned += 1
                continue
            row = q.pop(scanned)
            tid = _dispatch_row(row)
            if tid:
                return tid
            print(f"[warn] dispatch 失败，跳过并继续（robot={robot}）", flush=True)
            if gap > 0:
                time.sleep(gap)
        # 当前无可安全补派任务（与其他车活跃路径冲突）；后续轮询再试。
        return ""

    if execute_stream_dispatch:
        for r in assign_robots:
            dispatch_queues[r] = []
            robot_hold_dock[r] = str(robot_start_dock.get(r, ""))
        runtime_cursor_dock = {r: str(robot_start_dock.get(r, "")) for r in assign_robots}
        for i, row in enumerate(envelope_rows):
            rr = str(row.get("dispatchRobotHint") or "")
            if rr not in dispatch_queues:
                rr = assign_robots[i % len(assign_robots)]
                row = {**row, "dispatchRobotHint": rr, "dispatchFleetHint": fleet_name}
            from_dock = str(row.get("fromDock") or "")
            to_dock = str(row.get("toDock") or "")
            task_seq = [str(x) for x in list(row.get("waypointSeqHint") or []) if str(x)]
            cur = runtime_cursor_dock.get(rr, "")
            full_seq: List[str] = []
            if cur and from_dock and cur != from_dock:
                reloc_seq = [str(x) for x in list(route_seq_map.get((cur, from_dock)) or [cur, from_dock]) if str(x)]
                full_seq.extend(reloc_seq)
            if task_seq:
                if full_seq and task_seq and full_seq[-1] == task_seq[0]:
                    full_seq.extend(task_seq[1:])
                else:
                    full_seq.extend(task_seq)
            full_seq = [x for j, x in enumerate(full_seq) if j == 0 or x != full_seq[j - 1]]
            row = {**row, "runtimeSeqHint": full_seq or task_seq}
            dispatch_queues[rr].append(row)
            runtime_cursor_dock[rr] = to_dock or from_dock or cur
        print("[multi] execute 流式派发：每车仅保持 1 个活跃任务，完成后再补下一个", flush=True)
        for r in assign_robots:
            tid0 = _dispatch_next_for_robot(r)
            if tid0:
                robot_active_tid[r] = tid0
                if gap > 0:
                    time.sleep(gap)
        print(f"[multi] 初始派发 {len(task_ids)}/{len(envelope_rows)}；进入执行轮询并按完成补派 ...", flush=True)
    else:
        for i, row in enumerate(envelope_rows):
            _dispatch_row(row)
            if gap > 0 and i + 1 < len(envelope_rows):
                time.sleep(gap)
        print(f"[multi] 已下发 {len(task_ids)}/{len(envelope_rows)}；轮询到终态 ...", flush=True)

    if args.execute:
        # 交通级 + 可视化采集：统一轮询 pose(:8090) + task(:8000)，按 my-clock（wall×ff=sim 秒）记录
        # 每车轨迹 + 任务窗 + 等待段（自洽一套时钟，不依赖 fast-sim 下漂移的 RMF unix_millis）。
        ff = float(getattr(args, "fast_forward", None) or 10.0)
        rlist = list(assign_robots)
        if not rlist:
            for _fn, _rb in sorted(query_fleet_robots(api, token).items()):
                if _rb:
                    rlist = list(_rb)
                    break
        user_charge = [s.strip() for s in str(getattr(args, "charge_docks", "") or "").split(",") if s.strip()]
        map_charge = sorted([nm for nm in dock_xy.keys() if nm.startswith("dock_charge_")])
        auto_charge_hint = [*map_charge, "dock_hplc_16", "dock_hplc_17", "dock_hplc_8", "dock_hplc_9"]
        charge_pref = user_charge or auto_charge_hint
        return_to_charge = bool(getattr(args, "return_to_charge", True))
        charge_dock_by_robot: Dict[str, str] = {}
        if return_to_charge and rlist and fleet_name:
            picked_charge = _pick_charge_docks(args.map_dir, len(rlist), charge_pref, dock_xy)
            charge_dock_by_robot = {r: picked_charge[i] for i, r in enumerate(rlist)}
            print(f"[execute] 充电点：{', '.join(f'{r}->{charge_dock_by_robot[r]}' for r in rlist)}", flush=True)
        elif return_to_charge and rlist and not fleet_name:
            print("[warn] 未识别 fleet_name，跳过自动回充（可重试 --launch）", flush=True)
        tid_robot = {
            tid: str((task_meta.get(tid) or {}).get("dispatchRobotHint") or (rlist[i % len(rlist)] if rlist else ""))
            for i, tid in enumerate(task_ids)
        }
        charge_tid_robot: Dict[str, str] = {}
        charge_meta: Dict[str, Dict[str, Any]] = {}
        charge_start: Dict[str, float] = {}
        charge_finish: Dict[str, float] = {}
        idle_charge_timeout = max(0.0, float(getattr(args, "idle_charge_timeout", 120.0) or 0.0))
        if return_to_charge and charge_dock_by_robot:
            print(f"[execute] 预排回充任务（无任务 {idle_charge_timeout:.0f}s 后自动回充）...", flush=True)
            for r in rlist:
                cd = charge_dock_by_robot.get(r, "")
                if not cd:
                    continue
                from_dock = robot_cursor_dock.get(r, "")  # 派发期维护的“该车最后任务终点”估计
                charge_seq = route_seq_map.get((from_dock, cd)) if from_dock else None
                if charge_seq and len(charge_seq) >= 2:
                    env = build_patrol_request(list(charge_seq), rounds=1, fleet=fleet_name, robot=r)
                else:
                    env = build_go_to_request(cd, fleet=fleet_name, robot=r)
                req = env.get("request") or {}
                if idle_charge_timeout > 0.0:
                    wall_delay = idle_charge_timeout / max(1.0, ff)  # 入参按 sim 秒理解；换算成墙钟延迟
                    req["unix_millis_earliest_start_time"] = int(time.time() * 1000.0 + wall_delay * 1000.0)
                ctid = dispatch_envelope(api, token, env)
                if ctid:
                    charge_tid_robot[ctid] = r
                    charge_meta[ctid] = {
                        "chargeDock": cd,
                        "fromDock": from_dock,
                        "waypointSeq": list(charge_seq or []),
                        "idleChargeTimeoutSec": round(idle_charge_timeout, 1),
                    }
        traj: Dict[str, List[List[float]]] = {r: [] for r in rlist}
        task_start: Dict[str, float] = {}
        task_finish: Dict[str, float] = {}
        wall0 = time.time()
        deadline = wall0 + max(5.0, args.wait_timeout)
        print(f"[execute] 轮询采集轨迹+任务态（pose@{edge_url}，x{ff} sim）...", flush=True)
        it = 0
        while time.time() < deadline:
            t = round((time.time() - wall0) * ff, 2)
            for r in rlist:
                stx = edge_state(edge_url, r)
                if stx is not None:
                    traj[r].append([t, round(float(stx.get("x", 0.0)), 3), round(float(stx.get("y", 0.0)), 3),
                                    round(float(stx.get("yaw", 0.0)), 4), 1 if str(stx.get("status")) == "moving" else 0])
            if it % 6 == 0:  # 每 ~0.3s wall 批量查任务态
                poll_ids = [*task_ids, *list(charge_tid_robot.keys())]
                batch = tasks_batch(api, token, poll_ids)
                pending_dispatch = bool(
                    execute_stream_dispatch
                    and any(bool(dispatch_queues.get(r)) for r in assign_robots)
                )
                allterm = bool(task_ids) and not pending_dispatch
                for tid in list(task_ids):
                    st_row = batch.get(tid) or {}
                    # 回退到拍卖时，用 RMF 实际 assigned_to 覆盖初始直派归属，确保统计正确。
                    assigned = _assigned_robot(st_row)
                    if assigned:
                        tid_robot[tid] = assigned
                    status = str((batch.get(tid) or {}).get("status") or "").lower()
                    if status and status not in ("", "uninitialized", "queued", "pending") and tid not in task_start:
                        task_start[tid] = t
                    if status in _TERMINAL:
                        task_finish.setdefault(tid, t)
                        done_robot = str(tid_robot.get(tid, ""))
                        done_to = str((task_meta.get(tid) or {}).get("toDock") or "")
                        if done_robot and done_to:
                            robot_hold_dock[done_robot] = done_to
                    else:
                        allterm = False
                for ctid in charge_tid_robot.keys():
                    st_row = batch.get(ctid) or {}
                    status = str(st_row.get("status") or "").lower()
                    if status and status not in ("", "uninitialized", "queued", "pending") and ctid not in charge_start:
                        charge_start[ctid] = t
                    if status in _TERMINAL:
                        charge_finish.setdefault(ctid, t)
                if execute_stream_dispatch and assign_robots:
                    for r in assign_robots:
                        cur_tid = robot_active_tid.get(r, "")
                        if cur_tid:
                            cur_status = str((batch.get(cur_tid) or {}).get("status") or "").lower()
                            if cur_status not in _TERMINAL:
                                continue
                            robot_active_tid.pop(r, None)
                        if not cur_tid or cur_status in _TERMINAL:
                            nxt = _dispatch_next_for_robot(r)
                            if nxt:
                                robot_active_tid[r] = nxt
                                allterm = False
                                if gap > 0:
                                    time.sleep(gap)
                    if robot_active_tid:
                        allterm = False
                    if any(bool(dispatch_queues.get(r)) for r in assign_robots):
                        allterm = False
                if allterm:
                    break
            it += 1
            time.sleep(0.05)

        if charge_tid_robot:
            pending = {tid for tid in charge_tid_robot.keys() if tid not in charge_finish}
            if pending:
                print("[execute] 等待回充任务终态 ...", flush=True)
            charge_deadline = time.time() + max(120.0, min(600.0, args.wait_timeout * 0.5))
            pit = 0
            while pending and time.time() < charge_deadline:
                t = round((time.time() - wall0) * ff, 2)
                for r in rlist:
                    stx = edge_state(edge_url, r)
                    if stx is not None:
                        traj[r].append([t, round(float(stx.get("x", 0.0)), 3), round(float(stx.get("y", 0.0)), 3),
                                        round(float(stx.get("yaw", 0.0)), 4), 1 if str(stx.get("status")) == "moving" else 0])
                if pit % 6 == 0:
                    batch = tasks_batch(api, token, list(pending))
                    for tid in list(pending):
                        st_row = batch.get(tid) or {}
                        status = str(st_row.get("status") or "").lower()
                        if status and status not in ("", "uninitialized", "queued", "pending") and tid not in charge_start:
                            charge_start[tid] = t
                        if status in _TERMINAL:
                            charge_finish.setdefault(tid, t)
                            pending.discard(tid)
                pit += 1
                time.sleep(0.05)

        charge_windows: List[Dict[str, Any]] = []
        for tid, rob in charge_tid_robot.items():
            if tid not in charge_finish:
                continue
            s = charge_start.get(tid, charge_finish[tid])
            f = max(charge_finish[tid], s)
            cm = charge_meta.get(tid) or {}
            charge_windows.append({
                "taskId": tid,
                "robot": rob,
                "startSec": round(s, 1),
                "finishSec": round(f, 1),
                "windowSec": round(max(0.0, f - s), 1),
                "fromDock": cm.get("fromDock"),
                "toDock": cm.get("chargeDock"),
                "waypointSeq": list(cm.get("waypointSeq") or []),
                "taskType": "charge_return",
            })

        by_task: List[Dict[str, Any]] = []
        per_robot_run: Dict[str, float] = {}
        per_robot_window: Dict[str, float] = {}
        for tid in task_ids:
            if tid not in task_finish:
                continue
            s = task_start.get(tid, 0.0)
            f = max(task_finish[tid], s)
            robot = tid_robot.get(tid, "")
            window_sec = round(f - s, 1)
            task_run_sec = window_sec
            reloc_sec = 0.0
            task_s = s
            task_f = f
            meta = task_meta.get(tid) or {}
            if robot in traj:
                seg = [pt for pt in traj[robot] if (s - 0.01) <= pt[0] <= (f + 0.01)]
                if len(seg) >= 2:
                    reloc_seg, task_seg = _split_task_samples(
                        seg,
                        from_dock=str(meta.get("fromDock") or ""),
                        to_dock=str(meta.get("toDock") or ""),
                        dock_xy=dock_xy,
                    )
                    if len(task_seg) >= 2:
                        task_s = float(task_seg[0][0])
                        task_f = float(task_seg[-1][0])
                        task_run_sec = max(0.0, round(task_f - task_s, 1))
                    if len(reloc_seg) >= 2:
                        reloc_sec = max(0.0, round(float(reloc_seg[-1][0]) - float(reloc_seg[0][0]), 1))
            if task_run_sec <= 0.0:
                task_run_sec = window_sec
            by_task.append({"taskId": tid, "robot": robot, "estimateSec": round(task_run_sec, 1),
                            "startSec": round(s, 1), "finishSec": round(f, 1), "status": "completed",
                            "windowSec": window_sec, "taskRunSec": round(task_run_sec, 1), "relocSec": round(reloc_sec, 1),
                            "taskStartSec": round(task_s, 1), "taskFinishSec": round(task_f, 1),
                            "fromDock": meta.get("fromDock"), "toDock": meta.get("toDock"),
                            "waypointSeqHint": meta.get("waypointSeqHint"),
                            "fromInstance": meta.get("fromInstance"), "toInstance": meta.get("toInstance"),
                            "source": "executed my-clock poll (traffic-accurate)"})
            per_robot_run[robot] = round(per_robot_run.get(robot, 0.0) + task_run_sec, 1)
            per_robot_window[robot] = round(per_robot_window.get(robot, 0.0) + window_sec, 1)
        total_sec = round(max((x["finishSec"] for x in by_task), default=0.0), 1)
        viz_total_sec = round(max(total_sec, max((x["finishSec"] for x in charge_windows), default=0.0)), 1)

        colors = ["#5BA0E9", "#5CBF60", "#E9A23B", "#B57BE0", "#E96B8E", "#43C6C6"]
        viz_robots: Dict[str, Any] = {}
        all_waits: List[Dict[str, Any]] = []
        raw_wait_count = 0
        for ri, r in enumerate(rlist):
            rtasks = [x for x in by_task if x["robot"] == r]
            ctasks = [x for x in charge_windows if x["robot"] == r]
            ms0 = min((x["startSec"] for x in rtasks), default=0.0)
            ms1 = max((x["finishSec"] for x in rtasks), default=viz_total_sec)
            waits_raw = _detect_waits(traj[r], ms0, ms1)
            waits = _filter_sched_waits(robot=r, waits=waits_raw, tasks=rtasks, traj_by_robot=traj)
            raw_wait_count += len(waits_raw)
            for w in waits:
                all_waits.append({"robot": r, **w})
            viz_tasks = [
                {"taskId": x["taskId"], "t0": x["startSec"], "t1": x["finishSec"],
                 "taskType": "mission",
                 "taskRunSec": x.get("taskRunSec", x.get("estimateSec", 0.0)),
                 "relocSec": x.get("relocSec", 0.0),
                 "fromDock": x.get("fromDock"), "toDock": x.get("toDock"),
                 "fromInstance": x.get("fromInstance"), "toInstance": x.get("toInstance")}
                for x in rtasks
            ]
            viz_tasks += [
                {"taskId": x["taskId"], "t0": x["startSec"], "t1": x["finishSec"],
                 "taskType": "charge_return",
                 "taskRunSec": x.get("windowSec", 0.0), "relocSec": 0.0,
                 "fromDock": x.get("fromDock"), "toDock": x.get("toDock"),
                 "waypointSeq": x.get("waypointSeq", []),
                 "fromInstance": "post-run", "toInstance": "charge"}
                for x in ctasks
            ]
            viz_robots[r] = {"color": colors[ri % len(colors)], "trajectory": traj[r], "waits": waits,
                             "tasks": sorted(viz_tasks, key=lambda z: z["t0"])}
        viz = {"frame": "edge", "fastForward": ff, "bounds": bounds, "waypoints": waypoints, "devices": devices,
               "layout": layout,
               "makespanSec": viz_total_sec, "robots": viz_robots,
               "chargeDocks": charge_dock_by_robot}
        print(
            f"[execute] 跑完 {len(by_task)}/{len(task_ids)}（交通级；{sum(len(v) for v in traj.values())} 位姿样本，"
            f"等待段 {len(all_waits)}/{raw_wait_count}；回充 {len(charge_windows)}/{len(charge_tid_robot)}）",
            flush=True,
        )
        return {
            "_counts": {"dispatched": len(task_ids), "ready": len(by_task),
                        "unassigned": len(task_ids) - len(by_task), "skippedUnmapped": skipped, "transferCount": transfer_count},
            "makespan": {"kind": "traffic_accurate", "trafficAccurate": True, "scheduleModel": "executed_actual",
                         "totalSec": total_sec, "robots": len({x["robot"] for x in by_task}),
                         "perRobot": {r: v for r, v in sorted(per_robot_run.items())},
                         "perRobotWindow": {r: v for r, v in sorted(per_robot_window.items())},
                         "byTask": sorted(by_task, key=lambda x: x["startSec"]), "waits": all_waits,
                         "postRunCharge": {"enabled": return_to_charge, "docks": charge_dock_by_robot, "tasks": charge_windows}},
            "viz": viz,
        }

    # planned：轮询到分配+估时就绪（不执行）
    states = _wait_until(api, token, task_ids, predicate=_is_planned, timeout_s=args.wait_timeout)

    # 稳定的 planned makespan：用 RMF 的分配（assigned_to）+ 每条任务的估时（original_estimate_millis），
    # 按"每车顺序排队"合成调度 → makespan = 瓶颈车的累计估时。
    # ⚠️ 不用 unix_millis_start/finish_time 算跨度：fast-sim 下这俩会漂移、甚至 start>finish（实测
    #    start=763094 > finish=80391 → 负时长），makespan 会变垃圾。估时（original_estimate_millis）才稳定。
    ready_tasks: List[Tuple[str, str, float]] = []
    unassigned = 0
    for tid in task_ids:
        st = states.get(tid) or {}
        robot = _assigned_robot(st)
        est = _estimate_sec(st)
        if not robot or est is None:
            unassigned += 1
            continue
        ready_tasks.append((tid, robot, est))

    # 每车按分配顺序顺序排队：start=该车累计，finish=start+est（合成调度，0 基，单位秒）
    per_robot_cursor: Dict[str, float] = {}
    by_task: List[Dict[str, Any]] = []
    for tid, robot, est in ready_tasks:
        meta = task_meta.get(tid) or {}
        start = per_robot_cursor.get(robot, 0.0)
        finish = start + est
        per_robot_cursor[robot] = finish
        by_task.append({
            "taskId": tid, "robot": robot, "estimateSec": round(est, 1),
            "startSec": round(start, 1), "finishSec": round(finish, 1),
            "windowSec": round(est, 1), "taskRunSec": round(est, 1), "relocSec": 0.0,
            "taskStartSec": round(start, 1), "taskFinishSec": round(finish, 1),
            "fromDock": meta.get("fromDock"), "toDock": meta.get("toDock"),
            "waypointSeqHint": meta.get("waypointSeqHint"),
            "fromInstance": meta.get("fromInstance"), "toInstance": meta.get("toInstance"),
            "source": "rmf:original_estimate_millis (sequential per-robot queue)",
        })
    per_robot = {r: round(v, 1) for r, v in sorted(per_robot_cursor.items())}
    total_sec = round(max(per_robot.values()), 1) if per_robot else 0.0  # 瓶颈车累计 = makespan

    if not args.execute:
        cancelled = 0
        for tid in task_ids:
            if str((states.get(tid) or {}).get("status") or "").lower() in _TERMINAL:
                continue  # 已终态，无需取消（fast-sim 下常已执行完）
            if cancel_task(api, token, tid):
                cancelled += 1
        print(f"[multi] 读取完成；cancel 未终态任务 {cancelled}（fast-sim 下任务可能已执行完，cancel 非必需）", flush=True)

    return {
        "_counts": {"dispatched": len(task_ids), "ready": len(by_task), "unassigned": unassigned,
                    "skippedUnmapped": skipped, "transferCount": transfer_count},
        "makespan": {
            "kind": "traffic_accurate" if args.execute else "planned_allocation",
            "trafficAccurate": bool(args.execute),
            "scheduleModel": "per_robot_sequential_estimate",  # makespan = 瓶颈车累计估时（不含车-车实时避让）
            "totalSec": total_sec,
            "robots": len(per_robot),
            "perRobot": per_robot,
            "byTask": sorted(by_task, key=lambda t: (t["robot"], t["startSec"])),
        },
    }


# ============================================================ 结果组装 + 写盘
def build_result(args: argparse.Namespace, fleet_params: Dict[str, Any], fleet_actual: Dict[str, List[str]], core: Dict[str, Any]) -> Dict[str, Any]:
    robots_actual = sum(len(v) for v in fleet_actual.values())
    note = ""
    if args.robots != robots_actual:
        note = (
            f"--robots={args.robots} 为请求值；运行中 RMF 实际有 {robots_actual} 台车。"
            "要让车数/速度覆盖真正生效，需用覆盖后的 fleet_config 重启 RMF（见 24.9 §4 / §7.2）。"
        )
    meta = {
        "source": "rmf_estimate_times",
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "mode": "multi",
        "dispatchMode": args.dispatch_mode,
        "apiUrl": args.api_url,
        "fleet": {
            "robotsRequested": args.robots,
            "robotsActual": robots_actual,
            "robotNames": sorted(r for v in fleet_actual.values() for r in v),
            **{k: fleet_params[k] for k in ("maxLinearSpeed", "linearAccel", "maxAngularSpeed", "angularAccel", "footprint")},
            "usedDefaultsFrom": fleet_params.get("usedDefaultsFrom"),
            "note": note,
        },
    }
    meta.update(core.pop("_counts", {}))
    result: Dict[str, Any] = {"meta": meta}
    result.update(core)
    return result


def write_outputs(result: Dict[str, Any], args: argparse.Namespace) -> Tuple[str, Optional[str]]:
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = None
    if not args.no_html:
        html_path = args.html or str(out_path.with_suffix(".html"))
        Path(html_path).write_text(render_html(result, args), encoding="utf-8")
    return str(out_path), html_path


# ============================================================ HTML 报告（自包含，内联 SVG）
def _fmt_mmss(sec: float) -> str:
    sec = int(round(sec or 0))
    return f"{sec // 60:d}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600:d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _svg_bars(items: List[Tuple[str, float]], *, width: int = 720, unit: str = "s") -> str:
    """水平条形图（label, value）。"""
    if not items:
        return "<p class='muted'>无数据</p>"
    vmax = max((v for _, v in items), default=1.0) or 1.0
    row_h, pad_l, pad_r = 26, 160, 70
    bar_w = width - pad_l - pad_r
    height = row_h * len(items) + 10
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">']
    for i, (label, v) in enumerate(items):
        y = i * row_h + 6
        w = max(1.0, (v / vmax) * bar_w)
        parts.append(f'<text x="{pad_l - 8}" y="{y + 13}" text-anchor="end" class="svg-lbl">{html.escape(str(label))}</text>')
        parts.append(f'<rect x="{pad_l}" y="{y}" width="{w:.1f}" height="18" rx="3" class="svg-bar"/>')
        parts.append(f'<text x="{pad_l + w + 6:.1f}" y="{y + 13}" class="svg-val">{v:.1f}{unit}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_gantt(by_task: List[Dict[str, Any]], *, width: int = 900) -> str:
    """甘特图（每车一行，任务按合成排队 startSec→finishSec 排布，单位秒、0 基）。"""
    tasks = [t for t in by_task if "finishSec" in t and "startSec" in t]
    if not tasks:
        return "<p class='muted'>无可绘制任务</p>"
    robots = sorted({t["robot"] for t in tasks})
    span = max(1e-6, max(t["finishSec"] for t in tasks))
    row_h, pad_l, pad_r, top = 30, 130, 20, 24
    plot_w = width - pad_l - pad_r
    height = top + row_h * len(robots) + 30
    colors = ["#5BA0E9", "#5CBF60", "#E9A23B", "#B57BE0", "#E96B8E", "#43C6C6"]
    ridx = {r: i for i, r in enumerate(robots)}
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">']
    parts.append(f'<line x1="{pad_l}" y1="{top - 6}" x2="{pad_l + plot_w}" y2="{top - 6}" class="svg-axis"/>')
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        x = pad_l + plot_w * frac
        parts.append(f'<text x="{x:.0f}" y="{top - 10}" text-anchor="middle" class="svg-tick">{_fmt_mmss(span * frac)}</text>')
    for r in robots:
        y = top + ridx[r] * row_h
        parts.append(f'<text x="{pad_l - 8}" y="{y + 19}" text-anchor="end" class="svg-lbl">{html.escape(r)}</text>')
        parts.append(f'<line x1="{pad_l}" y1="{y + row_h - 4}" x2="{pad_l + plot_w}" y2="{y + row_h - 4}" class="svg-grid"/>')
    for t in tasks:
        y = top + ridx[t["robot"]] * row_h + 4
        x = pad_l + t["startSec"] / span * plot_w
        w = max(2.0, (t["finishSec"] - t["startSec"]) / span * plot_w)
        c = colors[ridx[t["robot"]] % len(colors)]
        title = f'{t["taskId"]}  {t.get("estimateSec", "")}s'
        parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="18" rx="2" fill="{c}" opacity="0.85"><title>{html.escape(title)}</title></rect>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_histogram(values: List[float], *, width: int = 720, bins: int = 12) -> str:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return "<p class='muted'>无数据</p>"
    vmin, vmax = min(vals), max(vals)
    if vmax <= vmin:
        vmax = vmin + 1.0
    step = (vmax - vmin) / bins
    counts = [0] * bins
    for v in vals:
        idx = min(bins - 1, int((v - vmin) / step))
        counts[idx] += 1
    cmax = max(counts) or 1
    pad_l, pad_b, top = 36, 28, 10
    plot_w, plot_h = width - pad_l - 12, 180
    bw = plot_w / bins
    height = top + plot_h + pad_b
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img">']
    for i, c in enumerate(counts):
        h = (c / cmax) * plot_h
        x = pad_l + i * bw
        y = top + plot_h - h
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(1.0, bw - 2):.1f}" height="{h:.1f}" class="svg-bar"><title>{vmin + i * step:.1f}-{vmin + (i + 1) * step:.1f}s: {c}</title></rect>')
    parts.append(f'<text x="{pad_l}" y="{height - 8}" class="svg-tick">{vmin:.0f}s</text>')
    parts.append(f'<text x="{pad_l + plot_w:.0f}" y="{height - 8}" text-anchor="end" class="svg-tick">{vmax:.0f}s</text>')
    parts.append(f'<text x="{pad_l - 6}" y="{top + 10}" text-anchor="end" class="svg-tick">{cmax}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _table(headers: List[str], rows: List[List[str]], table_id: str) -> str:
    th = "".join(f'<th onclick="sortTable(\'{table_id}\',{i})">{html.escape(h)} &#8597;</th>' for i, h in enumerate(headers))
    trs = []
    for row in rows:
        tds = "".join(f"<td>{c}</td>" for c in row)
        trs.append(f"<tr>{tds}</tr>")
    return f'<table id="{table_id}"><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>'


_HTML_CSS = """
:root{--bg:#0f1419;--card:#1a212b;--fg:#e6edf3;--mut:#8b97a6;--acc:#5BA0E9;--ok:#5CBF60;--line:#2b3543}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:24px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 10px;color:var(--acc);border-bottom:1px solid var(--line);padding-bottom:6px}
.muted{color:var(--mut)}.cards{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:130px}
.card .k{color:var(--mut);font-size:12px}.card .v{font-size:18px;font-weight:600;margin-top:2px}
.big{font-size:34px;font-weight:700;color:var(--ok)}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;background:#243042;color:var(--acc);margin-left:8px}
.badge.warn{background:#3a2a12;color:#E9A23B}
.note{background:#1d2530;border:1px solid var(--line);border-left:3px solid #E9A23B;border-radius:6px;padding:10px 14px;color:#cdd6e0;margin:10px 0}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--mut);cursor:pointer;user-select:none;position:sticky;top:0;background:var(--card)}
tr:hover td{background:#1d2530}
.svg-lbl{fill:var(--fg);font-size:12px}.svg-val{fill:var(--mut);font-size:11px}
.svg-bar{fill:var(--acc)}.svg-tick{fill:var(--mut);font-size:10px}.svg-axis{stroke:var(--line)}.svg-grid{stroke:#222b37}
.tbl-wrap{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:8px}
.foot{color:var(--mut);font-size:12px;margin-top:30px}
.playbar{display:flex;align-items:center;gap:12px;margin:12px 0}
.pbtn{background:#243042;color:var(--acc);border:1px solid var(--line);border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px}
.pbtn:hover{background:#2b3a4f}
.mapwrap,.gwrap{background:#0c1116;border:1px solid var(--line);border-radius:8px;padding:8px;overflow:auto;margin:8px 0}
.mapwrap svg{cursor:grab;touch-action:none}
.gwrap svg{cursor:ew-resize;touch-action:none}
.playbar.map-tools{margin:4px 0 8px}
.gantt-overlay{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 8px;padding:4px 0}
.speed-label{color:var(--acc);font-size:12px}
tr.active-row td{background:#2a3a4d}
#taskTbl tr{cursor:pointer}
.leg{display:flex;gap:16px;flex-wrap:wrap;color:var(--mut);font-size:12px;margin:6px 0}
.leg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}
"""

_HTML_JS = """
function sortTable(id,col){
  var t=document.getElementById(id),tb=t.tBodies[0],rows=Array.prototype.slice.call(tb.rows);
  var dir=t.getAttribute('data-sc')==col+'a'?-1:1;t.setAttribute('data-sc',col+(dir==1?'a':'d'));
  rows.sort(function(a,b){var x=a.cells[col].innerText,y=b.cells[col].innerText;
    var nx=parseFloat(x),ny=parseFloat(y);
    if(!isNaN(nx)&&!isNaN(ny))return (nx-ny)*dir;return x.localeCompare(y)*dir;});
  rows.forEach(function(r){tb.appendChild(r);});
}
"""

# ============================================================ 交互式回放（--execute viz）
_VIZ_JS = r"""
(function(){
  var VIZ=DATA.viz||{}, BY=DATA.byTask||[];
  var rids=Object.keys(VIZ.robots||{});
  var span=Math.max(0.1, VIZ.makespanSec||1);
  var B=VIZ.bounds||{minX:0,maxX:1,minY:0,maxY:1};
  var LB=(VIZ.layout&&VIZ.layout.bounds)||null;
  if(LB){
    B={
      minX:Math.min(B.minX,LB.minX),
      maxX:Math.max(B.maxX,LB.maxX),
      minY:Math.min(B.minY,LB.minY),
      maxY:Math.max(B.maxY,LB.maxY)
    };
  }
  function esc(s){ return String(s||'').replace(/[&<>]/g, function(ch){ return ({'&':'&amp;','<':'&lt;','>':'&gt;'})[ch]; }); }
  // ----- MAP -----
  var mapSvg=document.getElementById('mapSvg'), pad=24, MW=960;
  var dx=Math.max(0.001,B.maxX-B.minX), dy=Math.max(0.001,B.maxY-B.minY);
  var scale=(MW-2*pad)/dx, MH=dy*scale+2*pad;
  mapSvg.setAttribute('viewBox','0 0 '+MW+' '+MH); mapSvg.setAttribute('height',Math.min(MH,560));
  function ex(x){return pad+(x-B.minX)*scale;}
  function ey(y){return pad+(B.maxY-y)*scale;}
  var DOCK_EPS=0.45, MOVE_EPS=0.08;
  var viewportG=document.getElementById('viewportG');
  var dockMap={}, dockList=[];
  var wp='';
  (VIZ.waypoints||[]).forEach(function(p){
    if(p.kind==='dock'){
      wp+='<circle cx="'+ex(p.x).toFixed(1)+'" cy="'+ey(p.y).toFixed(1)+'" r="3.2" fill="#5BA0E9" opacity="0.85"/>';
      if(p.name){ dockMap[p.name]={x:p.x,y:p.y,name:p.name}; dockList.push({x:p.x,y:p.y,name:p.name}); }
    }
    else wp+='<circle cx="'+ex(p.x).toFixed(1)+'" cy="'+ey(p.y).toFixed(1)+'" r="1.5" fill="#5a6b7d" opacity="0.65"/>';
  });
  var lay='';
  if(VIZ.layout && VIZ.layout.imageDataUri && VIZ.layout.bounds){
    var L=VIZ.layout.bounds;
    var lx=ex(L.minX), ly=ey(L.maxY), lw=Math.max(1,(L.maxX-L.minX)*scale), lh=Math.max(1,(L.maxY-L.minY)*scale);
    var op=Math.max(0,Math.min(1,parseFloat(VIZ.layout.opacity||0.6)));
    lay+='<image href="'+VIZ.layout.imageDataUri+'" x="'+lx.toFixed(1)+'" y="'+ly.toFixed(1)+'" width="'+lw.toFixed(1)+'" height="'+lh.toFixed(1)+'" preserveAspectRatio="none" opacity="'+op+'"/>';
  }
  document.getElementById('layoutG').innerHTML=lay;
  document.getElementById('wpG').innerHTML=wp;
  var dev='';
  (VIZ.devices||[]).forEach(function(d){
    var cx=ex(d.x), cy=ey(d.y), tip=esc((d.name||'')+' @'+(d.dock||d.waypoint||''));
    dev+='<rect x="'+(cx-2.6).toFixed(1)+'" y="'+(cy-2.6).toFixed(1)+'" width="5.2" height="5.2" rx="0.8" fill="#FFB74D" stroke="#0f1419" stroke-width="0.8"><title>'+tip+'</title></rect>';
  });
  document.getElementById('devG').innerHTML=dev;
  var pathsG=document.getElementById('pathsG'), robotsG=document.getElementById('robotsG');
  // ----- map pan/zoom -----
  var z=1.0, panX=0.0, panY=0.0;
  function applyView(){ viewportG.setAttribute('transform','matrix('+z.toFixed(6)+' 0 0 '+z.toFixed(6)+' '+panX.toFixed(2)+' '+panY.toFixed(2)+')'); }
  function clamp(v,lo,hi){ return Math.max(lo,Math.min(hi,v)); }
  function zoomAt(cx,cy,factor){
    var nz=clamp(z*factor,0.6,10.0);
    if(Math.abs(nz-z)<1e-6)return;
    panX = cx - (cx-panX)*(nz/z);
    panY = cy - (cy-panY)*(nz/z);
    z=nz; applyView();
  }
  function svgPoint(ev){
    var p=mapSvg.createSVGPoint();
    p.x=ev.clientX; p.y=ev.clientY;
    return p.matrixTransform(mapSvg.getScreenCTM().inverse());
  }
  var zIn=document.getElementById('zoomInBtn'), zOut=document.getElementById('zoomOutBtn'), zReset=document.getElementById('zoomResetBtn');
  if(zIn) zIn.addEventListener('click', function(){ zoomAt(MW*0.5,MH*0.5,1.2); });
  if(zOut) zOut.addEventListener('click', function(){ zoomAt(MW*0.5,MH*0.5,1/1.2); });
  if(zReset) zReset.addEventListener('click', function(){ z=1.0; panX=0.0; panY=0.0; applyView(); });
  mapSvg.addEventListener('wheel', function(ev){
    ev.preventDefault();
    var p=svgPoint(ev), factor=(ev.deltaY<0)?1.12:(1/1.12);
    zoomAt(p.x,p.y,factor);
  }, {passive:false});
  var dragging=false, lastX=0, lastY=0;
  mapSvg.addEventListener('pointerdown', function(ev){
    if(ev.button!==0)return;
    dragging=true; lastX=ev.clientX; lastY=ev.clientY;
    mapSvg.setPointerCapture(ev.pointerId); mapSvg.style.cursor='grabbing';
  });
  mapSvg.addEventListener('pointermove', function(ev){
    if(!dragging)return;
    panX += (ev.clientX-lastX);
    panY += (ev.clientY-lastY);
    lastX=ev.clientX; lastY=ev.clientY;
    applyView();
  });
  function endDrag(ev){
    dragging=false; mapSvg.style.cursor='grab';
    try{ mapSvg.releasePointerCapture(ev.pointerId); }catch(_e){}
  }
  mapSvg.addEventListener('pointerup', endDrag);
  mapSvg.addEventListener('pointercancel', endDrag);
  // ----- GANTT -----
  var gSvg=document.getElementById('ganttSvg'), GW=960, rowH=30, padL=120, padR=24, topY=22;
  var gH=topY+rowH*rids.length+28, plotGW=GW-padL-padR;
  gSvg.setAttribute('viewBox','0 0 '+GW+' '+gH); gSvg.setAttribute('height',gH);
  function tx(t){return padL+Math.max(0,Math.min(span,t))/span*plotGW;}
  var g='';
  for(var k=0;k<=4;k++){var tt=span*k/4, gx=tx(tt); g+='<line x1="'+gx+'" y1="'+(topY-6)+'" x2="'+gx+'" y2="'+(topY+rowH*rids.length)+'" stroke="#222b37"/>'; g+='<text x="'+gx+'" y="'+(topY-9)+'" fill="#8b97a6" font-size="10" text-anchor="middle">'+tt.toFixed(0)+'s</text>';}
  rids.forEach(function(r,i){ var R=VIZ.robots[r], y=topY+i*rowH+4;
    g+='<text x="'+(padL-8)+'" y="'+(y+15)+'" fill="#e6edf3" font-size="12" text-anchor="end">'+r+'</text>';
    (R.tasks||[]).forEach(function(kk){ var x=tx(kk.t0), w=Math.max(2,tx(kk.t1)-tx(kk.t0)); g+='<rect x="'+x+'" y="'+y+'" width="'+w+'" height="20" rx="2" fill="'+R.color+'" opacity="0.85"><title>'+kk.taskId+'  '+kk.t0+'→'+kk.t1+'s</title></rect>';});
    (R.waits||[]).forEach(function(w2){ var x=tx(w2.t0), ww=Math.max(2,tx(w2.t1)-tx(w2.t0)); g+='<rect x="'+x+'" y="'+y+'" width="'+ww+'" height="20" rx="1" fill="#F44336"><title>调度等待 '+w2.durationSec+'s @('+w2.x+','+w2.y+')</title></rect>';});
  });
  g+='<line id="playhead" x1="'+padL+'" y1="'+(topY-8)+'" x2="'+padL+'" y2="'+(topY+rowH*rids.length+4)+'" stroke="#fff" stroke-width="1.5"/>';
  gSvg.innerHTML=g; var playhead=document.getElementById('playhead');
  // ----- TABLE -----
  function n1(v){ var x=parseFloat(v); return isFinite(x)?x:0; }
  var tb=document.getElementById('taskTbody'), rows='';
  BY.forEach(function(t,i){
    var run=n1((t.taskRunSec!=null)?t.taskRunSec:t.estimateSec), reloc=n1(t.relocSec);
    rows+='<tr data-t0="'+t.startSec+'" data-t1="'+t.finishSec+'"><td>'+String(t.taskId).slice(0,18)+'</td><td>'+t.robot+'</td><td>'+run.toFixed(1)+'</td><td>'+reloc.toFixed(1)+'</td><td>'+t.startSec+'→'+t.finishSec+'</td></tr>';
  });
  tb.innerHTML=rows; var trs=Array.prototype.slice.call(tb.querySelectorAll('tr'));
  trs.forEach(function(tr){ tr.addEventListener('click',function(){ setT(parseFloat(tr.dataset.t0)); }); });
  // ----- interaction -----
  var tlabel=document.getElementById('tlabel');
  var currentT=0.0;
  // 回放倍率定义：x1 = 真实世界 1:1；离散档位按需求固定
  var RATE_STEPS=[1,2,4,6,8,10,20,40,60,80,100];
  var playRate=1.0, MIN_RATE=RATE_STEPS[0], MAX_RATE=RATE_STEPS[RATE_STEPS.length-1];
  var speedLabel=document.getElementById('speedLabel');
  function fmtRate(v){
    var s=(v<1)?v.toFixed(2):v.toFixed(1);
    return s.replace(/\.0$/,'');
  }
  function syncRateLabel(){ if(speedLabel) speedLabel.textContent='sim clock x'+fmtRate(playRate); }
  function prevRate(v){
    for(var i=RATE_STEPS.length-1;i>=0;i--){
      if(RATE_STEPS[i] < v-1e-9) return RATE_STEPS[i];
    }
    return MIN_RATE;
  }
  function nextRate(v){
    for(var i=0;i<RATE_STEPS.length;i++){
      if(RATE_STEPS[i] > v+1e-9) return RATE_STEPS[i];
    }
    return MAX_RATE;
  }
  var speedDownBtn=document.getElementById('speedDownBtn');
  var speedResetBtn=document.getElementById('speedResetBtn');
  var speedUpBtn=document.getElementById('speedUpBtn');
  if(speedDownBtn) speedDownBtn.addEventListener('click', function(){ playRate=prevRate(playRate); syncRateLabel(); });
  if(speedResetBtn) speedResetBtn.addEventListener('click', function(){ playRate=1.0; syncRateLabel(); });
  if(speedUpBtn) speedUpBtn.addEventListener('click', function(){ playRate=nextRate(playRate); syncRateLabel(); });
  function xToT(px){
    px=Math.max(padL, Math.min(padL+plotGW, px));
    return (px-padL)/plotGW*span;
  }
  function tFromEvent(ev){
    var p=gSvg.createSVGPoint();
    p.x=ev.clientX; p.y=ev.clientY;
    p=p.matrixTransform(gSvg.getScreenCTM().inverse());
    return xToT(p.x);
  }
  function interp(tr,t){ if(!tr.length)return null; if(t<=tr[0][0])return {x:tr[0][1],y:tr[0][2],yaw:tr[0][3]}; var last=tr[tr.length-1]; if(t>=last[0])return {x:last[1],y:last[2],yaw:last[3]}; var lo=0; for(var i=0;i<tr.length-1;i++){if(tr[i][0]<=t&&t<=tr[i+1][0]){lo=i;break;}} var a=tr[lo],b=tr[lo+1],f=(t-a[0])/Math.max(1e-6,b[0]-a[0]); var d=b[3]-a[3]; while(d>Math.PI)d-=2*Math.PI; while(d<-Math.PI)d+=2*Math.PI; return {x:a[1]+(b[1]-a[1])*f,y:a[2]+(b[2]-a[2])*f,yaw:a[3]+d*f}; }
  function pathTravel(seg){ var s=0; for(var i=1;i<seg.length;i++){ s+=Math.hypot(seg[i][1]-seg[i-1][1], seg[i][2]-seg[i-1][2]); } return s; }
  function nearDock(sample,d){ return d && (Math.hypot(sample[1]-d.x, sample[2]-d.y) <= DOCK_EPS); }
  function findFirstDockHit(seg,start){ for(var i=Math.max(0,start||0); i<seg.length; i++){ for(var j=0;j<dockList.length;j++){ if(nearDock(seg[i],dockList[j])) return {idx:i,name:dockList[j].name}; } } return null; }
  function toPts(seg){ return seg.map(function(s){ return ex(s[1]).toFixed(1)+','+ey(s[2]).toFixed(1); }).join(' '); }
  function splitTaskPath(R,at){
    var seg=R.trajectory.filter(function(s){return s[0]>=at.t0-0.01 && s[0]<=at.t1+0.01;});
    if(seg.length<2) return {taskSeg:[], relocSeg:[]};
    var iA=0, iB=seg.length-1;
    var fd=(at.fromDock && dockMap[at.fromDock]) ? dockMap[at.fromDock] : null;
    var td=(at.toDock && dockMap[at.toDock]) ? dockMap[at.toDock] : null;
    if(fd){
      for(var i=0;i<seg.length;i++){ if(nearDock(seg[i],fd)){ iA=i; break; } }
    } else {
      var h0=findFirstDockHit(seg,0);
      if(h0){ iA=h0.idx; }
    }
    if(td){
      for(var k=Math.max(0,iA); k<seg.length; k++){ if(nearDock(seg[k],td)){ iB=k; break; } }
    } else {
      var h1=findFirstDockHit(seg, Math.max(0,iA+1));
      if(h1){ iB=h1.idx; }
    }
    iA=Math.max(0, Math.min(seg.length-1, iA));
    iB=Math.max(iA, Math.min(seg.length-1, iB));
    var reloc=(iA>0) ? seg.slice(0, iA+1) : [];
    var taskSeg=seg.slice(iA, iB+1);
    if(taskSeg.length<2 || pathTravel(taskSeg)<MOVE_EPS){
      taskSeg=seg; reloc=[];
    }
    return {taskSeg:taskSeg, relocSeg:reloc};
  }
  function yawSpeed(tr,t){ if(tr.length<2)return 0; if(t<=tr[0][0]||t>=tr[tr.length-1][0])return 0; var lo=0; for(var i=0;i<tr.length-1;i++){if(tr[i][0]<=t&&t<=tr[i+1][0]){lo=i;break;}} var a=tr[lo],b=tr[lo+1],dt=Math.max(1e-6,b[0]-a[0]); var d=b[3]-a[3]; while(d>Math.PI)d-=2*Math.PI; while(d<-Math.PI)d+=2*Math.PI; return Math.abs(d/dt); }
  function waiting(r,t){ var W=VIZ.robots[r].waits||[]; for(var i=0;i<W.length;i++){if(W[i].t0<=t&&t<W[i].t1)return true;} return false; }
  function activeTask(r,t){ var K=VIZ.robots[r].tasks||[]; for(var i=0;i<K.length;i++){if(K[i].t0<=t&&t<=K[i].t1)return K[i];} return null; }
  function tri(px,py,yaw,s){ var a=-yaw, P=[[s,0],[-s*0.6,s*0.65],[-s*0.6,-s*0.65]]; return P.map(function(p){var rx=p[0]*Math.cos(a)-p[1]*Math.sin(a),ry=p[0]*Math.sin(a)+p[1]*Math.cos(a);return (px+rx).toFixed(1)+','+(py+ry).toFixed(1);}).join(' '); }
  function spreadIcons(items){
    var minPx = 16.0;
    for(var it=0; it<4; it++){
      var moved=false;
      for(var i=0;i<items.length;i++){
        for(var j=i+1;j<items.length;j++){
          var a=items[i], b=items[j];
          var dx=b.sx-a.sx, dy=b.sy-a.sy, d=Math.hypot(dx,dy);
          if(d<1e-6){ dx=1.0; dy=0.0; d=1.0; }
          if(d<minPx){
            var push=(minPx-d)*0.5, ux=dx/d, uy=dy/d;
            a.sx-=ux*push; a.sy-=uy*push;
            b.sx+=ux*push; b.sy+=uy*push;
            moved=true;
          }
        }
      }
      if(!moved) break;
    }
  }
  function update(t){ tlabel.textContent=t.toFixed(1)+'s'; var x=tx(t); playhead.setAttribute('x1',x); playhead.setAttribute('x2',x);
    var ph='', ro='', icons=[];
    rids.forEach(function(r){ var R=VIZ.robots[r], at=activeTask(r,t);
      if(at){
        var segAll=R.trajectory.filter(function(s){return s[0]>=at.t0-0.01 && s[0]<=at.t1+0.01;});
        if(at.taskType==='charge_return'){
          if(segAll.length>1){
            var dc=toPts(segAll);
            ph+='<polyline points="'+dc+'" fill="none" stroke="#0f1419" stroke-width="4.8" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="3,6" opacity="0.46"/>';
            ph+='<polyline points="'+dc+'" fill="none" stroke="'+R.color+'" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="3,6" opacity="0.96"/>';
          }
        } else {
          var sl=splitTaskPath(R,at);
          if(sl.relocSeg.length>1){
            var dr=toPts(sl.relocSeg);
            ph+='<polyline points="'+dr+'" fill="none" stroke="#0f1419" stroke-width="4.6" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="6,5" opacity="0.52"/>';
            ph+='<polyline points="'+dr+'" fill="none" stroke="'+R.color+'" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="6,5" opacity="0.94"/>';
          }
          if(sl.taskSeg.length>1){
            var dtp=toPts(sl.taskSeg);
            ph+='<polyline points="'+dtp+'" fill="none" stroke="#0f1419" stroke-width="4.8" stroke-linecap="round" stroke-linejoin="round" opacity="0.46"/>';
            ph+='<polyline points="'+dtp+'" fill="none" stroke="'+R.color+'" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.98"/>';
          }
        }
      }
      var p=interp(R.trajectory,t);
      if(p){
        var turning=yawSpeed(R.trajectory,t)>0.18, w=waiting(r,t)&&!turning, col=w?'#F44336':R.color;
        icons.push({r:r,p:p,col:col,w:w,sx:ex(p.x),sy:ey(p.y)});
      }
    });
    spreadIcons(icons);
    icons.forEach(function(it){
      ro+='<polygon points="'+tri(it.sx,it.sy,it.p.yaw,9)+'" fill="'+it.col+'" stroke="#0f1419" stroke-width="1"/>';
      ro+='<text x="'+(it.sx+11).toFixed(1)+'" y="'+(it.sy-9).toFixed(1)+'" fill="'+it.col+'" stroke="#0f1419" stroke-opacity="0.88" stroke-width="2.4" paint-order="stroke" font-size="10" font-weight="600">'+it.r+(it.w?' 等待':'')+'</text>';
    });
    pathsG.innerHTML=ph; robotsG.innerHTML=ro;
    trs.forEach(function(tr){ var on=parseFloat(tr.dataset.t0)<=t&&t<=parseFloat(tr.dataset.t1); if(on)tr.classList.add('active-row'); else tr.classList.remove('active-row'); });
  }
  function setT(t){ currentT=Math.max(0,Math.min(span,t)); update(currentT); }
  var playing=false, last=0, btn=document.getElementById('playBtn');
  function loop(ts){ if(!playing)return; if(!last)last=ts; var dt=(ts-last)/1000; last=ts; var t=currentT+dt*playRate; if(t>=span){t=span;playing=false;btn.textContent='▶ 播放';} setT(t); if(playing)requestAnimationFrame(loop); }
  btn.addEventListener('click',function(){ playing=!playing; btn.textContent=playing?'⏸ 暂停':'▶ 播放'; if(playing){ if(currentT>=span)setT(0); last=0; requestAnimationFrame(loop);} });
  var gSeeking=false;
  function beginSeek(ev){
    if(ev.button!==0)return;
    gSeeking=true;
    playing=false; btn.textContent='▶ 播放';
    setT(tFromEvent(ev));
    try{ gSvg.setPointerCapture(ev.pointerId); }catch(_e){}
  }
  function moveSeek(ev){ if(!gSeeking)return; setT(tFromEvent(ev)); }
  function endSeek(ev){
    gSeeking=false;
    try{ gSvg.releasePointerCapture(ev.pointerId); }catch(_e){}
  }
  gSvg.addEventListener('pointerdown', beginSeek);
  gSvg.addEventListener('pointermove', moveSeek);
  gSvg.addEventListener('pointerup', endSeek);
  gSvg.addEventListener('pointercancel', endSeek);
  syncRateLabel();
  applyView();
  update(0);
})();
"""


def _render_viz_section(result: Dict[str, Any]) -> str:
    """交通级 --execute：地图示意 + 时间轴回放 + 甘特(红等待) + 联动任务表。"""
    data = {"viz": result.get("viz") or {}, "byTask": (result.get("makespan") or {}).get("byTask") or []}
    blob = json.dumps(data, ensure_ascii=False)
    waits_n = len((result.get("makespan") or {}).get("waits") or [])
    parts = [
        "<h2>交通级地图回放（地图要素 + 实时位置/路径）</h2>",
        '<div class="leg"><span><i style="background:#8b97a6"></i>实验室底图</span>'
        '<span><i style="background:#5BA0E9"></i>工位停靠点</span>'
        '<span><i style="background:#FFB74D"></i>设备点位</span>'
        '<span><i style="background:#5a6b7d"></i>转折/走廊点</span>'
        '<span><i style="background:#F44336"></i>调度等待（原地停车）</span>'
        '<span>实线=任务执行路径</span><span>虚线=任务切换补位移动</span><span>点虚线=收工回充路径</span>'
        '<span>▲ 小车（颜色=甘特行；红=正在等待）</span></div>',
        '<div class="playbar map-tools"><button id="zoomInBtn" class="pbtn">＋ 放大</button>'
        '<button id="zoomOutBtn" class="pbtn">－ 缩小</button>'
        '<button id="zoomResetBtn" class="pbtn">重置视图</button>'
        '<span class="muted">滚轮缩放，鼠标左键拖动平移</span></div>',
        '<div class="mapwrap"><svg id="mapSvg" width="100%" role="img">'
        '<g id="viewportG"><g id="layoutG"></g><g id="wpG"></g><g id="devG"></g><g id="pathsG"></g><g id="robotsG"></g></g></svg></div>',
        f'<h2>甘特图（红 = 调度等待原地停车，共 {waits_n} 段）</h2>',
        '<div class="gwrap"><div class="gantt-overlay"><button id="playBtn" class="pbtn">▶ 播放</button>'
        '<span id="tlabel" class="muted">0.0s</span>'
        '<button id="speedDownBtn" class="pbtn">－ 变慢</button>'
        '<button id="speedResetBtn" class="pbtn">x1</button>'
        '<button id="speedUpBtn" class="pbtn">＋ 加速</button>'
        '<span id="speedLabel" class="speed-label">sim clock x1</span>'
        '<span class="muted">拖动甘特图中的白色时间线（或点击时间位置）；速度档位：x1/x2/x4/x6/x8/x10/x20/x40/x60/x80/x100</span></div>'
        '<svg id="ganttSvg" width="100%" role="img"></svg></div>',
        '<h2>任务明细（拖甘特图时间轴高亮 · 点击跳到任务开头）</h2>'
        '<div class="tbl-wrap"><table id="taskTbl"><thead><tr><th>taskId</th><th>robot</th><th>A→B执行(s)</th><th>补位(s)</th><th>窗口(s)</th></tr></thead>'
        '<tbody id="taskTbody"></tbody></table></div>',
        f"<script>var DATA={blob};</script>",
        f"<script>{_VIZ_JS}</script>",
    ]
    return "".join(parts)


def render_html(result: Dict[str, Any], args: argparse.Namespace) -> str:
    meta = result.get("meta", {})
    fleet = meta.get("fleet", {})

    cards = [
        ("模式", "多机规划"),
        ("dispatch", html.escape(str(meta.get("dispatchMode", "")))),
        ("车数(请求/实际)", f'{fleet.get("robotsRequested","?")} / {fleet.get("robotsActual","?")}'),
        ("最大速度", f'{fleet.get("maxLinearSpeed","?")} m/s'),
        ("加速度", f'{fleet.get("linearAccel","?")} m/s²'),
        ("角速度", f'{fleet.get("maxAngularSpeed","?")} rad/s'),
    ]
    card_html = "".join(f'<div class="card"><div class="k">{html.escape(k)}</div><div class="v">{v}</div></div>' for k, v in cards)

    body = [f'<h1>RMF 路径时间预估报告 <span class="badge">{html.escape(meta.get("mode",""))}</span></h1>']
    body.append(f'<div class="muted">生成于 {html.escape(str(meta.get("generatedAt","")))} · api {html.escape(str(meta.get("apiUrl","")))} · 默认源 {html.escape(str(fleet.get("usedDefaultsFrom","")))}</div>')
    body.append(f'<div class="cards">{card_html}</div>')
    if fleet.get("note"):
        body.append(f'<div class="note">{html.escape(str(fleet["note"]))}</div>')

    ms = result.get("makespan", {})
    kind = ms.get("kind", "")
    badge = '<span class="badge warn">含交通竞争</span>' if ms.get("trafficAccurate") else '<span class="badge">分配+排队估时（不含实时避让）</span>'
    body.append("<h2>多机规划 makespan</h2>")
    body.append(f'<div class="big">{_fmt_mmss(ms.get("totalSec",0))}</div>')
    body.append(f'<div class="muted">总时间 {ms.get("totalSec",0):.1f}s · {ms.get("robots",0)} 车 · {len(ms.get("byTask",[]))} 任务 · kind={html.escape(kind)} {badge}</div>')
    run_total = sum(float(t.get("taskRunSec", t.get("estimateSec", 0.0)) or 0.0) for t in ms.get("byTask", []))
    reloc_total = sum(float(t.get("relocSec", 0.0) or 0.0) for t in ms.get("byTask", []))
    body.append('<h2>每车负载（A→B任务执行累计）</h2>')
    body.append(_svg_bars(sorted(ms.get("perRobot", {}).items()), unit="s"))
    if ms.get("trafficAccurate"):
        body.append(
            f'<div class="muted">任务执行累计 {run_total:.1f}s · 补位累计 {reloc_total:.1f}s（总 makespan 仍按真实执行窗口统计）</div>'
        )
    if result.get("viz"):
        # 交通级 --execute：交互式回放（地图 + 时间轴 + 红等待甘特 + 联动表）
        body.append(_render_viz_section(result))
    else:
        body.append('<h2>甘特图（任务按起止时刻）</h2>')
        body.append(_svg_gantt(ms.get("byTask", [])))
        body.append('<h2>任务明细（合成排队调度）</h2><div class="tbl-wrap">')
        rows = [[html.escape(t["taskId"]), html.escape(t["robot"]), f'{t.get("estimateSec",0):.1f}', f'{t.get("startSec",0):.1f}→{t.get("finishSec",0):.1f}'] for t in ms.get("byTask", [])]
        body.append(_table(["taskId", "robot", "估时(s)", "排队窗口(s)"], rows, "tasksTbl"))
        body.append("</div>")

    body.append('<div class="foot">本报告由 scripts/rmf_estimate_times.py 生成（数据源 = 同名 JSON）。设计：product_designs/.../24.9。</div>')
    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>RMF 路径时间预估报告</title>"
        f"<style>{_HTML_CSS}</style></head><body><div class='wrap'>"
        + "".join(body)
        + f"</div><script>{_HTML_JS}</script></body></html>"
    )


# ============================================================ 启动栈（edge-free；绝不起 OS edge）
_LAUNCHER: Optional[RmfRuntimeLauncher] = None
_LAUNCH_OPTIONS: Optional[RmfRuntimeOptions] = None


def _rmf_run_logs_dir(map_dir: str) -> Optional[Path]:
    p = Path(map_dir).resolve()
    for cand in [p, *p.parents]:
        if (cand / "run_rmfweb.sh").exists():
            return cand
    return None


def _dock_start_xy(map_dir: str, waypoint: str = "dock_96_0") -> Tuple[float, float]:
    """从 nav_graphs/0.yaml 读起始 dock（fleet_config start waypoint）的边缘帧坐标。"""
    try:
        import yaml

        g = yaml.safe_load((Path(map_dir) / "nav_graphs" / "0.yaml").read_text(encoding="utf-8"))
        for v in g["levels"]["L1"]["vertices"]:
            if len(v) > 2 and isinstance(v[2], dict) and v[2].get("name") == waypoint:
                return float(v[0]), float(v[1])
    except Exception:  # noqa: BLE001
        pass
    return 55.66, -24.70  # 兜底


def _pick_start_docks(map_dir: str, n: int) -> List[Tuple[str, float, float]]:
    """从 nav_graph 0.yaml 选 n 个**互不相同**的 dock 顶点作 n 车起点 → [(waypoint, x, y)]。"""
    docks: List[Tuple[str, float, float]] = []
    try:
        import yaml

        g = yaml.safe_load((Path(map_dir) / "nav_graphs" / "0.yaml").read_text(encoding="utf-8"))
        for v in g["levels"]["L1"]["vertices"]:
            if len(v) > 2 and isinstance(v[2], dict):
                name = v[2].get("name")
                if name and str(name).startswith("dock_"):
                    docks.append((str(name), float(v[0]), float(v[1])))
    except Exception:  # noqa: BLE001
        pass
    # 优先 dock_96_*（稳定、彼此分开），不足再用其它 dock_*，仍不足则循环兜底
    preferred = [d for d in docks if d[0].startswith("dock_96_")] + [d for d in docks if not d[0].startswith("dock_96_")]
    if not preferred:
        sx, sy = _dock_start_xy(map_dir)
        preferred = [("dock_96_0", sx, sy)]
    return [preferred[i % len(preferred)] for i in range(max(1, n))]


def _demote_star_holding_points(map_dir: str) -> int:
    """主干 star_* 交汇点不作为 holding point，避免在主路口驻停占位。"""
    nav_path = Path(map_dir) / "nav_graphs" / "0.yaml"
    if not nav_path.exists():
        return 0
    try:
        import yaml

        graph = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
        verts = ((((graph.get("levels") or {}).get("L1") or {}).get("vertices") or []))
        changed = 0
        for v in verts:
            if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
                continue
            props = v[2]
            name = str(props.get("name") or "")
            if name.startswith("star_") and props.get("is_holding_point"):
                props.pop("is_holding_point", None)
                changed += 1
        if changed > 0:
            nav_path.write_text(yaml.safe_dump(graph, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return changed
    except Exception:  # noqa: BLE001
        return 0


def _demote_non_charge_dock_holding_points(map_dir: str) -> int:
    """非充电 dock 不作为 holding point，避免两车在同一 dock 上驻停重叠。"""
    nav_path = Path(map_dir) / "nav_graphs" / "0.yaml"
    if not nav_path.exists():
        return 0
    try:
        import yaml

        graph = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
        verts = ((((graph.get("levels") or {}).get("L1") or {}).get("vertices") or []))
        changed = 0
        for v in verts:
            if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
                continue
            props = v[2]
            name = str(props.get("name") or "")
            if not name.startswith("dock_"):
                continue
            # 充电点（含 is_charger）保留 holding，避免回充流程被破坏。
            if name.startswith("dock_charge_") or bool(props.get("is_charger")):
                continue
            if props.get("is_holding_point"):
                props.pop("is_holding_point", None)
                changed += 1
        if changed > 0:
            nav_path.write_text(yaml.safe_dump(graph, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return changed
    except Exception:  # noqa: BLE001
        return 0


def _clear_hotspot_mutex(map_dir: str) -> int:
    """清理本脚本注入的热点 mutex，避免跨轮次遗留污染。"""
    nav_path = Path(map_dir) / "nav_graphs" / "0.yaml"
    if not nav_path.exists():
        return 0
    try:
        import yaml

        graph = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
        level = ((graph.get("levels") or {}).get("L1") or {})
        verts = level.get("vertices") or []
        lanes = level.get("lanes") or []

        changed = 0
        for lane in lanes:
            if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
                continue
            props = lane[2]
            if str(props.get("mutex") or "") not in {
                "hotspot_right_mutex",
                "star_corridor_mutex",
                "hotspot_focus_mutex",
            }:
                continue
            props.pop("mutex", None)
            changed += 1

        if changed > 0:
            nav_path.write_text(yaml.safe_dump(graph, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return changed
    except Exception:  # noqa: BLE001
        return 0


def _clear_dock_access_mutex(map_dir: str) -> int:
    """清理本脚本注入的 dock_access_* mutex，避免跨轮次遗留污染。"""
    nav_path = Path(map_dir) / "nav_graphs" / "0.yaml"
    if not nav_path.exists():
        return 0
    try:
        import yaml

        graph = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
        level = ((graph.get("levels") or {}).get("L1") or {})
        lanes = level.get("lanes") or []
        changed = 0
        for lane in lanes:
            if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
                continue
            props = lane[2]
            mutex = str(props.get("mutex") or "")
            if not mutex.startswith("dock_access_"):
                continue
            props.pop("mutex", None)
            changed += 1
        if changed > 0:
            nav_path.write_text(yaml.safe_dump(graph, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return changed
    except Exception:  # noqa: BLE001
        return 0


def _apply_dock_access_mutex(map_dir: str) -> int:
    """为 dock 接入车道注入按 dock 分组的 mutex，避免两车同点 dock 重叠。"""
    nav_path = Path(map_dir) / "nav_graphs" / "0.yaml"
    if not nav_path.exists():
        return 0
    try:
        import yaml

        graph = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
        level = ((graph.get("levels") or {}).get("L1") or {})
        verts = level.get("vertices") or []
        lanes = level.get("lanes") or []

        dock_by_idx: Dict[int, str] = {}
        for idx, v in enumerate(verts):
            if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
                continue
            props = v[2]
            name = str(props.get("name") or "")
            if not name.startswith("dock_"):
                continue
            # 充电点由 finishing_request=charge 管理，保持原图行为。
            if name.startswith("dock_charge_") or bool(props.get("is_charger")):
                continue
            dock_by_idx[idx] = name
        if not dock_by_idx:
            return 0

        changed = 0
        for lane in lanes:
            if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
                continue
            src = int(lane[0]) if isinstance(lane[0], (int, float)) else None
            dst = int(lane[1]) if isinstance(lane[1], (int, float)) else None
            if src is None or dst is None:
                continue
            dock_name = dock_by_idx.get(src) or dock_by_idx.get(dst)
            if not dock_name:
                continue
            target_mutex = f"dock_access_{dock_name}"
            props = lane[2]
            current_mutex = str(props.get("mutex") or "")
            # 保留原图已有互斥（非本脚本注入），避免破坏人工设计。
            if current_mutex and (not current_mutex.startswith("dock_access_")) and current_mutex != target_mutex:
                continue
            if current_mutex != target_mutex:
                props["mutex"] = target_mutex
                changed += 1

        if changed > 0:
            nav_path.write_text(yaml.safe_dump(graph, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return changed
    except Exception:  # noqa: BLE001
        return 0


def _apply_hotspot_mutex(map_dir: str) -> int:
    """为已确认热点节点加 mutex（RMF 原生），减小同点占位冲突。"""
    nav_path = Path(map_dir) / "nav_graphs" / "0.yaml"
    if not nav_path.exists():
        return 0
    try:
        import yaml

        graph = yaml.safe_load(nav_path.read_text(encoding="utf-8")) or {}
        level = ((graph.get("levels") or {}).get("L1") or {})
        verts = level.get("vertices") or []
        lanes = level.get("lanes") or []
        hotspot_names = {
            "star_l5_right",
        }
        hotspot_indices: set[int] = set()
        for idx, v in enumerate(verts):
            if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
                continue
            name = str(v[2].get("name") or "")
            if name in hotspot_names:
                hotspot_indices.add(idx)
        if not hotspot_indices:
            return 0

        changed = 0
        for lane in lanes:
            if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
                continue
            src = int(lane[0]) if isinstance(lane[0], (int, float)) else None
            dst = int(lane[1]) if isinstance(lane[1], (int, float)) else None
            if src is None or dst is None:
                continue
            if src not in hotspot_indices and dst not in hotspot_indices:
                continue
            props = lane[2]
            if str(props.get("mutex") or "") == "hotspot_focus_mutex":
                continue
            # 若已有其它互斥组，不覆盖（保留原图设计）；否则注入热点互斥。
            if str(props.get("mutex") or "").strip():
                continue
            props["mutex"] = "hotspot_focus_mutex"
            changed += 1

        if changed > 0:
            nav_path.write_text(yaml.safe_dump(graph, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return changed
    except Exception:  # noqa: BLE001
        return 0


def teardown_stack() -> None:
    """关闭本脚本通过统一 launcher 拉起的 edge-free 栈。"""
    global _LAUNCHER, _LAUNCH_OPTIONS
    if _LAUNCHER is None or _LAUNCH_OPTIONS is None:
        return
    try:
        _LAUNCHER.stop_runtime_stack(_LAUNCH_OPTIONS, include_shell_stop=False)
    finally:
        _LAUNCHER = None
        _LAUNCH_OPTIONS = None


def ensure_stack(args: argparse.Namespace) -> None:
    """连不上 :8000 时：--launch 则用统一 runtime launcher 起 edge-free 栈。"""
    global _LAUNCHER, _LAUNCH_OPTIONS
    if api_reachable(args.api_url, args.token):
        print("[rmf] 已连上运行中的 api-server（不自动起栈）", flush=True)
        return
    if not args.launch:
        print(
            f"[error] 连不上 RMF api-server（{args.api_url}）。edge-free 起栈二选一：\n"
            "  A) 加 --launch（脚本自动起 api-server + RMF core headless + mock + standalone fleet_manager，不起 OS edge）；\n"
            "  B) 手动起这 4 个，再不带 --launch 连 :8000。",
            file=sys.stderr,
        )
        sys.exit(2)
    run_dir = _rmf_run_logs_dir(args.map_dir)
    if run_dir is None:
        print("[error] --launch 但找不到 run_rmfweb.sh（检查 --map-dir）", file=sys.stderr)
        sys.exit(2)
    # 全量 dock_access mutex（数百条）会把协商约束推得过重，反而出现 negotiation timeout
    # 与同 dock 重合；这里只清理遗留，不再全量注入。避让交给 RMF traffic + holding 调整。
    changed_star_holding = _demote_star_holding_points(args.map_dir)
    changed_dock_holding = _demote_non_charge_dock_holding_points(args.map_dir)
    cleared_hotspot_mutex = _clear_hotspot_mutex(args.map_dir)
    cleared_dock_mutex = _clear_dock_access_mutex(args.map_dir)
    if changed_star_holding > 0:
        print(f"[launch] nav_graph 调整：已移除 {changed_star_holding} 个 star_* holding_point", flush=True)
    if changed_dock_holding > 0:
        print(f"[launch] nav_graph 调整：已移除 {changed_dock_holding} 个非充电 dock holding_point", flush=True)
    if cleared_hotspot_mutex > 0:
        print(f"[launch] nav_graph 调整：已清理 {cleared_hotspot_mutex} 条热点车道 mutex", flush=True)
    if cleared_dock_mutex > 0:
        print(f"[launch] nav_graph 调整：已清理 {cleared_dock_mutex} 条 dock_access mutex", flush=True)

    speed = float(getattr(args, "max_linear_speed", None) or FLEET_DEFAULTS["maxLinearSpeed"])
    accel = float(getattr(args, "linear_accel", None) or FLEET_DEFAULTS["linearAccel"])
    ang_speed = float(getattr(args, "max_angular_speed", None) or FLEET_DEFAULTS["maxAngularSpeed"])
    ang_accel = float(getattr(args, "angular_accel", None) or FLEET_DEFAULTS["angularAccel"])
    # 纯 RMF：不做执行层最小间距回滚（那是假物理兜底）。避障交给 RMF traffic scheduler。
    min_sep = 0.0
    n_robots = max(1, int(getattr(args, "robots", 1) or 1))
    picks = _pick_start_docks(args.map_dir, n_robots)          # [(dock, x, y), ...]（互不相同）
    start_docks = [s.strip() for s in str(getattr(args, "start_docks", "") or "").split(",") if s.strip()]
    if start_docks:  # --start-docks 覆盖：按名取边缘帧坐标，不足则用默认补齐
        override = [(nm, *_dock_start_xy(args.map_dir, nm)) for nm in start_docks[:n_robots]]
        picks = override + picks[len(override):]
    robot_names = [f"unilab_agv{i + 1}" for i in range(n_robots)]
    sim = bool(args.execute)
    ff = float(getattr(args, "fast_forward", None) or 10.0)
    fleet_poll_hz = max(10.0, min(200.0, ff * 10.0)) if sim else 10.0
    # 冲突接近时降速，给 RMF 协商留墙钟时间（不改小车物理尺寸/速度）。
    adaptive_slow_scale = 0.5
    adaptive_danger_enter = 6.5
    adaptive_danger_exit = 8.0
    robot_specs = [f"{name}:{x}:{y}" for name, (_dock, x, y) in zip(robot_names, picks)]
    options = RmfRuntimeOptions(
        runtime_root=str(run_dir),
        map_dir=str(Path(args.map_dir).resolve()),
        mode="headless",
        use_sim_time=sim,
        stop_before_start=True,
        start_api_server=True,
        start_rmf_core=True,
        bridge_mode="sim_bridge" if sim else "mock",
        robots=robot_names,
        robot_specs=robot_specs,
        edge_port=8090,
        fleet_manager_port=22011,
        fleet_poll_hz=fleet_poll_hz,
        min_separation_m=min_sep,
        nominal_velocity=speed,
        linear_speed=speed,
        linear_accel=accel,
        angular_speed=ang_speed,
        angular_accel=ang_accel,
        sim_scale=ff,
        adaptive_enabled=True,
        adaptive_slow_scale=adaptive_slow_scale,
        adaptive_danger_enter=adaptive_danger_enter,
        adaptive_danger_exit=adaptive_danger_exit,
        adaptive_monitor_hz=20.0,
        min_registered_robots=n_robots,
        api_url=str(args.api_url),
        api_token=str(args.token),
        logs_dir=str(run_dir),
        wait_api_timeout_s=180.0,
        wait_fleet_timeout_s=180.0,
    )
    launcher = RmfRuntimeLauncher()
    result = launcher.start_runtime_stack(options)
    if not bool(result.get("success", False)):
        print(f"[error] runtime launcher 启动失败: {result.get('error')}", file=sys.stderr)
        sys.exit(2)
    _LAUNCHER = launcher
    _LAUNCH_OPTIONS = options
    print(
        f"[launch] edge-free 栈已拉起（{n_robots} 车，{'sim 快进 x' + str(ff) if sim else '墙钟'}，"
        f"fleet poll {fleet_poll_hz:.1f}Hz，纯 RMF 避障，不起 OS edge）",
        flush=True,
    )


# ============================================================ main
def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="脱离 edge，用 RMF 估算路径时间 + makespan（默认实时避让）→ JSON + HTML")
    ap.add_argument("--routes", help="rmf_agv_routes.json（instanceId→dock 名映射用；缺省从 --map-dir 找）")
    ap.add_argument("--paths", help="rmf_transfer_paths.json（多机规划用）")
    ap.add_argument("--map-dir", default="../.rmf_run_logs/maps/latest", help="地图/fleet_config 目录")
    ap.add_argument("--robots", type=int, default=1, help="车数（主输入；缺省 1）")
    ap.add_argument("--start-docks", default="", help="逗号分隔的各车起始 dock（覆盖默认 dock_96_0/1...；如 dock_lc_ms_9,dock_96quench_2）")
    ap.add_argument("--max", type=int, default=0, help="只取前 N 条（0=全部）")
    ap.add_argument("--dispatch-mode", choices=["delivery", "patrol"], default="patrol")
    ap.add_argument("--max-linear-speed", type=float)
    ap.add_argument("--linear-accel", type=float)
    ap.add_argument("--max-angular-speed", type=float)
    ap.add_argument("--angular-accel", type=float)
    ap.add_argument("--footprint", type=float)
    ap.set_defaults(execute=True)  # 默认交通级（实时避让）
    ap.add_argument("--execute", dest="execute", action="store_true", help="执行任务并统计交通级 makespan（默认）")
    ap.add_argument("--planned-only", dest="execute", action="store_false", help="仅分配+估时（不执行任务，不含实时避让）")
    ap.set_defaults(return_to_charge=True)
    ap.add_argument("--return-to-charge", dest="return_to_charge", action="store_true", help="执行完后空闲车自动回充（默认）")
    ap.add_argument("--no-return-to-charge", dest="return_to_charge", action="store_false", help="关闭自动回充")
    ap.add_argument("--charge-docks", default="", help="逗号分隔的回充 dock（按机器人顺序）；缺省自动选右上角")
    ap.add_argument("--idle-charge-timeout", type=float, default=120.0, help="execute 模式：机器人无任务达到该时长(sim秒)后再触发回充（0=立即）")
    ap.add_argument("--fast-forward", type=float, default=10.0, help="仅 --execute：sim 快进倍率（rig 见 24.9 §7.2）")
    ap.add_argument("--wait-timeout", type=float, default=900.0, help="轮询分配/终态的超时秒（默认 900，适配实时避让）")
    ap.add_argument("--dispatch-gap", type=float, default=-1.0, help="任务间派发间隔秒（<0=自动=0）")
    ap.add_argument("--api-url", default="http://127.0.0.1:8000")
    ap.add_argument("--edge-url", default="http://127.0.0.1:8090", help="mock 车 HTTP（--execute 采集实时位姿轨迹）")
    ap.add_argument("--token", default=JWT_DEFAULT)
    ap.add_argument("--launch", action="store_true", help="连不上 :8000 时自动起 edge-free 栈（api-server+RMF headless+mock+standalone fleet_manager；不起 OS edge）")
    ap.add_argument("--out", default="../.rmf_run_logs/maps/latest/rmf_time_estimates.json")
    ap.add_argument("--html", default="", help="HTML 报告路径；缺省=与 --out 同名 .html")
    ap.add_argument("--no-html", action="store_true", help="不生成 HTML 报告")
    ap.add_argument("--embed-layout", action="store_true", help="（预留）HTML 内嵌布局图")
    ap.add_argument("--keep-alive", action="store_true", help="（预留）结束后不关 RMF")
    return ap


def main() -> None:
    args = build_argparser().parse_args()

    if not args.paths:
        print("[error] 多机规划需要 --paths <transfer 文件（rmf_transfer_paths.json / transfers.json）>", file=sys.stderr)
        sys.exit(2)

    ensure_stack(args)

    try:
        fleet_params = load_fleet_params(args.map_dir, args)
        fleet_actual = query_fleet_robots(args.api_url, args.token)
        if sum(len(v) for v in fleet_actual.values()) < max(1, args.robots):
            print(f"[rmf] 等待 fleet_adapter 注册 {max(1, args.robots)} 台机器人 ...", flush=True)
            fleet_actual = wait_for_fleet(args.api_url, args.token, min_robots=max(1, args.robots), timeout_s=180.0)
        robots_actual = sum(len(v) for v in fleet_actual.values())
        print(f"[rmf] 运行中车队：{fleet_actual or '（空/未就绪）'}（实际 {robots_actual} 台）", flush=True)
        if args.robots != robots_actual:
            print(
                f"[warn] --robots={args.robots} ≠ 运行中 {robots_actual} 台。多机分配按运行中的车队进行；"
                "要让车数生效需以覆盖后的 fleet_config（N 车）起 RMF（见 24.9 §4）。",
                flush=True,
            )

        core = run_multi(args.api_url, args.token, args, fleet_params)
        result = build_result(args, fleet_params, fleet_actual, core)
        out_path, html_path = write_outputs(result, args)

        m = result["meta"]
        ms = result["makespan"]
        print("\n========== 结果 ==========", flush=True)
        print(f"makespan：{ms['totalSec']:.1f}s（{_fmt_mmss(ms['totalSec'])}）· {ms['robots']} 车 · {m.get('ready', 0)}/{m.get('dispatched', 0)} 就绪 · kind={ms['kind']}", flush=True)
        print(f"JSON → {out_path}", flush=True)
        if html_path:
            print(f"HTML → {html_path}", flush=True)
    finally:
        if args.launch and not args.keep_alive and _LAUNCHER is not None and _LAUNCH_OPTIONS is not None:
            print("[teardown] 收尾：停掉本脚本拉起的 edge-free 栈 ...", flush=True)
            teardown_stack()


if __name__ == "__main__":
    main()
