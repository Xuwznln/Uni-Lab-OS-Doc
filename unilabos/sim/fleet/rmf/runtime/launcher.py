"""RMF runtime 统一起停入口（OS-first + standalone 复用）。"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("rmf.runtime.launcher")


try:  # pragma: no cover - requests 在目标环境默认可用
    import requests
except Exception:  # noqa: BLE001
    requests = None  # type: ignore[assignment]


@dataclass
class RmfRuntimeOptions:
    runtime_root: str = ""
    map_dir: str = ""
    layout_dir: str = ""
    lab_uuid: str = "demo_lab"
    mode: str = "headless"  # "headless" | "sim"
    use_sim_time: bool = False

    prepare_map: bool = False
    start_api_server: bool = True
    start_rmf_core: bool = True
    stop_before_start: bool = False
    api_url: str = "http://127.0.0.1:8000"
    api_token: str = ""

    # bridge_mode:
    # - "none": 仅起 api + rmf core（OS-first）
    # - "mock": 起 mock agv_http_server + standalone fleet_manager + rmf core
    # - "sim_bridge": 起 _rmf_sim_bridge + standalone fleet_manager + rmf core
    bridge_mode: str = "none"
    robots: List[str] = field(default_factory=lambda: ["unilab_agv1"])
    robot_specs: List[str] = field(default_factory=list)  # name[:x:y[:yaw]]
    edge_url: str = "http://127.0.0.1:8090"
    edge_port: int = 8090
    fleet_manager_host: str = "127.0.0.1"
    fleet_manager_port: int = 22011
    fleet_poll_hz: float = 10.0
    min_separation_m: float = 0.9
    nominal_velocity: float = 1.5
    linear_speed: float = 1.5
    linear_accel: float = 0.75
    angular_speed: float = 0.6
    angular_accel: float = 2.0
    sim_scale: float = 10.0
    adaptive_enabled: bool = True
    adaptive_slow_scale: float = 0.5
    adaptive_danger_enter: float = 6.5
    adaptive_danger_exit: float = 8.0
    adaptive_monitor_hz: float = 20.0
    min_registered_robots: int = 0

    fleet_config_override: str = ""
    logs_dir: str = ""
    wait_api_timeout_s: float = 150.0
    wait_fleet_timeout_s: float = 180.0


@dataclass
class _SpawnedProc:
    name: str
    proc: subprocess.Popen
    runtime_root: str
    log_path: str


class RmfRuntimeLauncher:
    """统一管理 RMF standalone 运行栈。"""

    _STATE_FILE = "rmf_runtime_state.json"

    def __init__(self) -> None:
        self._spawned: List[_SpawnedProc] = []

    # ========================================================= public api
    def prepare_runtime_map(self, options: RmfRuntimeOptions) -> Dict[str, Any]:
        opt = self._normalize_options(options)
        runtime_root = Path(opt.runtime_root)
        ensure_sh = runtime_root / "ensure_latest_map.sh"
        if not ensure_sh.is_file():
            return {
                "success": False,
                "error": f"missing ensure_latest_map.sh: {ensure_sh}",
                "runtime_root": str(runtime_root),
            }

        env = dict(os.environ)
        if opt.layout_dir:
            env["LAYOUT_OPTIMIZER_DIR"] = str(Path(opt.layout_dir).expanduser().resolve())
        if opt.map_dir:
            env["MAP_OUTPUT_DIR"] = str(Path(opt.map_dir).expanduser().resolve())
        if opt.lab_uuid:
            env["LAB_UUID"] = opt.lab_uuid
        if opt.fleet_config_override:
            env["FLEET_CONFIG"] = str(Path(opt.fleet_config_override).expanduser().resolve())

        try:
            subprocess.run(["bash", str(ensure_sh)], cwd=str(runtime_root), env=env, check=True)
        except subprocess.CalledProcessError as e:
            return {
                "success": False,
                "error": f"ensure_latest_map failed: exit={e.returncode}",
                "runtime_root": str(runtime_root),
                "map_dir": str(Path(opt.map_dir)),
            }
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "error": f"ensure_latest_map failed: {e}",
                "runtime_root": str(runtime_root),
                "map_dir": str(Path(opt.map_dir)),
            }

        return {
            "success": True,
            "runtime_root": str(runtime_root),
            "map_dir": str(Path(opt.map_dir)),
        }

    def start_runtime_stack(self, options: RmfRuntimeOptions) -> Dict[str, Any]:
        opt = self._normalize_options(options)
        runtime_root = Path(opt.runtime_root)
        started_now: List[_SpawnedProc] = []
        robot_names: List[str] = []
        try:
            if opt.stop_before_start:
                self._cleanup_before_start(opt)

            if opt.prepare_map:
                prep = self.prepare_runtime_map(opt)
                if not prep.get("success", False):
                    return prep

            if opt.start_api_server and not self._api_reachable(opt.api_url, opt.api_token):
                run_rmfweb = runtime_root / "run_rmfweb.sh"
                if not run_rmfweb.is_file():
                    return {"success": False, "error": f"missing run_rmfweb.sh: {run_rmfweb}"}
                started_now.append(
                    self._spawn(
                        name="api-server",
                        cmd=["bash", str(run_rmfweb)],
                        runtime_root=runtime_root,
                        log_path=self._log_path(runtime_root, opt, "_runtime_api_server.log"),
                    )
                )

            if not self._wait_for_api(opt.api_url, opt.api_token, timeout_s=opt.wait_api_timeout_s):
                raise RuntimeError(f"api-server not reachable: {opt.api_url}")

            robot_names, robot_specs, start_docks = self._resolve_robot_context(opt)
            bridge_mode = str(opt.bridge_mode or "none").strip().lower()
            if bridge_mode not in {"none", "mock", "sim_bridge"}:
                raise ValueError(f"unsupported bridge_mode={bridge_mode}")

            if bridge_mode in {"mock", "sim_bridge"}:
                if bridge_mode == "sim_bridge":
                    sim_cmd: List[str] = [
                        sys.executable,
                        str(self._sim_bridge_script_path()),
                        "--port",
                        str(opt.edge_port),
                        *self._robot_specs_to_cli(robot_specs),
                        "--speed",
                        str(opt.linear_speed),
                        "--accel",
                        str(opt.linear_accel),
                        "--ang-speed",
                        str(opt.angular_speed),
                        "--ang-accel",
                        str(opt.angular_accel),
                        "--scale",
                        str(opt.sim_scale),
                        "--min-separation",
                        str(opt.min_separation_m),
                    ]
                    if not bool(opt.adaptive_enabled):
                        sim_cmd.append("--no-adaptive")
                    else:
                        sim_cmd.extend(
                            [
                                "--slow-scale",
                                str(opt.adaptive_slow_scale),
                                "--danger-enter",
                                str(opt.adaptive_danger_enter),
                                "--danger-exit",
                                str(opt.adaptive_danger_exit),
                                "--monitor-hz",
                                str(opt.adaptive_monitor_hz),
                            ]
                        )
                    started_now.append(
                        self._spawn(
                            name="sim-bridge",
                            cmd=sim_cmd,
                            runtime_root=runtime_root,
                            log_path=self._log_path(runtime_root, opt, "_runtime_sim_bridge.log"),
                        )
                    )
                else:
                    started_now.append(
                        self._spawn(
                            name="mock-agv",
                            cmd=[
                                sys.executable,
                                "-m",
                                "unilabos.sim.fleet.rmf.edge.agv_http_server",
                                "--port",
                                str(opt.edge_port),
                                *self._robot_specs_to_cli(robot_specs),
                                "--speed",
                                str(opt.linear_speed),
                                "--accel",
                                str(opt.linear_accel),
                                "--ang-speed",
                                str(opt.angular_speed),
                                "--ang-accel",
                                str(opt.angular_accel),
                                "--min-separation",
                                str(opt.min_separation_m),
                            ],
                            runtime_root=runtime_root,
                            log_path=self._log_path(runtime_root, opt, "_runtime_mock_agv.log"),
                        )
                    )

                started_now.append(
                    self._spawn(
                        name="fleet-manager",
                        cmd=[
                            sys.executable,
                            "-m",
                            "unilabos.sim.fleet.rmf.edge.fleet_manager_http",
                            "--port",
                            str(opt.fleet_manager_port),
                            "--edge-url",
                            f"http://127.0.0.1:{opt.edge_port}",
                            *self._robot_names_to_cli(robot_names),
                            "--nominal-velocity",
                            str(opt.nominal_velocity),
                            "--poll-hz",
                            str(opt.fleet_poll_hz),
                            "--linear-accel",
                            str(opt.linear_accel),
                            "--angular-speed",
                            str(opt.angular_speed),
                            "--angular-accel",
                            str(opt.angular_accel),
                        ],
                        runtime_root=runtime_root,
                        log_path=self._log_path(runtime_root, opt, "_runtime_fleet_manager.log"),
                    )
                )
                time.sleep(1.5)

            if opt.start_rmf_core and not self._core_running():
                run_core = runtime_root / "run_rmf_layout.sh"
                if not run_core.is_file():
                    raise RuntimeError(f"missing run_rmf_layout.sh: {run_core}")

                env = dict(os.environ)
                env["RMF_LAYOUT_MODE"] = "headless" if opt.mode == "headless" else "sim"
                env["USE_SIM_TIME"] = "true" if opt.use_sim_time else "false"
                fc = self._resolve_fleet_config(opt, runtime_root, robot_names, start_docks)
                if fc:
                    env["FLEET_CONFIG"] = fc
                started_now.append(
                    self._spawn(
                        name="rmf-core",
                        cmd=["bash", str(run_core)],
                        runtime_root=runtime_root,
                        env=env,
                        log_path=self._log_path(runtime_root, opt, "_runtime_rmf_core.log"),
                    )
                )

            min_robots = int(opt.min_registered_robots or 0)
            if bridge_mode != "none" and min_robots > 0:
                ok = self._wait_for_fleet(
                    api_url=opt.api_url,
                    api_token=opt.api_token,
                    min_robots=min_robots,
                    timeout_s=opt.wait_fleet_timeout_s,
                )
                if not ok:
                    raise RuntimeError(f"fleet registration timeout: need >= {min_robots} robots")

            self._write_state(opt, started_now, robot_names)
            self._spawned.extend(started_now)
            return {
                "success": True,
                "runtime_root": str(runtime_root),
                "map_dir": str(Path(opt.map_dir)),
                "mode": opt.mode,
                "bridge_mode": bridge_mode,
                "started": [p.name for p in started_now],
                "robot_names": robot_names,
                "state_file": str(self._state_path(runtime_root)),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf-runtime] start failed: {e}")
            self._terminate_processes(started_now)
            return {
                "success": False,
                "error": str(e),
                "runtime_root": str(runtime_root),
                "started": [p.name for p in started_now],
            }

    def stop_runtime_stack(self, options: RmfRuntimeOptions, *, include_shell_stop: bool = False) -> Dict[str, Any]:
        opt = self._normalize_options(options)
        runtime_root = Path(opt.runtime_root)
        state = self._read_state(runtime_root)
        terminated: List[str] = []

        # 先按 state 文件里的 pid 回收（跨进程可用）
        for item in state.get("processes") or []:
            name = str(item.get("name") or "")
            pid = int(item.get("pid") or 0)
            if pid <= 0:
                continue
            if self._kill_pid_group(pid):
                terminated.append(name or f"pid:{pid}")

        # 再回收当前 launcher 实例内的进程句柄
        local: List[_SpawnedProc] = [p for p in self._spawned if p.runtime_root == str(runtime_root)]
        self._terminate_processes(local)
        if local:
            terminated.extend([p.name for p in local])
        self._spawned = [p for p in self._spawned if p.runtime_root != str(runtime_root)]

        if include_shell_stop:
            stop_sh = runtime_root / "stop_all_rmf.sh"
            if stop_sh.is_file():
                try:
                    subprocess.run(["bash", str(stop_sh)], cwd=str(runtime_root), check=False)
                except Exception:  # noqa: BLE001
                    pass

        self._remove_state(runtime_root)
        return {
            "success": True,
            "runtime_root": str(runtime_root),
            "terminated": sorted(set(terminated)),
        }

    def runtime_status(self, options: RmfRuntimeOptions) -> Dict[str, Any]:
        opt = self._normalize_options(options)
        runtime_root = Path(opt.runtime_root)
        state = self._read_state(runtime_root)
        proc_status: List[Dict[str, Any]] = []
        for item in state.get("processes") or []:
            name = str(item.get("name") or "")
            pid = int(item.get("pid") or 0)
            proc_status.append(
                {
                    "name": name,
                    "pid": pid,
                    "alive": self._pid_alive(pid),
                }
            )
        return {
            "success": True,
            "runtime_root": str(runtime_root),
            "map_dir": str(Path(opt.map_dir)),
            "apiReachable": self._api_reachable(opt.api_url, opt.api_token),
            "coreRunning": self._core_running(),
            "stateExists": self._state_path(runtime_root).is_file(),
            "processes": proc_status,
            "bridgeMode": state.get("bridge_mode") or "",
            "robotNames": state.get("robot_names") or [],
            "startedAt": state.get("started_at") or "",
        }

    # ========================================================= normalization
    def _normalize_options(self, options: RmfRuntimeOptions) -> RmfRuntimeOptions:
        opt = RmfRuntimeOptions(**asdict(options))

        runtime_root = self._resolve_runtime_root(opt.runtime_root, opt.map_dir)
        map_dir = self._resolve_map_dir(opt.map_dir, runtime_root)
        opt.runtime_root = str(runtime_root)
        opt.map_dir = str(map_dir)

        if opt.mode not in {"headless", "sim"}:
            opt.mode = "headless"
        if opt.bridge_mode not in {"none", "mock", "sim_bridge"}:
            opt.bridge_mode = "none"
        if not opt.robots:
            opt.robots = ["unilab_agv1"]
        opt.wait_api_timeout_s = max(2.0, float(opt.wait_api_timeout_s))
        opt.wait_fleet_timeout_s = max(2.0, float(opt.wait_fleet_timeout_s))
        return opt

    @staticmethod
    def _resolve_runtime_root(runtime_root: str, map_dir: str) -> Path:
        if runtime_root:
            return Path(runtime_root).expanduser().resolve()
        if map_dir:
            p = Path(map_dir).expanduser().resolve()
            if p.name == "latest" and p.parent.name == "maps":
                return p.parent.parent
            if p.parent.name == "maps":
                return p.parent.parent
            return p.parent
        return Path(".").resolve()

    @staticmethod
    def _resolve_map_dir(map_dir: str, runtime_root: Path) -> Path:
        if map_dir:
            return Path(map_dir).expanduser().resolve()
        return (runtime_root / "maps" / "latest").resolve()

    # ========================================================= process helpers
    @staticmethod
    def _env_for_conda_python(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """给 unilab conda python 子进程用的环境：去掉 /opt/ros 的 PYTHONPATH 污染。"""
        env = dict(base if base is not None else os.environ)
        raw = env.get("PYTHONPATH", "")
        if raw:
            parts = [p for p in raw.split(":") if p and "/opt/ros/" not in p.replace("\\", "/")]
            if parts:
                env["PYTHONPATH"] = ":".join(parts)
            else:
                env.pop("PYTHONPATH", None)
        return env

    def _spawn(
        self,
        *,
        name: str,
        cmd: List[str],
        runtime_root: Path,
        log_path: Path,
        env: Optional[Dict[str, str]] = None,
    ) -> _SpawnedProc:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        out = open(log_path, "ab")
        # sim-bridge / fleet-manager 跑 conda python，需避免系统 ROS PYTHONPATH
        spawn_env = env
        if name in {"sim-bridge", "fleet-manager", "mock-agv"}:
            spawn_env = self._env_for_conda_python(env)
        proc = subprocess.Popen(
            cmd,
            cwd=str(runtime_root),
            env=spawn_env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        logger.info(f"[rmf-runtime] start {name} pid={proc.pid} cmd={' '.join(cmd)}")
        return _SpawnedProc(
            name=name,
            proc=proc,
            runtime_root=str(runtime_root),
            log_path=str(log_path),
        )

    @staticmethod
    def _terminate_processes(items: List[_SpawnedProc]) -> None:
        # 先 TERM
        for item in reversed(items):
            try:
                os.killpg(os.getpgid(item.proc.pid), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                pass
        time.sleep(1.0)
        # 再 KILL
        for item in reversed(items):
            try:
                os.killpg(os.getpgid(item.proc.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _kill_pid_group(pid: int) -> bool:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            time.sleep(0.6)
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ========================================================= state file
    def _state_path(self, runtime_root: Path) -> Path:
        return runtime_root / ".runtime" / self._STATE_FILE

    def _read_state(self, runtime_root: Path) -> Dict[str, Any]:
        p = self._state_path(runtime_root)
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            return {}

    def _write_state(self, opt: RmfRuntimeOptions, started: List[_SpawnedProc], robot_names: List[str]) -> None:
        root = Path(opt.runtime_root)
        p = self._state_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "started_at": int(time.time() * 1000),
            "runtime_root": opt.runtime_root,
            "map_dir": opt.map_dir,
            "mode": opt.mode,
            "bridge_mode": opt.bridge_mode,
            "robot_names": robot_names,
            "processes": [{"name": s.name, "pid": int(s.proc.pid), "log_path": s.log_path} for s in started],
            "options": asdict(opt),
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _remove_state(self, runtime_root: Path) -> None:
        p = self._state_path(runtime_root)
        if p.is_file():
            try:
                p.unlink()
            except Exception:  # noqa: BLE001
                pass

    # ========================================================= health checks
    @staticmethod
    def _auth_headers(token: str) -> Dict[str, str]:
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _api_reachable(self, api_url: str, token: str) -> bool:
        if requests is None:
            return False
        try:
            resp = requests.get(
                f"{api_url.rstrip('/')}/tasks?limit=1",
                headers=self._auth_headers(token),
                timeout=4.0,
            )
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False

    def _wait_for_api(self, api_url: str, token: str, *, timeout_s: float) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._api_reachable(api_url, token):
                return True
            time.sleep(1.0)
        return False

    def _wait_for_fleet(self, *, api_url: str, api_token: str, min_robots: int, timeout_s: float) -> bool:
        if requests is None:
            return False
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                resp = requests.get(
                    f"{api_url.rstrip('/')}/fleets",
                    headers=self._auth_headers(api_token),
                    timeout=6.0,
                )
                if resp.status_code == 200:
                    rows = resp.json() if resp.content else []
                    total = 0
                    if isinstance(rows, list):
                        for row in rows:
                            robots = (row or {}).get("robots") if isinstance(row, dict) else {}
                            total += len(robots or {})
                    if total >= max(1, min_robots):
                        return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.5)
        return False

    @staticmethod
    def _core_running() -> bool:
        """检测 RMF core 是否已在跑。

        注意：不能对整段 ``ps`` 输出做裸子串匹配。父 shell / agent 诊断命令行里
        常带有 ``strict_fleet_adapter.py`` 等字样，会误判为 core 已启动并跳过 launch，
        最终表现为 fleet registration timeout。
        """
        try:
            out = subprocess.run(["ps", "-eo", "pid,args"], check=False, capture_output=True, text=True)
        except Exception:  # noqa: BLE001
            return False
        # 逐行匹配「真正在跑」的进程形态，避免 cp/grep/estimate 父 shell 误伤
        patterns = (
            re.compile(r"(?:^|\s)(?:bash|sh)\s+\S*run_rmf_layout\.sh\b"),
            re.compile(r"\bbuilding_map_server\b"),
            re.compile(r"\bros2\s+launch\b.*\bunilab_layout(?:_headless)?\.launch\.xml\b"),
            re.compile(r"\bpython3?(?:\d[\d.]*)?\s+\S*strict_fleet_adapter\.py\b"),
            re.compile(r"(?:/|\s)(?:rmf_demos_)?fleet_adapter(?:\s|$)"),
        )
        for line in (out.stdout or "").splitlines():
            args = line.strip()
            if not args:
                continue
            # 排除诊断/停止脚本自身
            if "pkill" in args or "grep -E" in args or "stop_all_rmf" in args:
                continue
            if "rmf_estimate_times.py" in args:
                continue
            if any(p.search(args) for p in patterns):
                return True
        return False

    # ========================================================= bootstrap utils
    def _cleanup_before_start(self, opt: RmfRuntimeOptions) -> None:
        runtime_root = Path(opt.runtime_root)
        # bridge 模式（edge-free）才需要强清场；OS-first 避免误杀 edge 主进程。
        if opt.bridge_mode in {"mock", "sim_bridge"}:
            stop_sh = runtime_root / "stop_all_rmf.sh"
            if stop_sh.is_file():
                try:
                    subprocess.run(["bash", str(stop_sh)], cwd=str(runtime_root), check=False)
                except Exception:  # noqa: BLE001
                    pass
            for pat in (
                "_rmf_sim_bridge.py",
                "unilabos.sim.fleet.rmf.edge.agv_http_server",
                "unilabos.sim.fleet.rmf.edge.fleet_manager_http",
                "rmf_demos_fleet_adapter",
            ):
                try:
                    subprocess.run(["pkill", "-9", "-f", pat], check=False)
                except Exception:  # noqa: BLE001
                    pass
            self._clear_fastdds_shm()
            time.sleep(2.0)

    @staticmethod
    def _clear_fastdds_shm() -> None:
        for pat in ("fastrtps_*", "sem.fastrtps_*", "fast_datasharing_*"):
            for fp in Path("/dev/shm").glob(pat):
                try:
                    fp.unlink()
                except Exception:  # noqa: BLE001
                    pass

    def _resolve_robot_context(
        self, opt: RmfRuntimeOptions
    ) -> Tuple[List[str], List[Tuple[str, float, float, float]], List[str]]:
        # 返回：
        # - robot_names
        # - robot_specs(name,x,y,yaw)
        # - start_docks（用于写多车临时 fleet_config）
        if opt.robot_specs:
            specs = [self._parse_robot_spec(s) for s in opt.robot_specs]
            robot_names = [s[0] for s in specs]
            start_docks = [self._nearest_dock_name(opt.map_dir, s[1], s[2]) for s in specs]
            return robot_names, specs, start_docks

        robot_names = [str(r).strip() for r in opt.robots if str(r).strip()]
        if not robot_names:
            robot_names = ["unilab_agv1"]
        picks = self._pick_start_docks(opt.map_dir, len(robot_names))
        specs = [(nm, x, y, 0.0) for nm, (_dock, x, y) in zip(robot_names, picks)]
        start_docks = [d for d, _x, _y in picks]
        return robot_names, specs, start_docks

    @staticmethod
    def _parse_robot_spec(spec: str) -> Tuple[str, float, float, float]:
        # name[:x:y[:yaw]]
        chunks = [c.strip() for c in str(spec or "").split(":")]
        if len(chunks) < 1 or not chunks[0]:
            raise ValueError(f"invalid robot spec: {spec}")
        name = chunks[0]
        if len(chunks) == 1:
            return name, 0.0, 0.0, 0.0
        if len(chunks) < 3:
            raise ValueError(f"invalid robot spec: {spec}")
        x = float(chunks[1])
        y = float(chunks[2])
        yaw = float(chunks[3]) if len(chunks) >= 4 and chunks[3] else 0.0
        return name, x, y, yaw

    @staticmethod
    def _robot_specs_to_cli(specs: Sequence[Tuple[str, float, float, float]]) -> List[str]:
        out: List[str] = []
        for name, x, y, yaw in specs:
            out.extend(["--robot", f"{name}:{x}:{y}:{yaw}"])
        return out

    @staticmethod
    def _robot_names_to_cli(names: Sequence[str]) -> List[str]:
        out: List[str] = []
        for n in names:
            out.extend(["--robot", str(n)])
        return out

    @staticmethod
    def _pick_start_docks(map_dir: str, n: int) -> List[Tuple[str, float, float]]:
        docks: List[Tuple[str, float, float]] = []
        nav_yaml = Path(map_dir) / "nav_graphs" / "0.yaml"
        try:
            import yaml

            g = yaml.safe_load(nav_yaml.read_text(encoding="utf-8")) or {}
            for v in ((g.get("levels") or {}).get("L1") or {}).get("vertices") or []:
                if not isinstance(v, list) or len(v) < 3 or not isinstance(v[2], dict):
                    continue
                name = str(v[2].get("name") or "")
                if name.startswith("dock_"):
                    docks.append((name, float(v[0]), float(v[1])))
        except Exception:  # noqa: BLE001
            docks = []

        if not docks:
            docks = [("dock_96_0", 55.66, -24.70)]
        preferred = [d for d in docks if d[0].startswith("dock_96_")] + [d for d in docks if not d[0].startswith("dock_96_")]
        return [preferred[i % len(preferred)] for i in range(max(1, n))]

    @staticmethod
    def _nearest_dock_name(map_dir: str, x: float, y: float) -> str:
        nav_yaml = Path(map_dir) / "nav_graphs" / "0.yaml"
        best = ("dock_96_0", float("inf"))
        try:
            import yaml

            g = yaml.safe_load(nav_yaml.read_text(encoding="utf-8")) or {}
            for v in ((g.get("levels") or {}).get("L1") or {}).get("vertices") or []:
                if not isinstance(v, list) or len(v) < 3 or not isinstance(v[2], dict):
                    continue
                name = str(v[2].get("name") or "")
                if not name.startswith("dock_"):
                    continue
                dx = float(v[0]) - x
                dy = float(v[1]) - y
                d2 = dx * dx + dy * dy
                if d2 < best[1]:
                    best = (name, d2)
        except Exception:  # noqa: BLE001
            pass
        return best[0]

    def _resolve_fleet_config(
        self,
        opt: RmfRuntimeOptions,
        runtime_root: Path,
        robot_names: Sequence[str],
        start_docks: Sequence[str],
    ) -> str:
        if opt.fleet_config_override:
            return str(Path(opt.fleet_config_override).expanduser().resolve())
        if len(robot_names) <= 1:
            return ""
        base_cfg = Path(opt.map_dir) / "fleet_config.yaml"
        if not base_cfg.is_file():
            return ""
        out_cfg = runtime_root / ".runtime" / f"fleet_config_{len(robot_names)}robots.yaml"
        out_cfg.parent.mkdir(parents=True, exist_ok=True)
        try:
            import yaml

            cfg = yaml.safe_load(base_cfg.read_text(encoding="utf-8")) or {}
            charger_wp = start_docks[0] if start_docks else "dock_96_0"
            # 真实 AGV 尺寸（中型车）：footprint=0.5m（机器人半径）、vicinity=1.0m（安全间距半径）。
            # 这是真实机器人尺寸，不是“逼开两车”的兜底 —— RMF traffic scheduler 用它判定冲突并
            # 保持 ≥vicinity 的间距（路过车会在 ~1m 外 hold，配合 responsive_wait 让空闲车让位），
            # 从而从 RMF 调度层根治共享 waypoint 的同点重合。
            rmf_fleet = cfg.setdefault("rmf_fleet", {})
            profile = rmf_fleet.setdefault("profile", {})
            profile["footprint"] = 0.5
            profile["vicinity"] = 1.0
            # 真实充电/停靠点：地图里带 is_charger 的 dock_charge_*（角落，远离主干），互不相同。
            # finishing_request=charge：空闲车由 RMF 自动送回各自充电点，而不是就地停在 star_* 主干挡路。
            charge_wps = self._charger_waypoints(opt.map_dir)
            task_caps = rmf_fleet.setdefault("task_capabilities", {})
            task_caps["finishing_request"] = "charge"

            def _charger_for(idx: int) -> str:
                if charge_wps:
                    return charge_wps[idx % len(charge_wps)]
                return str(start_docks[idx] if idx < len(start_docks) else charger_wp)

            # responsive_wait=true 是 RMF fleet adapter 自带的“拥堵让位”特性：空闲车若占用
            # 其它车需要经过/到达的 waypoint，会主动重规划让路。
            cfg["robots"] = {
                name: {
                    "robot_config": {"max_delay": 15.0, "responsive_wait": True},
                    "rmf_config": {
                        "robot_state_update_frequency": 10.0,
                        "start": {
                            "map_name": "L1",
                            "waypoint": str(start_docks[idx] if idx < len(start_docks) else charger_wp),
                            "orientation": 0.0,
                        },
                        "charger": {"waypoint": _charger_for(idx)},
                    },
                }
                for idx, name in enumerate(robot_names)
            }
            out_cfg.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
            return str(out_cfg)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf-runtime] write temporary fleet_config failed: {e}")
            return ""

    @staticmethod
    def _charger_waypoints(map_dir: str) -> List[str]:
        """从 nav_graph 找真正的充电点：带 is_charger 且名字以 dock_charge_ 开头（角落、非物料 dock）。"""
        nav_yaml = Path(map_dir) / "nav_graphs" / "0.yaml"
        out: List[str] = []
        try:
            import yaml

            g = yaml.safe_load(nav_yaml.read_text(encoding="utf-8")) or {}
            for v in g["levels"]["L1"]["vertices"]:
                props = v[2] if len(v) > 2 and isinstance(v[2], dict) else {}
                name = str(props.get("name") or "")
                if name.startswith("dock_charge_") and props.get("is_charger"):
                    out.append(name)
        except Exception:  # noqa: BLE001
            return []
        return sorted(out)

    @staticmethod
    def _sim_bridge_script_path() -> Path:
        # .../Uni-Lab-OS/unilabos/sim/fleet/rmf/runtime/launcher.py -> .../Uni-Lab-OS/scripts/_rmf_sim_bridge.py
        return Path(__file__).resolve().parents[5] / "scripts" / "_rmf_sim_bridge.py"

    @staticmethod
    def _log_path(runtime_root: Path, opt: RmfRuntimeOptions, filename: str) -> Path:
        base = Path(opt.logs_dir).expanduser().resolve() if opt.logs_dir else runtime_root
        return base / filename

