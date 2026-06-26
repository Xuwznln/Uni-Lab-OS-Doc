#!/usr/bin/env python3
"""edge AGV mock HTTP 服务（#18 §10.3）——RMF 经此 HTTP 下发指令，edge 必收到；**零 ROS / 零 rmf_msgs**。

接口：
  POST /agv/navigate   body {robot, cmdId, destination:{x,y,yaw,level}, path?:[{x,y,yaw}], taskId?}
  GET  /agv/state?robot=NAME   → {robot,x,y,yaw,level,status,battery,mode,lastCmdId,remainingPath}
  POST /agv/stop       body {robot, cmdId}
  GET  /agv/cmdlog?robot=NAME  → 已收指令日志（证明 edge 收到）

用法：
  python -m unilabos.sim.fleet.rmf.edge.agv_http_server --port 8090 \\
      --robot unilab_agv1:42.46:-27.10
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[5]  # .../Uni-Lab-OS
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unilabos.sim.fleet.rmf.edge.mock_agv import MockAgvHardware

ROBOTS: Dict[str, MockAgvHardware] = {}
_STEP_DT = 0.1


class AgvHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # 静音默认访问日志
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

    def _robot(self, body: dict, q: dict):
        name = body.get("robot") or (q.get("robot", [None])[0])
        return name, ROBOTS.get(str(name)) if name else None

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/agv/state":
            name = q.get("robot", [None])[0]
            if name:
                hw = ROBOTS.get(name)
                self._send(200 if hw else 404, hw.state() if hw else {"error": f"unknown robot {name}"})
            else:
                self._send(200, {"robots": [hw.state() for hw in ROBOTS.values()]})
            return
        if u.path == "/agv/cmdlog":
            name = q.get("robot", [None])[0]
            hw = ROBOTS.get(name) if name else None
            self._send(200 if hw else 404, {"robot": name, "cmdLog": hw.cmd_log()} if hw else {"error": "unknown robot"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        body = self._body()
        name, hw = self._robot(body, q)
        if u.path == "/agv/navigate":
            if hw is None:
                self._send(404, {"error": f"unknown robot {name}"})
                return
            cmd_id = body.get("cmdId", body.get("cmd_id", 0))
            res = hw.navigate(cmd_id, body.get("destination"), body.get("path"))
            dest = body.get("destination") or {}
            print(
                f"[edge:{name}] 收到 navigate cmd={res['cmdId']} "
                f"dest=({dest.get('x')},{dest.get('y')}) waypoint={dest.get('waypoint')} "
                f"task={body.get('taskId')} pathLen={res['pathLen']}",
                flush=True,
            )
            self._send(200, res)
            return
        if u.path == "/agv/stop":
            if hw is None:
                self._send(404, {"error": f"unknown robot {name}"})
                return
            print(f"[edge:{name}] 收到 stop", flush=True)
            self._send(200, hw.stop(body.get("cmdId", 0)))
            return
        self._send(404, {"error": "not found"})


def _stepper() -> None:
    while True:
        for hw in list(ROBOTS.values()):
            hw.step(_STEP_DT)
        time.sleep(_STEP_DT)


def _parse_robot(spec: str) -> MockAgvHardware:
    # 格式 name[:x:y[:yaw]]
    parts = spec.split(":")
    name = parts[0]
    x = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
    y = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    yaw = float(parts[3]) if len(parts) > 3 and parts[3] else 0.0
    return MockAgvHardware(name, x=x, y=y, yaw=yaw)


def main() -> None:
    ap = argparse.ArgumentParser(description="edge AGV mock HTTP 服务（#18 §10.3）")
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

    specs = args.robot or ["unilab_agv1"]
    for spec in specs:
        hw = _parse_robot(spec)
        hw.linear_speed = args.speed
        ROBOTS[hw.robot] = hw

    threading.Thread(target=_stepper, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), AgvHandler)
    print(f"[edge] AGV mock HTTP 服务 http://{args.host}:{args.port}  robots={list(ROBOTS)}", flush=True)
    print("[edge] POST /agv/navigate | GET /agv/state?robot= | POST /agv/stop | GET /agv/cmdlog?robot=", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
