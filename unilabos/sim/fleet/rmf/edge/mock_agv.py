"""edge AGV mock 硬件（#18 §10.3）。

收到 RMF 经 HTTP 下发的 `navigate`（destination + path）后，按 `linear_speed` 沿 path 线性插值
更新 `(x, y, yaw)`，到点置 `idle`；`state()` 供上报。**收到的每条指令落 `cmd_log`**（即"edge 收到指令"的证据）。
真实硬件把本类替换为对接 SEER `agv_navigator.py` 即可（接口不变）。
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, List, Optional


class MockAgvHardware:
    def __init__(
        self,
        robot: str,
        x: float = 0.0,
        y: float = 0.0,
        yaw: float = 0.0,
        level: str = "L1",
        linear_speed: float = 0.5,
        battery: float = 1.0,
    ) -> None:
        self.robot = robot
        self.x = float(x)
        self.y = float(y)
        self.yaw = float(yaw)
        self.level = level
        self.linear_speed = float(linear_speed)
        self.battery = float(battery)
        self.status = "idle"
        self.mode = "idle"
        self.last_cmd_id = 0
        self._path: List[List[float]] = []  # [[x, y, yaw], ...]
        self._idx = 0
        self._lock = threading.Lock()
        self._cmd_log: List[Dict[str, Any]] = []

    def navigate(
        self,
        cmd_id: int,
        destination: Optional[Dict[str, Any]],
        path: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """RMF→edge：接收导航指令并开始执行（mock）。"""
        with self._lock:
            pts: List[List[float]] = []
            for p in path or []:
                pts.append([float(p.get("x")), float(p.get("y")), float(p.get("yaw", 0.0))])
            if not pts and destination:
                pts = [
                    [float(destination["x"]), float(destination["y"]), float(destination.get("yaw", 0.0))]
                ]
            self._path = pts
            self._idx = 0
            self.last_cmd_id = int(cmd_id)
            self.status = "moving" if pts else "idle"
            self.mode = self.status
            self._cmd_log.append(
                {"t": round(time.time(), 3), "cmdId": int(cmd_id), "destination": destination, "pathLen": len(pts)}
            )
            if len(self._cmd_log) > 200:
                self._cmd_log = self._cmd_log[-200:]
        return {"success": True, "robot": self.robot, "cmdId": int(cmd_id), "pathLen": len(pts)}

    def stop(self, cmd_id: int = 0) -> Dict[str, Any]:
        with self._lock:
            self._path = []
            self._idx = 0
            self.status = "idle"
            self.mode = "idle"
            self.last_cmd_id = int(cmd_id)
        return {"success": True, "robot": self.robot}

    def step(self, dt: float) -> None:
        """推进运动 dt 秒（后台线程周期调用）。"""
        with self._lock:
            if self.status != "moving" or self._idx >= len(self._path):
                if self._path and self._idx >= len(self._path):
                    self.status = "idle"
                    self.mode = "idle"
                return
            tx, ty, tyaw = self._path[self._idx]
            dx, dy = tx - self.x, ty - self.y
            dist = math.hypot(dx, dy)
            stepd = self.linear_speed * dt
            if dist <= stepd or dist < 1e-6:
                self.x, self.y, self.yaw = tx, ty, tyaw
                self._idx += 1
                if self._idx >= len(self._path):
                    self.status = "idle"
                    self.mode = "idle"
            else:
                self.x += dx / dist * stepd
                self.y += dy / dist * stepd
                self.yaw = math.atan2(dy, dx)
            self.battery = max(0.0, self.battery - 1e-5 * dt)

    def state(self) -> Dict[str, Any]:
        """edge→RMF：上报当前位姿/状态。"""
        with self._lock:
            return {
                "robot": self.robot,
                "x": round(self.x, 3),
                "y": round(self.y, 3),
                "yaw": round(self.yaw, 4),
                "level": self.level,
                "status": self.status,
                "battery": round(self.battery, 4),
                "mode": self.mode,
                "lastCmdId": self.last_cmd_id,
                "remainingPath": max(0, len(self._path) - self._idx),
            }

    def cmd_log(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._cmd_log)
