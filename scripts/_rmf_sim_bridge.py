#!/usr/bin/env python3
"""edge-free sim bridge（供 rmf_estimate_times.py --execute 用）。

一个进程内拉起：
- SimClock(mode="sim", scale=N)：sim 时间，按 N 倍快进（sim 时间=真实建模时间，wall-clock 被压缩 N×）；
- SimClockPublisher：发布 /clock（让 RMF headless `use_sim_time:=true` 跟随同一个时钟）；
- SimClockControlNode：`/unilab/sim/{set_rate,pause,resume,status}` 服务（可选，运行中调速/暂停）；
- MockAgvServer(clock=SimClock)：mock 小车按 **sim 时间** 步进（与 RMF 同钟，快进时一起加速）。

即 24.9 §7.2「标准（真·脱离 edge）」rig：RMF + mock 同跟 OS 的 /clock，set_rate 一处调速、全栈一起快进。
不起 OS edge、不起 Gazebo。

支持多车（交通级 makespan 需要）：多次 `--robot name:x:y`，所有车共用同一 SimClock（一起快进），
RMF traffic scheduler 在多车实跑时自动协商避让（等待/绕行），实际 finish 时刻即含避让。

用法（一般由 rmf_estimate_times.py --execute 自动拉起）：
    单车: python scripts/_rmf_sim_bridge.py --robot unilab_agv1 --x 49.4 --y -25.0 --speed 1.5 --scale 10
    多车: python scripts/_rmf_sim_bridge.py --robot unilab_agv1:49.4:-25.0 --robot unilab_agv2:53.1:-25.0 --scale 10
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # .../Uni-Lab-OS
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _scrub_system_ros_from_pythonpath() -> None:
    """conda unilab(python3.11) 不能加载 /opt/ros/humble 的 py3.10 rclpy 扩展。

    若父进程曾 source ROS setup，PYTHONPATH/sys.path 会把系统 rclpy 插到前面导致
    ModuleNotFoundError: rclpy._rclpy_pybind11。启动前清掉 /opt/ros 路径，改用 conda 自带 rclpy。
    """
    import os

    def _keep(p: str) -> bool:
        return "/opt/ros/" not in p.replace("\\", "/")

    raw = os.environ.get("PYTHONPATH", "")
    if raw:
        parts = [p for p in raw.split(":") if p and _keep(p)]
        if parts:
            os.environ["PYTHONPATH"] = ":".join(parts)
        else:
            os.environ.pop("PYTHONPATH", None)
    sys.path[:] = [p for p in sys.path if _keep(str(p))]


def main() -> None:
    _scrub_system_ros_from_pythonpath()
    ap = argparse.ArgumentParser(description="edge-free sim bridge：SimClock + /clock + mock(按 sim 时间步进)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--robot", action="append", default=[], help="name[:x:y[:yaw]]，可多次（多车交通级）")
    ap.add_argument("--x", type=float, default=0.0, help="单车且 --robot 无坐标时的起始 x")
    ap.add_argument("--y", type=float, default=0.0)
    ap.add_argument("--yaw", type=float, default=0.0)
    ap.add_argument("--speed", type=float, default=1.5, help="巡航线速度 m/s（= nominal_v）")
    ap.add_argument("--accel", type=float, default=0.75, help="线加/减速度 m/s²")
    ap.add_argument("--ang-speed", type=float, default=0.6, help="角速度上限 rad/s")
    ap.add_argument("--ang-accel", type=float, default=2.0, help="角加/减速度 rad/s²")
    ap.add_argument("--scale", type=float, default=1.0, help="sim 时钟倍率（快进，>1 压缩墙钟）")
    ap.add_argument("--min-separation", type=float, default=0.9, help="机器人最小间距（米，安全兜底）")
    ap.add_argument("--no-adaptive", action="store_true", help="关闭自适应快进（默认开启）")
    ap.add_argument("--slow-scale", type=float, default=0.5, help="冲突区降速倍率（>0，且不高于 --scale）")
    ap.add_argument("--danger-enter", type=float, default=6.5, help="最近两车中心距 <= 此阈值时降速（米）")
    ap.add_argument("--danger-exit", type=float, default=8.0, help="最近两车中心距 >= 此阈值时恢复（米）")
    ap.add_argument("--monitor-hz", type=float, default=20.0, help="自适应监控频率（Hz）")
    ap.add_argument("--clock-rate-hz", type=int, default=100)
    args = ap.parse_args()

    import rclpy
    from rclpy.executors import MultiThreadedExecutor

    from unilabos.sim.clock import SimClock
    from unilabos.sim.clock_control import SimClockControlNode
    from unilabos.sim.clock_publisher import SimClockPublisher
    from unilabos.sim.fleet.rmf.edge.agv_http_server import MockAgvServer, _parse_robot
    from unilabos.sim.fleet.rmf.edge.mock_agv import MockAgvHardware

    if not rclpy.ok():
        rclpy.init()

    clock = SimClock(mode="sim", scale=float(args.scale))
    pub = SimClockPublisher(clock, rate_hz=int(args.clock_rate_hz), auto_start=True)   # → /clock
    ctl = SimClockControlNode(clock, auto_start=True)                                  # → /unilab/sim/*

    # 多车：每个 --robot 一台，全部共用同一 SimClock（一起快进）；单车且无坐标时回退 --x/--y
    specs = args.robot or ["unilab_agv1"]
    hws = []
    for spec in specs:
        hw = _parse_robot(spec) if ":" in spec else MockAgvHardware(spec, x=args.x, y=args.y, yaw=args.yaw)
        hw.linear_speed = float(args.speed)
        hw.linear_accel = float(args.accel)
        hw.max_angular_speed = float(args.ang_speed)
        hw.angular_accel = float(args.ang_accel)
        hws.append(hw)
    server = MockAgvServer(hws, clock=clock, min_separation_m=float(args.min_separation))
    server.start("127.0.0.1", int(args.port))

    adaptive_enabled = (not bool(args.no_adaptive)) and len(hws) >= 2 and float(args.scale) > 1.0
    monitor_stop = threading.Event()
    monitor_thread: threading.Thread | None = None

    if adaptive_enabled:
        base_scale = max(1.0, float(args.scale))
        slow_scale = max(0.1, min(base_scale, float(args.slow_scale)))
        enter = max(0.05, float(args.danger_enter))
        exit_ = max(enter + 0.05, float(args.danger_exit))
        hz = max(1.0, float(args.monitor_hz))
        period = 1.0 / hz

        def _nearest_pair() -> tuple[float, tuple[str, str]]:
            best = float("inf")
            pair = ("", "")
            states = [hw.state() for hw in hws]
            for i in range(len(states)):
                for j in range(i + 1, len(states)):
                    s1 = states[i]
                    s2 = states[j]
                    d = math.hypot(
                        float(s1.get("x", 0.0)) - float(s2.get("x", 0.0)),
                        float(s1.get("y", 0.0)) - float(s2.get("y", 0.0)),
                    )
                    if d < best:
                        best = d
                        pair = (str(s1.get("robot", "")), str(s2.get("robot", "")))
            return best, pair

        def _adaptive_loop() -> None:
            in_danger = False
            while not monitor_stop.is_set():
                min_d, pair = _nearest_pair()
                if min_d <= enter:
                    in_danger = True
                elif min_d >= exit_:
                    in_danger = False
                target = slow_scale if in_danger else base_scale
                if abs(float(clock.scale) - target) > 1e-6:
                    clock.set_scale(target)
                    print(
                        f"[adaptive-ff] min_dist={min_d:.3f}m pair={pair[0]}/{pair[1]} "
                        f"-> scale x{target:.2f}",
                        flush=True,
                    )
                monitor_stop.wait(period)

        monitor_thread = threading.Thread(target=_adaptive_loop, name="adaptive-ff", daemon=True)
        monitor_thread.start()
        print(
            f"[adaptive-ff] enabled base=x{base_scale:.2f} slow=x{slow_scale:.2f} "
            f"enter<={enter:.2f}m exit>={exit_:.2f}m hz={hz:.1f}",
            flush=True,
        )
    else:
        print("[adaptive-ff] disabled", flush=True)

    print(
        f"[sim-bridge] /clock 发布中（mode=sim, scale={args.scale}x, {args.clock_rate_hz}Hz）；"
        f"mock {[h.robot for h in hws]}@:{args.port} 按 sim 时间步进。"
        f" RMF 请以 use_sim_time:=true headless 跟随本 /clock。",
        flush=True,
    )

    ex = MultiThreadedExecutor()
    for node in (getattr(pub, "node", None), getattr(ctl, "node", None)):
        if node is not None:
            ex.add_node(node)
    try:
        ex.spin()
    except KeyboardInterrupt:
        pass
    finally:
        monitor_stop.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=1.0)
        try:
            server.stop()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)


if __name__ == "__main__":
    main()
