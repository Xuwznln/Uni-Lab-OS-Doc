"""RMF runtime standalone CLI。"""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from unilabos.sim.fleet.rmf.runtime.launcher import RmfRuntimeLauncher, RmfRuntimeOptions


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-root", default="", help="runtime 根目录（默认按 --map-dir 推导）")
    parser.add_argument("--map-dir", default="", help="地图目录（默认 <runtime-root>/maps/latest）")
    parser.add_argument("--layout-dir", default="", help="layout-optimizer 目录（prepare 时可覆盖 map.env）")
    parser.add_argument("--lab-uuid", default="demo_lab")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-token", default="")


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RMF runtime launcher CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="仅准备地图产物")
    _common_args(p_prepare)
    p_prepare.add_argument("--fleet-config", default="", help="可选 fleet_config 覆盖路径")

    p_start = sub.add_parser("start", help="启动 runtime 栈")
    _common_args(p_start)
    p_start.add_argument("--mode", choices=["headless", "sim"], default="headless")
    p_start.add_argument("--use-sim-time", action="store_true")
    p_start.add_argument("--prepare-map", action="store_true")
    p_start.add_argument("--stop-before-start", action="store_true")
    p_start.add_argument("--no-start-api", action="store_true")
    p_start.add_argument("--no-start-core", action="store_true")
    p_start.add_argument("--bridge-mode", choices=["none", "mock", "sim_bridge"], default="none")
    p_start.add_argument("--robot", action="append", default=[], help="name[:x:y[:yaw]]，可多次")
    p_start.add_argument("--robots", default="", help="逗号分隔机器人名（仅在未给 --robot 时使用）")
    p_start.add_argument("--edge-port", type=int, default=8090)
    p_start.add_argument("--fleet-manager-port", type=int, default=22011)
    p_start.add_argument("--fleet-poll-hz", type=float, default=10.0)
    p_start.add_argument("--min-separation", type=float, default=0.9)
    p_start.add_argument("--nominal-velocity", type=float, default=1.5)
    p_start.add_argument("--linear-speed", type=float, default=1.5)
    p_start.add_argument("--linear-accel", type=float, default=0.75)
    p_start.add_argument("--angular-speed", type=float, default=0.6)
    p_start.add_argument("--angular-accel", type=float, default=2.0)
    p_start.add_argument("--sim-scale", type=float, default=10.0)
    p_start.add_argument("--min-registered-robots", type=int, default=0)
    p_start.add_argument("--fleet-config", default="", help="可选 fleet_config 覆盖路径")
    p_start.add_argument("--logs-dir", default="", help="日志目录（默认 runtime_root）")
    p_start.add_argument("--wait-api-timeout", type=float, default=150.0)
    p_start.add_argument("--wait-fleet-timeout", type=float, default=180.0)

    p_stop = sub.add_parser("stop", help="停止 runtime 栈")
    _common_args(p_stop)
    p_stop.add_argument("--with-shell-stop", action="store_true", help="额外执行 stop_all_rmf.sh")

    p_status = sub.add_parser("status", help="查询 runtime 状态")
    _common_args(p_status)

    return parser


def _build_options(args: argparse.Namespace) -> RmfRuntimeOptions:
    robot_names = [r.strip() for r in str(getattr(args, "robots", "") or "").split(",") if r.strip()]
    return RmfRuntimeOptions(
        runtime_root=str(getattr(args, "runtime_root", "") or ""),
        map_dir=str(getattr(args, "map_dir", "") or ""),
        layout_dir=str(getattr(args, "layout_dir", "") or ""),
        lab_uuid=str(getattr(args, "lab_uuid", "demo_lab") or "demo_lab"),
        mode=str(getattr(args, "mode", "headless") or "headless"),
        use_sim_time=bool(getattr(args, "use_sim_time", False)),
        prepare_map=bool(getattr(args, "prepare_map", False)),
        start_api_server=not bool(getattr(args, "no_start_api", False)),
        start_rmf_core=not bool(getattr(args, "no_start_core", False)),
        stop_before_start=bool(getattr(args, "stop_before_start", False)),
        api_url=str(getattr(args, "api_url", "http://127.0.0.1:8000") or "http://127.0.0.1:8000"),
        api_token=str(getattr(args, "api_token", "") or ""),
        bridge_mode=str(getattr(args, "bridge_mode", "none") or "none"),
        robots=robot_names or ["unilab_agv1"],
        robot_specs=list(getattr(args, "robot", []) or []),
        edge_port=int(getattr(args, "edge_port", 8090) or 8090),
        fleet_manager_port=int(getattr(args, "fleet_manager_port", 22011) or 22011),
        fleet_poll_hz=float(getattr(args, "fleet_poll_hz", 10.0) or 10.0),
        min_separation_m=float(getattr(args, "min_separation", 0.9) or 0.0),
        nominal_velocity=float(getattr(args, "nominal_velocity", 1.5) or 1.5),
        linear_speed=float(getattr(args, "linear_speed", 1.5) or 1.5),
        linear_accel=float(getattr(args, "linear_accel", 0.75) or 0.75),
        angular_speed=float(getattr(args, "angular_speed", 0.6) or 0.6),
        angular_accel=float(getattr(args, "angular_accel", 2.0) or 2.0),
        sim_scale=float(getattr(args, "sim_scale", 10.0) or 10.0),
        min_registered_robots=int(getattr(args, "min_registered_robots", 0) or 0),
        fleet_config_override=str(getattr(args, "fleet_config", "") or ""),
        logs_dir=str(getattr(args, "logs_dir", "") or ""),
        wait_api_timeout_s=float(getattr(args, "wait_api_timeout", 150.0) or 150.0),
        wait_fleet_timeout_s=float(getattr(args, "wait_fleet_timeout", 180.0) or 180.0),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argparser().parse_args(argv)
    launcher = RmfRuntimeLauncher()
    opt = _build_options(args)

    if args.command == "prepare":
        result = launcher.prepare_runtime_map(opt)
    elif args.command == "start":
        result = launcher.start_runtime_stack(opt)
    elif args.command == "stop":
        result = launcher.stop_runtime_stack(opt, include_shell_stop=bool(args.with_shell_stop))
    elif args.command == "status":
        result = launcher.runtime_status(opt)
    else:  # pragma: no cover
        result = {"success": False, "error": f"unknown command: {args.command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if bool(result.get("success", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())

