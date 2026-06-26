#!/usr/bin/env python3
"""OS 侧「车队主」HTTP 控制层（#18 §10.4）—— **Uni-Lab-OS 接收 RMF 指令并驱动小车**。

车队主（fleet owner）= 实验室 = Uni-Lab-OS。RMF 的 `fleet_adapter` 把导航/停止指令发到
车队主暴露的 HTTP 接口（rmf_demos 契约），车队主再把指令下发给 AGV 硬件、并回报位姿。

- **RMF 侧**：监听 `fleet_config.rmf_fleet.fleet_manager.port`，暴露
  `GET/POST /open-rmf/rmf_demos_fm/{status,navigate,stop_robot}`，未改的 `rmf_demos_fleet_adapter`
  的 `RobotClientAPI` 可直接对接。
- **AGV 侧**：navigate → `POST {edge}/agv/navigate`；后台轮询 `GET {edge}/agv/state` 缓存位姿。
- **零 ROS / 零 `rmf_msgs`**，纯 HTTP / 墙钟（#18 §10.4.1）。

两种用法：
1. **OS 进程内**（推荐，#18 §10）：`rmf.coordinator` 设备在 `initialize()` 里 `EdgeFleetManager(...).start()`，
   于是 **OS 进程本身即车队主**，RMF 指令进 OS、OS 驱动小车（日志走 OS logger，OS 窗口可见）。
2. **独立 CLI**（自测）：`python -m unilabos.sim.fleet.rmf.edge.fleet_manager_http --port 22011 --edge-url http://127.0.0.1:8090`。
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests

DEFAULT_EDGE_URL = "http://127.0.0.1:8090"
DEFAULT_NOMINAL_V = 0.5  # m/s（fleet_config limits.linear[0]），用于 destination_arrival 估时
DEFAULT_POLL_HZ = 10.0


class RobotBridge:
    """单机器人桥状态：缓存 edge 位姿 + 跟踪 destination/cmd 完成（复刻 rmf_demos 完成判定）。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.level = "L1"
        self.battery = 100.0  # rmf battery_percent 0..100
        self.mode = "idle"
        self.last_cmd_id = 0
        self.destination: Optional[Dict[str, float]] = None
        self.last_completed_request = 0
        self._lock = threading.Lock()

    def update_from_edge(self, st: Dict[str, Any]) -> None:
        with self._lock:
            self.x = float(st.get("x", self.x))
            self.y = float(st.get("y", self.y))
            self.yaw = float(st.get("yaw", self.yaw))
            self.level = st.get("level", self.level)
            b = st.get("battery")
            if isinstance(b, (int, float)):
                self.battery = float(b) * 100.0 if b <= 1.0 else float(b)
            self.mode = st.get("status", self.mode)
            remaining = int(st.get("remainingPath", 0) or 0)
            # 完成判定：有目标、且 edge 空闲且无剩余路径 → 该 cmd 完成
            if self.destination is not None and self.mode == "idle" and remaining == 0:
                self.last_completed_request = self.last_cmd_id
                self.destination = None

    def set_destination(self, cmd_id: int, x: float, y: float, yaw: float, level: str) -> None:
        with self._lock:
            self.last_cmd_id = int(cmd_id)
            self.destination = {"x": float(x), "y": float(y), "yaw": float(yaw)}
            self.level = level or self.level

    def clear_destination(self, cmd_id: int) -> None:
        with self._lock:
            self.last_cmd_id = int(cmd_id)
            self.destination = None

    def status_data(self, nominal_v: float) -> Dict[str, Any]:
        """复刻 rmf_demos get_robot_state 的 data 形状（无时间戳）。"""
        with self._lock:
            data: Dict[str, Any] = {
                "robot_name": self.name,
                "map_name": self.level,
                "position": {"x": round(self.x, 3), "y": round(self.y, 3), "yaw": round(self.yaw, 4)},
                "battery": round(self.battery, 2),
                "last_completed_request": self.last_completed_request,
            }
            if self.destination is not None:
                dx = self.destination["x"] - self.x
                dy = self.destination["y"] - self.y
                duration = math.hypot(dx, dy) / max(nominal_v, 1e-3)
                data["destination_arrival"] = {"cmd_id": self.last_cmd_id, "duration": duration}
            else:
                data["destination_arrival"] = None
            return data


class EdgeFleetManager:
    """车队主控制层：接收 RMF `fleet_adapter` 指令 → 驱动 edge AGV 硬件（#18 §10.4）。

    可在 OS 设备进程内 `start()`（OS 即车队主），或作为独立进程（CLI）运行。
    `log(msg, level)`：注入日志（OS 设备传入 unilabos logger；CLI 默认 print）。
    """

    def __init__(
        self,
        edge_url: str = DEFAULT_EDGE_URL,
        robot_names: Optional[List[str]] = None,
        *,
        nominal_velocity: float = DEFAULT_NOMINAL_V,
        poll_hz: float = DEFAULT_POLL_HZ,
        log: Optional[Callable[..., None]] = None,
    ) -> None:
        self.edge_url = edge_url.rstrip("/")
        self.nominal_v = float(nominal_velocity)
        self.poll_hz = float(poll_hz)
        self._log = log
        self.robots: Dict[str, RobotBridge] = {n: RobotBridge(n) for n in (robot_names or ["unilab_agv1"])}
        self._stop = threading.Event()
        self._server: Optional[ThreadingHTTPServer] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._srv_thread: Optional[threading.Thread] = None

    def log(self, msg: str, level: str = "info") -> None:
        if self._log is not None:
            try:
                self._log(msg, level)
                return
            except TypeError:
                self._log(msg)  # type: ignore[misc]
                return
        print(msg, flush=True)

    # ------------------------------------------------------------ edge HTTP（OS → 小车硬件）
    def _edge_get_state(self, robot: str) -> Optional[Dict[str, Any]]:
        try:
            r = requests.get(f"{self.edge_url}/agv/state", params={"robot": robot}, timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception:  # noqa: BLE001
            return None
        return None

    def _edge_navigate(self, robot: str, cmd_id: int, x: float, y: float, yaw: float, level: str) -> bool:
        try:
            r = requests.post(
                f"{self.edge_url}/agv/navigate",
                json={"robot": robot, "cmdId": cmd_id, "destination": {"x": x, "y": y, "yaw": yaw, "level": level}},
                timeout=5,
            )
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def _edge_stop(self, robot: str, cmd_id: int) -> bool:
        try:
            r = requests.post(f"{self.edge_url}/agv/stop", json={"robot": robot, "cmdId": cmd_id}, timeout=5)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def _poll_loop(self) -> None:
        dt = 1.0 / max(self.poll_hz, 1e-3)
        while not self._stop.is_set():
            for name, rb in list(self.robots.items()):
                st = self._edge_get_state(name)
                if st:
                    rb.update_from_edge(st)
            self._stop.wait(dt)

    # ------------------------------------------------------------ 对外控制 API（供 OS 设备 status）
    def robot_states(self) -> List[Dict[str, Any]]:
        return [rb.status_data(self.nominal_v) for rb in self.robots.values()]

    # ------------------------------------------------------------ 生命周期
    def start(self, host: str = "127.0.0.1", port: int = 22011) -> None:
        for n, rb in self.robots.items():
            st = self._edge_get_state(n)
            if st:
                rb.update_from_edge(st)
        self._poll_thread = threading.Thread(target=self._poll_loop, name="os-fm-poll", daemon=True)
        self._poll_thread.start()
        self._server = ThreadingHTTPServer((host, port), self._make_handler())
        self._srv_thread = threading.Thread(target=self._server.serve_forever, name="os-fm-http", daemon=True)
        self._srv_thread.start()
        self.log(
            f"[os-fleet] 车队主已就位：监听 RMF 指令 http://{host}:{port}/open-rmf/rmf_demos_fm/*，"
            f"驱动 edge 小车 {self.edge_url}，robots={list(self.robots)}"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:  # noqa: BLE001
                pass
        self._server = None

    def serve_forever(self, host: str = "127.0.0.1", port: int = 22011) -> None:
        """阻塞式（CLI 用）。"""
        self.start(host, port)
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop()

    # ------------------------------------------------------------ RMF 契约 handler
    def _make_handler(self):
        mgr = self

        class FmHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:
                return

            def _send(self, code: int, payload) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    return json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    return {}

            def do_GET(self) -> None:
                u = urlparse(self.path)
                q = parse_qs(u.query)
                path = u.path.rstrip("/")
                if path == "/open-rmf/rmf_demos_fm/status":
                    name = q.get("robot_name", [None])[0]
                    resp = {"data": {}, "success": False, "msg": ""}
                    if name is None:
                        resp["data"]["all_robots"] = mgr.robot_states()
                        resp["success"] = True
                    else:
                        rb = mgr.robots.get(name)
                        if rb is None:
                            self._send(200, resp)
                            return
                        resp["data"] = rb.status_data(mgr.nominal_v)
                        resp["success"] = True
                    self._send(200, resp)
                    return
                if path == "/open-rmf/rmf_demos_fm/stop_robot":
                    name = q.get("robot_name", [None])[0]
                    cmd_id = int(q.get("cmd_id", [0])[0])
                    rb = mgr.robots.get(name) if name else None
                    ok = False
                    if rb is not None:
                        rb.clear_destination(cmd_id)
                        ok = mgr._edge_stop(name, cmd_id)
                        mgr.log(f"[os-fleet] RMF→OS 停止 {name} cmd={cmd_id} → 驱动小车 stop ok={ok}")
                    self._send(200, {"success": bool(ok), "msg": ""})
                    return
                self._send(404, {"success": False, "msg": "not found"})

            def do_POST(self) -> None:
                u = urlparse(self.path)
                q = parse_qs(u.query)
                path = u.path.rstrip("/")
                body = self._body()
                if path == "/open-rmf/rmf_demos_fm/navigate":
                    name = q.get("robot_name", [None])[0]
                    cmd_id = int(q.get("cmd_id", [0])[0])
                    rb = mgr.robots.get(name) if name else None
                    dest = body.get("destination") or {}
                    if rb is None or "x" not in dest:
                        self._send(200, {"success": False, "msg": "bad robot/destination"})
                        return
                    x, y, yaw = float(dest["x"]), float(dest["y"]), float(dest.get("yaw", 0.0))
                    level = body.get("map_name") or rb.level
                    rb.set_destination(cmd_id, x, y, yaw, level)
                    ok = mgr._edge_navigate(name, cmd_id, x, y, yaw, level)
                    mgr.log(
                        f"[os-fleet] RMF→OS 导航指令 {name} cmd={cmd_id} → OS 驱动小车 navigate "
                        f"to ({x:.2f},{y:.2f}) ok={ok}"
                    )
                    self._send(200, {"success": bool(ok), "msg": ""})
                    return
                if path == "/open-rmf/rmf_demos_fm/start_task":
                    self._send(200, {"success": False, "msg": "start_task not supported (use navigate)"})
                    return
                if path == "/open-rmf/rmf_demos_fm/toggle_action":
                    self._send(200, {"success": True, "msg": ""})
                    return
                self._send(404, {"success": False, "msg": "not found"})

        return FmHandler


def main() -> None:
    ap = argparse.ArgumentParser(description="OS 车队主 HTTP 控制层（CLI 自测；#18 §10.4）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=22011, help="= fleet_config fleet_manager.port")
    ap.add_argument("--edge-url", default=DEFAULT_EDGE_URL, help="edge AGV HTTP 服务地址（§10.3）")
    ap.add_argument("--robot", action="append", default=[], help="机器人名，可多次；默认 unilab_agv1")
    ap.add_argument("--nominal-velocity", type=float, default=DEFAULT_NOMINAL_V)
    args = ap.parse_args()

    mgr = EdgeFleetManager(
        edge_url=args.edge_url,
        robot_names=args.robot or ["unilab_agv1"],
        nominal_velocity=args.nominal_velocity,
    )
    print(f"[os-fleet] CLI 启动：RMF↔edge 桥 http://{args.host}:{args.port}  edge={args.edge_url}", flush=True)
    mgr.serve_forever(args.host, args.port)


if __name__ == "__main__":
    main()
