#!/usr/bin/env python3
"""edge AGV mock HTTP 服务（#18 §10.3）——RMF（经车队主）下发指令，edge 必收到；**零 ROS / 零 rmf_msgs**。

接口：
  POST /agv/navigate   body {robot, cmdId, destination:{x,y,yaw,level}, path?:[{x,y,yaw}], taskId?}
  GET  /agv/state?robot=NAME   → {robot,x,y,yaw,level,status,battery,mode,lastCmdId,remainingPath}
  POST /agv/stop       body {robot, cmdId}
  GET  /agv/cmdlog?robot=NAME  → 已收指令日志（证明 edge 收到）

两种用法：
1. **OS 设备进程内（推荐，#22 §0.1）**：`agv.RMFSim` 设备在 `__init__` 里 `MockAgvServer([...]).start()`
   → mock 小车成为 OS graph 的**独立 root 节点**，暴露 `/agv/*`。
2. **独立 CLI（自测 / 接外部）**：
   `python -m unilabos.sim.fleet.rmf.edge.agv_http_server --port 8090 --robot unilab_agv1:55.66:-24.70`
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[5]  # .../Uni-Lab-OS
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unilabos.sim.fleet.rmf.edge.mock_agv import MockAgvHardware

_STEP_DT = 0.1

if TYPE_CHECKING:
    from unilabos.sim.clock import SimClock


class MockAgvServer:
    """一台/多台 mock 小车的 `/agv/*` HTTP 服务 + 运动 stepper。

    进程内可 `start(host, port)`（OS 设备用，非阻塞）；CLI 用 `serve_forever(host, port)`（阻塞）。
    `log(msg)`：注入日志（OS 设备传 unilabos logger；CLI 默认 print）。
    """

    def __init__(
        self,
        robots: List[MockAgvHardware],
        *,
        step_dt: float = _STEP_DT,
        log: Optional[Callable[[str], None]] = None,
        clock: Optional["SimClock"] = None,
    ) -> None:
        self.robots: Dict[str, MockAgvHardware] = {hw.robot: hw for hw in robots}
        self._step_dt = step_dt
        self._log = log
        # 仅在 OS 进程内由 RMFSim 注入；CLI 独立运行时保持墙钟推进。
        self._clock = clock
        self._stop = threading.Event()
        self._server: Optional[ThreadingHTTPServer] = None
        self._srv_thread: Optional[threading.Thread] = None
        self._step_thread: Optional[threading.Thread] = None

    def log(self, msg: str) -> None:
        (self._log or (lambda m: print(m, flush=True)))(msg)

    def _stepper(self) -> None:
        if self._clock is None:
            while not self._stop.is_set():
                for hw in list(self.robots.values()):
                    hw.step(self._step_dt)
                self._stop.wait(self._step_dt)
            return

        # 绑定 SimClock：pause 时 now 不前进；set_rate 会改变 now 增速。
        try:
            last_sim_now = float(self._clock.now())
        except Exception:  # noqa: BLE001
            last_sim_now = time.time()
        max_slice = max(self._step_dt, 0.02)
        while not self._stop.is_set():
            try:
                sim_now = float(self._clock.now())
            except Exception:  # noqa: BLE001
                sim_now = last_sim_now
            dt = sim_now - last_sim_now
            last_sim_now = sim_now
            # 防止线程偶发卡顿导致一次性跨过过大位移，按切片推进更稳定。
            while dt > 1e-9:
                step_dt = min(dt, max_slice)
                for hw in list(self.robots.values()):
                    hw.step(step_dt)
                dt -= step_dt
            self._stop.wait(self._step_dt)

    def start(self, host: str = "127.0.0.1", port: int = 8090) -> None:
        self._step_thread = threading.Thread(target=self._stepper, name="mock-agv-step", daemon=True)
        self._step_thread.start()
        self._server = ThreadingHTTPServer((host, port), self._make_handler())
        self._srv_thread = threading.Thread(target=self._server.serve_forever, name="mock-agv-http", daemon=True)
        self._srv_thread.start()
        self.log(f"[mock-agv] /agv/* http://{host}:{port}  robots={list(self.robots)}")

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:  # noqa: BLE001
                pass
        self._server = None

    def serve_forever(self, host: str = "127.0.0.1", port: int = 8090) -> None:
        self.start(host, port)
        self.log("[mock-agv] POST /agv/navigate | GET /agv/state?robot= | POST /agv/stop | GET /agv/cmdlog?robot=")
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop()

    def _make_handler(self):
        robots = self.robots
        log = self.log

        class AgvHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:
                return

            def _send(self, code: int, payload) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
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
                if u.path == "/agv/state":
                    name = q.get("robot", [None])[0]
                    if name:
                        hw = robots.get(name)
                        self._send(200 if hw else 404, hw.state() if hw else {"error": f"unknown robot {name}"})
                    else:
                        self._send(200, {"robots": [hw.state() for hw in robots.values()]})
                    return
                if u.path == "/agv/cmdlog":
                    name = q.get("robot", [None])[0]
                    hw = robots.get(name) if name else None
                    self._send(
                        200 if hw else 404,
                        {"robot": name, "cmdLog": hw.cmd_log()} if hw else {"error": "unknown robot"},
                    )
                    return
                self._send(404, {"error": "not found"})

            def do_POST(self) -> None:
                u = urlparse(self.path)
                q = parse_qs(u.query)
                body = self._body()
                name = body.get("robot") or (q.get("robot", [None])[0])
                hw = robots.get(str(name)) if name else None
                if u.path == "/agv/navigate":
                    if hw is None:
                        self._send(404, {"error": f"unknown robot {name}"})
                        return
                    cmd_id = body.get("cmdId", body.get("cmd_id", 0))
                    res = hw.navigate(cmd_id, body.get("destination"), body.get("path"))
                    dest = body.get("destination") or {}
                    log(
                        f"[mock-agv:{name}] 收到 navigate cmd={res['cmdId']} "
                        f"dest=({dest.get('x')},{dest.get('y')}) waypoint={dest.get('waypoint')} "
                        f"task={body.get('taskId')} pathLen={res['pathLen']}"
                    )
                    self._send(200, res)
                    return
                if u.path == "/agv/stop":
                    if hw is None:
                        self._send(404, {"error": f"unknown robot {name}"})
                        return
                    log(f"[mock-agv:{name}] 收到 stop")
                    self._send(200, hw.stop(body.get("cmdId", 0)))
                    return
                self._send(404, {"error": "not found"})

        return AgvHandler


def _parse_robot(spec: str) -> MockAgvHardware:
    # 格式 name[:x:y[:yaw]]
    parts = spec.split(":")
    name = parts[0]
    x = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
    y = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    yaw = float(parts[3]) if len(parts) > 3 and parts[3] else 0.0
    return MockAgvHardware(name, x=x, y=y, yaw=yaw)


def main() -> None:
    ap = argparse.ArgumentParser(description="edge AGV mock HTTP 服务（CLI；#18 §10.3）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument(
        "--robot",
        action="append",
        default=[],
        help="机器人初始位姿 name[:x:y[:yaw]]，可多次；默认 unilab_agv1",
    )
    ap.add_argument("--speed", type=float, default=0.5, help="线速度 m/s")
    args = ap.parse_args()

    robots: List[MockAgvHardware] = []
    for spec in args.robot or ["unilab_agv1"]:
        hw = _parse_robot(spec)
        hw.linear_speed = args.speed
        robots.append(hw)
    MockAgvServer(robots).serve_forever(args.host, args.port)


if __name__ == "__main__":
    main()
