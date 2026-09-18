"""edge AGV mock 硬件（#18 §10.3）。

收到 RMF 经 HTTP 下发的 `navigate`（destination + path）后，沿 path 逐点行驶更新 `(x, y, yaw)`，
到点置 `idle`；`state()` 供上报。**收到的每条指令落 `cmd_log`**（即"edge 收到指令"的证据）。

运动学（与 RMF diff-drive 估时同口径）：**停车转弯 + 梯形速度剖面**——
- 线性：以 `linear_accel` 加速到巡航 `linear_speed`、末端按同加速度减速到 0（停在目标）；
- 角度：转向目标航向时以 `angular_accel` 加/减速、峰值 `max_angular_speed`，转到位再前进（turn-in-place）；
- 未对准航向时线速度减到 0（先停后转），故短段/多拐弯会体现真实加减速与转弯耗时。
真实硬件把本类替换为对接 SEER `agv_navigator.py` 即可（接口不变）。
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, List, Optional

_ALIGN_THRESH = 0.12  # rad：航向误差 < 此值才允许前进（否则先停下转向）


def _wrap_angle(a: float) -> float:
    """归一化到 [-pi, pi]。"""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _ramp(cur: float, target: float, max_delta: float) -> float:
    """把 cur 朝 target 逼近，单步最多变化 max_delta（限加/减速度）。"""
    if cur < target:
        return min(target, cur + max_delta)
    return max(target, cur - max_delta)


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
        linear_accel: float = 0.75,
        max_angular_speed: float = 0.6,
        angular_accel: float = 2.0,
    ) -> None:
        self.robot = robot
        self.x = float(x)
        self.y = float(y)
        self.yaw = float(yaw)
        self.level = level
        self.linear_speed = float(linear_speed)      # 巡航线速度上限 m/s
        self.linear_accel = float(linear_accel)      # 线加/减速度 m/s²
        self.max_angular_speed = float(max_angular_speed)  # 角速度上限 rad/s
        self.angular_accel = float(angular_accel)    # 角加/减速度 rad/s²
        self.battery = float(battery)
        self.status = "idle"
        self.mode = "idle"
        self.last_cmd_id = 0
        self.v_lin = 0.0  # 当前线速度（用于加减速积分）
        self.v_ang = 0.0  # 当前角速度
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
            self.v_lin = 0.0
            self.v_ang = 0.0
            self.status = "idle"
            self.mode = "idle"
            self.last_cmd_id = int(cmd_id)
        return {"success": True, "robot": self.robot}

    def step(self, dt: float) -> None:
        """推进运动 dt 秒（后台线程周期调用）：停车转弯 + 梯形线性/角速度剖面。"""
        with self._lock:
            if dt <= 0:
                return
            if self.status != "moving" or self._idx >= len(self._path):
                # 无目标：把残余速度按加速度减到 0（避免瞬停）
                self.v_lin = _ramp(self.v_lin, 0.0, self.linear_accel * dt)
                self.v_ang = _ramp(self.v_ang, 0.0, self.angular_accel * dt)
                if self._path and self._idx >= len(self._path):
                    self.status = "idle"
                    self.mode = "idle"
                return

            tx, ty, _tyaw = self._path[self._idx]
            dx, dy = tx - self.x, ty - self.y
            dist = math.hypot(dx, dy)
            if dist < 1e-3:  # 到点
                self.x, self.y = tx, ty
                self._idx += 1
                if self._idx >= len(self._path):
                    self.v_lin = 0.0
                    self.status = "idle"
                    self.mode = "idle"
                return

            # --- 角度：梯形角速度剖面，转到"朝向目标"且停住 ---
            desired = math.atan2(dy, dx)
            yaw_err = _wrap_angle(desired - self.yaw)
            # 允许的峰值角速度：既 ≤ max，又要能在剩余 |yaw_err| 内减到 0（w ≤ √(2·α·|err|)）
            w_cap = math.sqrt(2.0 * self.angular_accel * abs(yaw_err)) if self.angular_accel > 0 else self.max_angular_speed
            w_des = math.copysign(min(self.max_angular_speed, w_cap), yaw_err)
            self.v_ang = _ramp(self.v_ang, w_des, self.angular_accel * dt)
            dyaw = self.v_ang * dt
            if abs(dyaw) >= abs(yaw_err):
                self.yaw = desired
                self.v_ang = 0.0
            else:
                self.yaw = _wrap_angle(self.yaw + dyaw)

            # --- 线性：仅在大致对准航向时前进；梯形剖面，末端减速停在目标 ---
            aligned = abs(_wrap_angle(desired - self.yaw)) < _ALIGN_THRESH
            if aligned:
                v_cap = math.sqrt(2.0 * self.linear_accel * dist) if self.linear_accel > 0 else self.linear_speed
                v_des = min(self.linear_speed, v_cap)
                self.v_lin = _ramp(self.v_lin, v_des, self.linear_accel * dt)
            else:
                self.v_lin = _ramp(self.v_lin, 0.0, self.linear_accel * dt)  # 转弯时先减速

            step_d = self.v_lin * dt
            if step_d >= dist:  # 本步跨过目标 → 落到目标
                self.x, self.y = tx, ty
                self._idx += 1
                if self._idx >= len(self._path):
                    self.v_lin = 0.0
                    self.status = "idle"
                    self.mode = "idle"
            elif step_d > 0.0:  # 沿目标方向前进（差速轮对准后≈沿航向）
                self.x += dx / dist * step_d
                self.y += dy / dist * step_d
            self.battery = max(0.0, self.battery - 1e-5 * dt)

    def motion_snapshot(self) -> Dict[str, Any]:
        """抓取一步运动快照（用于 server 级最小间距回滚）。"""
        with self._lock:
            return {
                "x": float(self.x),
                "y": float(self.y),
                "yaw": float(self.yaw),
                "v_lin": float(self.v_lin),
                "v_ang": float(self.v_ang),
                "idx": int(self._idx),
                "status": str(self.status),
                "mode": str(self.mode),
                "battery": float(self.battery),
            }

    def restore_motion_snapshot(self, snap: Dict[str, Any], *, zero_velocity: bool = False) -> None:
        """回滚到某一步前状态。"""
        with self._lock:
            self.x = float(snap.get("x", self.x))
            self.y = float(snap.get("y", self.y))
            self.yaw = float(snap.get("yaw", self.yaw))
            self._idx = int(snap.get("idx", self._idx))
            self.status = str(snap.get("status", self.status))
            self.mode = str(snap.get("mode", self.mode))
            self.battery = float(snap.get("battery", self.battery))
            if zero_velocity:
                self.v_lin = 0.0
                self.v_ang = 0.0
            else:
                self.v_lin = float(snap.get("v_lin", self.v_lin))
                self.v_ang = float(snap.get("v_ang", self.v_ang))

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
