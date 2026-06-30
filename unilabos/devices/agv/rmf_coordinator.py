"""`rmf.coordinator`：RMF 调度域的 OS device facade（#17 §7.1 / #18 §6.2-(3)）。

聚合编译器 + gateway + task_dispatcher + event_collector + backend_reporter，对外以
OS device action/status 暴露：compile_map / start_runtime / stop_runtime /
dispatch_go_to / dispatch_delivery / cancel_task / query_runtime /
run_designer_transfers / query_designer_transfers_run / cancel_designer_transfers_run /
set_sim_rate / pause_sim_clock / resume_sim_clock / get_sim_clock_status。

RMF 启用入口：graph 中出现本设备即代表该实验室启用 RMF（图驱动，#17 §2.1）。
重运行时（ROS/进程）惰性接线；离线（无 RMF）时编译/信封组装仍可用，便于测试。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("rmf.coordinator")

from unilabos.registry.decorators import topic_config

# 本地 rmf-web api-server 开发态 stub JWT（与 scripts/rmf_os_read_tasks.py 一致）；
# OS action 下发任务时用它 POST 本地 /tasks/dispatch_task（避开 rmf_task_msgs ROS ABI，#22 §3）。
_DEFAULT_API_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJzdHViIiwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWRtaW4iLCJpYXQiOjE1MTYyMzkwMjIsImF1ZCI6InJtZl9hcGlfc2VydmVyIiwiaXNzIjoic3R1YiIsImV4cCI6MjA1MTIyMjQwMH0."
    "zzX3zXp467ldkzmLVIadQ_AHr8M5uWVV43n4wEB0OhE"
)


class RmfCoordinator:
    def __init__(
        self,
        lab_uuid: str = "",
        fleet_name: str = "unilab_agv",
        map_source: str = "backend_published_floorplan",
        generated_map_dir: str = "/tmp/unilabos/rmf_maps",
        device_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        self.device_id = device_id or "rmf_coordinator"
        self.lab_uuid = lab_uuid
        self.fleet_name = fleet_name
        self.map_source = map_source
        self.generated_map_dir = generated_map_dir
        # graph 节点 config 的额外键经 **kwargs 传入（框架把 driver_params 展开为 kwargs），
        # 合并进 self.config，使 fleet_manager_port / edge_url / robots 等可读。
        self.config = {**(config or {}), **kwargs}
        self.data: Dict[str, Any] = {}

        # 运行态缓存（status_types 暴露）
        self._runtime_status = "stopped"
        self._scene_hash = ""
        self._map_version = ""
        self._artifact_id = ""
        self._robot_states: Dict[str, Dict[str, Any]] = {}
        self._task_states: Dict[str, Dict[str, Any]] = {}
        self._diagnostics: List[Dict[str, Any]] = []
        # 运行能力（供 cloud capability-driven UI 显隐控制项）
        self._runtime_mode: str = "real"
        self._supports_sim_clock: bool = False
        self._supports_robot_speed_control: bool = False
        self._sim_rate: float = 1.0
        self._sim_paused: bool = False
        # nav_graph（导航点+设备点+路径）上报缓存（楼层帧 mm，#24.1 §1）
        self._nav_graph_json: str = ""
        self._floor = None  # FloorFrame（边缘帧→楼层帧）

        # 组件（惰性）
        self._dispatcher = None
        self._gateway = None
        self._reporter = None
        self._live_source = None
        self._last_building: Optional[Dict[str, Any]] = None
        self._last_semantic: Optional[Dict[str, Any]] = None
        self._last_transfer_plan: Optional[Dict[str, Any]] = None
        self._dispatch_ack: Dict[str, str] = {}
        self._dispatch_ack_lock = threading.Lock()
        self._designer_transfer_lock = threading.Lock()
        self._designer_transfer_cancel = threading.Event()
        self._designer_transfer_thread: Optional[threading.Thread] = None
        self._designer_transfer_run: Dict[str, Any] = {}

        # 车队主控制层（#18 §10.4）：OS 即 fleet owner，接收 RMF fleet_adapter 指令并驱动 AGV 硬件。
        # 框架不调用 async initialize()，故在 __init__（构造时必执行）直接启动。
        self._fleet_manager = None  # EdgeFleetManager
        self._designer_replay = None  # DesignerRouteReplay（designer 规划模式回放设计折线，#22 §3）
        self._start_fleet_manager()
        self._load_designer_transfer_run_state()
        # 初始化能力快照（部分启动路径不会及时回调 initialize()，先给 data 落一份当前值）
        self._refresh_data()

    async def initialize(self) -> bool:
        self._refresh_data()
        # 图驱动接线：把本设备的 RmfLiveSource 挂到 runtime context，供 query API 注册
        # （main_slave_run._build_query_static_sources 读取 ctx.rmf_live_source）。
        try:
            from unilabos.sim.context import get_runtime_context

            ctx = get_runtime_context()
            if getattr(ctx, "rmf_live_source", None) is None:
                ctx.rmf_live_source = self.get_live_source()
        except Exception:  # noqa: BLE001
            pass
        # 兜底：若框架未在 __init__ 后保留实例，这里再确保车队主在跑（幂等）
        self._start_fleet_manager()
        return True

    async def cleanup(self) -> bool:
        if self._designer_transfer_thread is not None and self._designer_transfer_thread.is_alive():
            self._designer_transfer_cancel.set()
            self._designer_transfer_thread.join(timeout=2.0)
        if self._fleet_manager is not None:
            try:
                self._fleet_manager.stop()
            except Exception:  # noqa: BLE001
                pass
            self._fleet_manager = None
        try:
            self.stop_runtime()
        except Exception:  # noqa: BLE001
            pass
        return True

    # ============================================================ actions
    def compile_map(
        self,
        scene: Optional[Dict[str, Any]] = None,
        robots: Optional[List[Dict[str, Any]]] = None,
        scene_hash: str = "",
        force: bool = False,
        layout_optimizer_dir: Optional[str] = None,
        route_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """编译发布版 scene 或 layout-optimizer 目录 → building.yaml + semantic_map.json + 诊断（#18 §4.1 / §9）。

        `route_overrides`：可选的最小路线编辑（#21 §7.0 入口 B），仅 layout-optimizer 路径生效。
        """
        from unilabos.sim.fleet.rmf.compiler import compile_layout_optimizer_dir, compile_scene

        transfer_plan: Optional[Dict[str, Any]] = None
        if layout_optimizer_dir:
            ir, building, semantic, transfer_plan = compile_layout_optimizer_dir(
                layout_optimizer_dir,
                robots,
                lab_uuid=self.lab_uuid,
                scene_hash=scene_hash or self._scene_hash,
                route_overrides=route_overrides,
            )
        else:
            if scene is None:
                scene = self._fetch_published_scene()
            if scene is None:
                return {"success": False, "artifact_id": "", "diagnostics": [{"level": "error", "code": "no_scene", "message": "无法获取发布版 scene"}]}

            ir, building, semantic = compile_scene(
                scene, robots, lab_uuid=self.lab_uuid, scene_hash=scene_hash or self._scene_hash
            )
        self._last_building = building
        self._last_semantic = semantic
        self._last_transfer_plan = transfer_plan
        self._scene_hash = scene_hash or self._scene_hash
        self._diagnostics = ir.diagnostics_as_dicts()
        self._artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        self._map_version = self._artifact_id

        artifact_dir = self._write_artifacts(building, semantic)
        success = not ir.has_errors()
        self._refresh_data()
        logger.info(f"[rmf] compile_map success={success} artifact={self._artifact_id} dir={artifact_dir}")
        result: Dict[str, Any] = {"success": success, "artifact_id": self._artifact_id, "diagnostics": self._diagnostics}
        if transfer_plan is not None:
            result["transfer_plan"] = transfer_plan
            result["transfer_count"] = len(transfer_plan.get("transfers") or [])
        return result

    def start_runtime(self, mode: str = "sim", artifact_id: str = "") -> Dict[str, Any]:
        """启动 RMF runtime（gateway + reporter）。无 ProcessSpec/无 ROS 时优雅降级。"""
        self._runtime_status = "starting"
        self._refresh_data()
        try:
            self._ensure_gateway()
            building_path = os.path.join(self.generated_map_dir, f"{self.lab_uuid or 'lab'}.building.yaml")
            if self._gateway is not None and os.path.exists(building_path):
                try:
                    self._gateway.generate_nav_graph(building_path)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[rmf] nav_graph 生成跳过（无 RMF 工具链？）: {e}")
                self._gateway.start()
            session_id = self._ensure_reporter(mode)
            self._runtime_status = "running"
            self._refresh_data()
            return {"success": True, "session_id": session_id}
        except Exception as e:  # noqa: BLE001
            self._runtime_status = "error"
            self._diagnostics.append({"level": "error", "code": "start_failed", "message": str(e)})
            self._refresh_data()
            return {"success": False, "session_id": "", "error": str(e)}

    def stop_runtime(self, session_id: str = "") -> Dict[str, Any]:
        if self._gateway is not None:
            self._gateway.stop()
        if self._reporter is not None:
            self._reporter.patch_session("stopped")
            self._reporter.stop()
        self._runtime_status = "stopped"
        self._refresh_data()
        return {"success": True}

    def dispatch_go_to(
        self,
        place: str = "",
        robot_name: str = "",
        orientation_deg: Optional[float] = None,
        plan_mode: str = "",
    ) -> Dict[str, Any]:
        """让 AGV 前往某黑点 `place`（如 `dock_96quench_0`）。

        `plan_mode` 控制规划来源（#22 §3）：
        - **`designer`（默认）**：回放 **designer 的精确直角折线**（`rmf_agv_routes.json` 的 per-transfer
          `geometryM`，转到边缘帧），OS 直接把整条 path 下发给小车，**沿设计路线行驶**；
          若无该 (当前dock→place) 预计算路线则**回退 `rmf`**。
        - **`rmf`**：下发 RMF 任务（patrol 单点），由 RMF 在 nav_graph（=designer 走廊）上规划最短路。

        `robot_name` 为空 → dispatch（竞标，分配给车队的车）；指定则直发该车。
        """
        mode = (plan_mode or self.config.get("default_plan_mode") or "designer").strip().lower()
        robot = robot_name or (self._robot_names() or ["unilab_agv1"])[0]

        if mode == "designer":
            res = self._designer_drive(place, robot)
            if res.get("success"):
                return res
            logger.warning(f"[rmf] designer 路线不可用（{res.get('error')}），回退 RMF 规划 → {place}")

        # rmf 模式（或 designer 回退）：RMF 在 designer 走廊图上规划
        from unilabos.sim.fleet.rmf.task_dispatcher import build_patrol_request

        envelope = build_patrol_request(
            [place], 1, fleet=self.fleet_name if robot_name else None, robot=robot_name or None
        )
        out = self._dispatch(envelope)
        out["mode"] = "rmf"
        return out

    def _robot_names(self) -> List[str]:
        r = self.config.get("robots")
        if isinstance(r, str):
            return [r]
        if isinstance(r, list) and r:
            return [str(x) for x in r]
        rn = self.config.get("robot_name")
        return [str(rn)] if rn else []

    def _get_designer_replay(self):
        """惰性加载 designer 路线回放器（rmf_agv_routes.json + nav_graph，#22 §3）。"""
        if self._designer_replay is None:
            from unilabos.sim.fleet.rmf.designer_route import DesignerRouteReplay

            routes_path = str(self.config.get("agv_routes_path") or os.path.join(self.generated_map_dir, "rmf_agv_routes.json"))
            nav_path = str(self.config.get("nav_graph_path") or os.path.join(self.generated_map_dir, "nav_graphs", "0.yaml"))
            self._designer_replay = DesignerRouteReplay(routes_path, nav_path)
        return self._designer_replay

    def _designer_drive(self, place: str, robot: str) -> Dict[str, Any]:
        """designer 模式：查 当前dock→place 的设计折线（边缘帧）→ 整条 path 直发小车（绕过 RMF 规划）。"""
        import requests

        try:
            replay = self._get_designer_replay()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "task_id": "", "error": f"加载 designer 路线失败: {e}"}
        if not replay.ready:
            return {"success": False, "task_id": "", "error": "designer 路线/nav_graph 帧对齐失败（公共黑点 <2）"}

        edge = str(self.config.get("edge_url") or "http://127.0.0.1:8090").rstrip("/")
        try:
            st = requests.get(f"{edge}/agv/state", params={"robot": robot}, timeout=3).json()
            cur_dock = replay.nearest_edge_dock(float(st["x"]), float(st["y"]))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "task_id": "", "error": f"取小车当前位姿失败: {e}"}

        path = replay.path_edge(cur_dock or "", place)
        if not path:
            return {"success": False, "task_id": "", "error": f"无 designer 预计算路线 {cur_dock}→{place}"}

        cmd_id = int(time.time() * 1000) % 1_000_000
        body = {
            "robot": robot,
            "cmdId": cmd_id,
            "destination": {"x": path[-1][0], "y": path[-1][1], "yaw": 0.0, "level": "L1"},
            "path": [{"x": round(x, 3), "y": round(y, 3), "yaw": 0.0} for (x, y) in path],
            "taskId": f"designer.{cmd_id}",
        }
        try:
            r = requests.post(f"{edge}/agv/navigate", json=body, timeout=5)
            ok = r.status_code == 200
        except Exception as e:  # noqa: BLE001
            return {"success": False, "task_id": "", "error": f"下发 edge 失败: {e}"}
        logger.info(
            f"[rmf] designer 路线回放 {cur_dock}→{place}：{len(path)} 点直发小车（绕过 RMF 规划）ok={ok}"
        )
        return {
            "success": ok,
            "task_id": f"designer.{cmd_id}",
            "mode": "designer",
            "from": cur_dock,
            "to": place,
            "points": len(path),
        }

    def dispatch_delivery(
        self,
        pickup: str = "",
        dropoff: str = "",
        pickup_handler: str = "",
        dropoff_handler: str = "",
        payload: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        from unilabos.sim.fleet.rmf.task_dispatcher import build_delivery_request

        envelope = build_delivery_request(pickup, pickup_handler, dropoff, dropoff_handler, payload)
        return self._dispatch(envelope)

    def run_designer_transfers(
        self,
        transfers_path: str = "",
        routes_path: str = "",
        start_index: int = 0,
        max_count: int = 0,
        on_error: str = "abort",
        retry_max: int = 1,
        wait_timeout_s: float = 1800.0,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """按 transfers.json 顺序串行下发 delivery 任务（后台执行线程）。"""
        with self._designer_transfer_lock:
            if self._designer_transfer_thread is not None and self._designer_transfer_thread.is_alive():
                run_id = str(self._designer_transfer_run.get("run_id") or "")
                return {
                    "success": False,
                    "run_id": run_id,
                    "status": str(self._designer_transfer_run.get("status") or "running"),
                    "error": "designer transfers run is already in progress",
                }

        on_error_norm = str(on_error or "abort").strip().lower()
        if on_error_norm not in ("abort", "skip"):
            return {"success": False, "run_id": "", "status": "invalid", "error": "on_error must be abort or skip"}

        try:
            start = max(0, int(start_index))
            limit_count = max(0, int(max_count))
            retries = max(0, int(retry_max))
            timeout_s = max(1.0, float(wait_timeout_s))
        except Exception:
            return {
                "success": False,
                "run_id": "",
                "status": "invalid",
                "error": "invalid numeric args (start_index/max_count/retry_max/wait_timeout_s)",
            }

        resolved_transfers_path = self._resolve_transfers_path(transfers_path)
        if not resolved_transfers_path:
            return {
                "success": False,
                "run_id": "",
                "status": "invalid",
                "error": "transfers_path is required (or set config.designer_transfers_path/layout_optimizer_dir)",
            }
        resolved_routes_path = self._resolve_routes_path(routes_path)
        if not resolved_routes_path:
            return {
                "success": False,
                "run_id": "",
                "status": "invalid",
                "error": "routes_path is required (or set config.agv_routes_path)",
            }

        try:
            plan = self._build_designer_transfer_plan(
                transfers_path=resolved_transfers_path,
                routes_path=resolved_routes_path,
                start_index=start,
                max_count=limit_count,
            )
        except Exception as e:  # noqa: BLE001
            return {"success": False, "run_id": "", "status": "invalid", "error": str(e)}

        now_ms = int(time.time() * 1000)
        run_id = f"designer_transfers.{now_ms}"
        run_state: Dict[str, Any] = {
            "run_id": run_id,
            "status": "validated" if dry_run else "running",
            "cursor": int(plan["start_index"]),
            "start_index": int(plan["start_index"]),
            "end_index_exclusive": int(plan["end_index_exclusive"]),
            "source_total": int(plan["source_total"]),
            "total": int(plan["run_total"]),
            "remaining": int(plan["remaining"]),
            "dispatched": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "last_task_id": "",
            "last_error": "",
            "last_error_index": -1,
            "on_error": on_error_norm,
            "max_count": limit_count,
            "retry_max": retries,
            "wait_timeout_s": timeout_s,
            "dry_run": bool(dry_run),
            "transfers_path": resolved_transfers_path,
            "routes_path": resolved_routes_path,
            "transfers_digest": str(plan.get("transfers_digest") or ""),
            "started_at_ms": now_ms,
            "updated_at_ms": now_ms,
        }
        if dry_run:
            run_state["preview"] = plan["preview"]

        with self._designer_transfer_lock:
            self._designer_transfer_cancel.clear()
            self._designer_transfer_run = run_state
            self._persist_designer_transfer_run_locked()

        if dry_run or plan["remaining"] <= 0:
            if not dry_run:
                with self._designer_transfer_lock:
                    self._designer_transfer_run["status"] = "completed"
                    self._designer_transfer_run["updated_at_ms"] = int(time.time() * 1000)
                    self._persist_designer_transfer_run_locked()
            return {
                "success": True,
                "run_id": run_id,
                "total": int(plan["run_total"]),
                "start_index": int(plan["start_index"]),
                "status": str(self._designer_transfer_run.get("status") or "validated"),
            }

        th = threading.Thread(
            target=self._designer_transfer_worker,
            args=(run_id, plan["entries"], on_error_norm, retries, timeout_s),
            name=f"designer-transfers-{run_id[-8:]}",
            daemon=True,
        )
        with self._designer_transfer_lock:
            self._designer_transfer_thread = th
        th.start()
        logger.info(
            f"[rmf] run_designer_transfers started run_id={run_id} "
            f"start_index={plan['start_index']} run_total={plan['run_total']} "
            f"source_total={plan['source_total']} remaining={plan['remaining']}"
        )
        return {
            "success": True,
            "run_id": run_id,
            "total": int(plan["run_total"]),
            "start_index": int(plan["start_index"]),
            "status": "running",
        }

    def query_designer_transfers_run(self, run_id: str = "") -> Dict[str, Any]:
        """查询当前（或指定）designer transfers run 状态。"""
        with self._designer_transfer_lock:
            if not self._designer_transfer_run:
                return {"success": False, "run_id": "", "status": "idle", "error": "no designer transfers run"}
            state = dict(self._designer_transfer_run)
            thread_alive = bool(self._designer_transfer_thread is not None and self._designer_transfer_thread.is_alive())
        if run_id and str(state.get("run_id") or "") != str(run_id):
            return {
                "success": False,
                "run_id": str(state.get("run_id") or ""),
                "status": str(state.get("status") or "unknown"),
                "error": f"run_id mismatch: requested={run_id}",
            }
        return {"success": True, "thread_alive": thread_alive, **state}

    def cancel_designer_transfers_run(self, run_id: str = "") -> Dict[str, Any]:
        """请求停止当前 designer transfers run（边界安全停止）。"""
        with self._designer_transfer_lock:
            if not self._designer_transfer_run:
                return {"success": False, "run_id": "", "status": "idle", "error": "no designer transfers run"}
            current_run_id = str(self._designer_transfer_run.get("run_id") or "")
            status = str(self._designer_transfer_run.get("status") or "")
            if run_id and run_id != current_run_id:
                return {"success": False, "run_id": current_run_id, "status": status, "error": "run_id mismatch"}
            if status not in ("running", "stopping"):
                return {"success": False, "run_id": current_run_id, "status": status, "error": "run is not running"}
            self._designer_transfer_cancel.set()
            self._designer_transfer_run["status"] = "stopping"
            self._designer_transfer_run["updated_at_ms"] = int(time.time() * 1000)
            self._persist_designer_transfer_run_locked()
            return {"success": True, "run_id": current_run_id, "status": "stopping"}

    def cancel_task(self, task_id: str = "") -> Dict[str, Any]:
        from unilabos.sim.fleet.rmf.task_dispatcher import build_cancel_request

        return self._dispatch(build_cancel_request(task_id))

    def set_sim_rate(self, rate: float = 1.0) -> Dict[str, Any]:
        """设置仿真时钟倍率（仅 sim/twin）。"""
        status = self._runtime_clock_status()
        if not status.get("success", False):
            return status
        try:
            from unilabos.sim.context import get_runtime_context

            changed = bool(get_runtime_context().clock.set_scale(float(rate)))
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "message": f"set sim rate failed: {e}",
                "mode": status.get("mode", self._runtime_mode),
                "rate": status.get("rate", self._sim_rate),
                "paused": status.get("paused", self._sim_paused),
                "sim_now": status.get("sim_now", 0.0),
            }
        self._refresh_data()
        latest = self._runtime_clock_status()
        latest["changed"] = changed
        if latest.get("success", False):
            latest["message"] = "ok" if changed else "rate locked"
        return latest

    def pause_sim_clock(self) -> Dict[str, Any]:
        """暂停仿真时钟（仅 sim/twin）。"""
        status = self._runtime_clock_status()
        if not status.get("success", False):
            return status
        try:
            from unilabos.sim.context import get_runtime_context

            changed = bool(get_runtime_context().clock.pause())
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "message": f"pause sim clock failed: {e}",
                "mode": status.get("mode", self._runtime_mode),
                "rate": status.get("rate", self._sim_rate),
                "paused": status.get("paused", self._sim_paused),
                "sim_now": status.get("sim_now", 0.0),
            }
        self._refresh_data()
        latest = self._runtime_clock_status()
        latest["changed"] = changed
        if latest.get("success", False):
            latest["message"] = "paused" if changed else "pause locked"
        return latest

    def resume_sim_clock(self) -> Dict[str, Any]:
        """恢复仿真时钟（仅 sim/twin）。"""
        status = self._runtime_clock_status()
        if not status.get("success", False):
            return status
        try:
            from unilabos.sim.context import get_runtime_context

            changed = bool(get_runtime_context().clock.resume())
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "message": f"resume sim clock failed: {e}",
                "mode": status.get("mode", self._runtime_mode),
                "rate": status.get("rate", self._sim_rate),
                "paused": status.get("paused", self._sim_paused),
                "sim_now": status.get("sim_now", 0.0),
            }
        self._refresh_data()
        latest = self._runtime_clock_status()
        latest["changed"] = changed
        if latest.get("success", False):
            latest["message"] = "resumed" if changed else "resume locked"
        return latest

    def get_sim_clock_status(self) -> Dict[str, Any]:
        """读取当前仿真时钟状态（mode/rate/paused/sim_now）。"""
        self._refresh_data()
        return self._runtime_clock_status()

    def query_runtime(self) -> Dict[str, Any]:
        self._refresh_data()
        clock_status = self._runtime_clock_status()
        with self._designer_transfer_lock:
            run_state = dict(self._designer_transfer_run)
        return {
            "robot_states": self._current_robot_states(),
            "task_states": list(self._task_states.values()),
            "diagnostics": self._diagnostics,
            "runtime_mode": self._runtime_mode,
            "supports_sim_clock": self._supports_sim_clock,
            "supports_robot_speed_control": self._supports_robot_speed_control,
            "sim_rate": self._sim_rate,
            "sim_paused": self._sim_paused,
            "sim_clock": {
                "success": bool(clock_status.get("success", False)),
                "message": str(clock_status.get("message", "")),
                "mode": str(clock_status.get("mode", self._runtime_mode)),
                "rate": float(clock_status.get("rate", self._sim_rate)),
                "paused": bool(clock_status.get("paused", self._sim_paused)),
                "sim_now": float(clock_status.get("sim_now", 0.0)),
            },
            "designer_transfers_run": run_state,
        }

    def _current_robot_states(self) -> List[Dict[str, Any]]:
        """优先取车队主控制层缓存的真实小车状态（OS 驱动的 mock AGV），否则取事件缓存。"""
        if self._fleet_manager is not None:
            try:
                return self._fleet_manager.robot_states()
            except Exception:  # noqa: BLE001
                pass
        return list(self._robot_states.values())

    # ============================================================ status_types
    @property
    def runtime_status(self) -> str:
        return self._runtime_status

    @property
    def runtime_mode(self) -> str:
        # 框架在部分启动路径下不会稳定调用 async initialize()，这里按读取时实时刷新能力字段。
        self._runtime_mode = self._detect_runtime_mode()
        return self._runtime_mode

    @property
    def supports_sim_clock(self) -> bool:
        mode = self.runtime_mode
        self._supports_sim_clock = self._detect_supports_sim_clock(mode)
        return self._supports_sim_clock

    @property
    def supports_robot_speed_control(self) -> bool:
        self._supports_robot_speed_control = self._detect_supports_robot_speed_control()
        return self._supports_robot_speed_control

    @property
    def sim_rate(self) -> float:
        self._sim_rate = float(self._runtime_clock_status().get("rate", self._sim_rate))
        return self._sim_rate

    @property
    def sim_paused(self) -> bool:
        self._sim_paused = bool(self._runtime_clock_status().get("paused", self._sim_paused))
        return self._sim_paused

    @property
    def scene_hash(self) -> str:
        return self._scene_hash

    @property
    def map_version(self) -> str:
        return self._map_version

    @property
    def robot_states(self) -> str:
        # 非标量 → 以 JSON str 暴露，规避 property_callback 标量限制（#18 §1.5/§4.4）
        return json.dumps(self._current_robot_states(), ensure_ascii=False)

    @property
    def task_states(self) -> str:
        return json.dumps(list(self._task_states.values()), ensure_ascii=False)

    @property
    def diagnostics(self) -> str:
        return json.dumps(self._diagnostics, ensure_ascii=False)

    @property
    def designer_transfers_run(self) -> str:
        with self._designer_transfer_lock:
            state = dict(self._designer_transfer_run)
        return json.dumps(state, ensure_ascii=False)

    @property
    @topic_config(period=10.0)
    def nav_graph(self) -> str:
        """RMF 导航点 + 设备点 + 路径，转楼层帧 mm，作前端 `{waypoints, lanes}` JSON 串上报（#24.1 §1）。

        走既有 `device_status` 通用透传（任意属性名）→ `material_node.data.nav_graph` → 前端
        `importNavigationData`。静态数据：惰性载入并缓存，HostNode 仅"值变化"才上报。
        """
        if not self._nav_graph_json:
            try:
                self._load_nav_graph()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[rmf] nav_graph 载入失败: {e}")
                self._nav_graph_json = "{}"
        return self._nav_graph_json

    def _get_floor(self):
        """惰性创建边缘帧→楼层帧转换器（#24.1 §0）。"""
        if self._floor is None:
            from unilabos.sim.fleet.rmf.frame import FloorFrame

            self._floor = FloorFrame(self.generated_map_dir)
        return self._floor

    def _load_nav_graph(self) -> str:
        """读 `nav_graphs/0.yaml` → 顶点/lane 转楼层帧 mm → 前端 `{waypoints, lanes}` JSON（#24.1 §1）。"""
        import yaml

        nav_path = os.path.join(self.generated_map_dir, "nav_graphs", "0.yaml")
        if not os.path.exists(nav_path):
            self._nav_graph_json = "{}"
            return self._nav_graph_json
        g = yaml.safe_load(open(nav_path, encoding="utf-8")) or {}
        levels = g.get("levels") or {}
        if not levels:
            self._nav_graph_json = "{}"
            return self._nav_graph_json
        level_name = next(iter(levels))
        lvl = levels[level_name] or {}
        ff = self._get_floor()
        charger = str(self.config.get("charger_waypoint") or "")

        waypoints: List[Dict[str, Any]] = []
        vid: List[str] = []
        for i, v in enumerate(lvl.get("vertices") or []):
            meta = v[2] if len(v) > 2 and isinstance(v[2], dict) else {}
            name = str(meta.get("name") or "")
            wid = name or f"v{i}"
            vid.append(wid)
            fx, fy = ff.edge_to_floor_mm(float(v[0]), float(v[1]))
            waypoints.append(
                {
                    "id": wid,
                    "levelId": level_name,
                    "name": name or wid,
                    "position": {"x": round(fx, 1), "y": round(fy, 1)},
                    "yaw": 0.0,
                    "type": (("charger" if name == charger else "dock") if name else "generic"),
                    "isHoldingPoint": bool(meta.get("is_holding_point")),
                    "pickupDispenser": meta.get("pickup_dispenser"),
                    "dropoffIngestor": meta.get("dropoff_ingestor"),
                }
            )

        lanes: List[Dict[str, Any]] = []
        for j, ln in enumerate(lvl.get("lanes") or []):
            if not isinstance(ln, list) or len(ln) < 2:
                continue
            opt = ln[2] if len(ln) > 2 and isinstance(ln[2], dict) else {}
            try:
                a, b = vid[int(ln[0])], vid[int(ln[1])]
            except (IndexError, ValueError):
                continue
            lanes.append(
                {
                    "id": f"l_{j}",
                    "levelId": level_name,
                    "startWaypointId": a,
                    "endWaypointId": b,
                    "bidirectional": False,
                    "speedLimit": float(opt.get("speed_limit", 0.0) or 0.0),
                }
            )

        self._nav_graph_json = json.dumps({"waypoints": waypoints, "lanes": lanes}, ensure_ascii=False)
        logger.info(
            f"[rmf] nav_graph 已载入：{len(waypoints)} waypoints + {len(lanes)} lanes（楼层帧 mm → cloud，#24.1）"
        )
        return self._nav_graph_json

    # ============================================================ live source
    def get_live_source(self):
        """返回供 QueryEngine 注册的 RmfLiveSource（main_slave_run 图驱动接线）。"""
        if self._live_source is None:
            from unilabos.queries.rmf_live_source import RmfLiveSource

            self._live_source = RmfLiveSource()
        return self._live_source

    # ============================================================ 车队主控制层（OS 接 RMF 指令 → 驱动小车）
    def _start_fleet_manager(self) -> None:
        """启动 OS 车队主：监听 RMF fleet_adapter 指令并驱动 mock AGV 硬件（#18 §10.4）。

        config（来自 graph 节点 config）：
          - fleet_manager_port：= fleet_config.rmf_fleet.fleet_manager.port（默认 22011）
          - edge_url：mock AGV 硬件 HTTP 地址（默认 http://127.0.0.1:8090）
          - robots：机器人名列表（默认 [fleet 的单车 unilab_agv1]）
          - fleet_manager_host / nominal_velocity：可选
        config.enable_fleet_manager=false 可关闭（纯调度、不接管小车）。
        """
        if str(self.config.get("enable_fleet_manager", True)).lower() in ("0", "false", "no"):
            return
        if self._fleet_manager is not None:
            return
        try:
            from unilabos.sim.fleet.rmf.edge.fleet_manager_http import EdgeFleetManager
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] 车队主控制层不可用（导入失败）: {e}")
            return

        host = str(self.config.get("fleet_manager_host") or "127.0.0.1")
        port = int(self.config.get("fleet_manager_port") or 22011)
        edge_url = str(self.config.get("edge_url") or os.environ.get("RMF_EDGE_URL") or "http://127.0.0.1:8090")
        robots = self.config.get("robots") or [self.config.get("robot_name") or "unilab_agv1"]
        if isinstance(robots, str):
            robots = [robots]
        nominal_v = float(self.config.get("nominal_velocity") or 0.5)

        def _os_log(msg: str, level: str = "info") -> None:
            getattr(logger, level, logger.info)(msg)

        try:
            self._fleet_manager = EdgeFleetManager(
                edge_url=edge_url,
                robot_names=list(robots),
                nominal_velocity=nominal_v,
                log=_os_log,
            )
            self._fleet_manager.start(host, port)
            logger.info(
                f"[rmf] OS 车队主上线：RMF 指令 → 本 OS（{host}:{port}）→ 驱动小车 {edge_url}；"
                f"robots={list(robots)}（OS 即 fleet owner，#18 §10.4）"
            )
        except OSError as e:
            logger.warning(
                f"[rmf] 车队主监听 {host}:{port} 失败（端口被占？是否已有 fleet_manager 在跑）: {e}"
            )
            self._fleet_manager = None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] 车队主启动失败: {e}")
            self._fleet_manager = None

    def _designer_transfer_state_path(self) -> str:
        path = str(self.config.get("designer_transfers_state_path") or "").strip()
        if path:
            return path
        return os.path.join(self.generated_map_dir, "designer_transfers_run_state.json")

    def _load_designer_transfer_run_state(self) -> None:
        path = self._designer_transfer_state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                self._designer_transfer_run = payload
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] 读取 designer_transfers 状态失败: {e}")

    def _persist_designer_transfer_run_locked(self) -> None:
        path = self._designer_transfer_state_path()
        try:
            dir_path = os.path.dirname(path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._designer_transfer_run, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] 持久化 designer_transfers 状态失败: {e}")

    def _resolve_transfers_path(self, transfers_path: str) -> str:
        explicit = str(transfers_path or "").strip()
        candidates: List[str] = []
        if explicit:
            candidates.append(explicit)
        cfg_path = str(self.config.get("designer_transfers_path") or "").strip()
        if cfg_path:
            candidates.append(cfg_path)
        lo_dir = str(self.config.get("layout_optimizer_dir") or "").strip()
        if lo_dir:
            candidates.append(os.path.join(lo_dir, "transfers.json"))
        env_path = str(os.environ.get("UNILAB_DESIGNER_TRANSFERS_PATH") or "").strip()
        if env_path:
            candidates.append(env_path)
        for p in candidates:
            if p and os.path.exists(p):
                return p
        return explicit

    def _resolve_routes_path(self, routes_path: str) -> str:
        explicit = str(routes_path or "").strip()
        candidates: List[str] = []
        if explicit:
            candidates.append(explicit)
        cfg_path = str(self.config.get("agv_routes_path") or "").strip()
        if cfg_path:
            candidates.append(cfg_path)
        candidates.append(os.path.join(self.generated_map_dir, "rmf_agv_routes.json"))
        default_latest = os.path.join(
            os.path.dirname(self.generated_map_dir),
            "maps",
            "latest",
            "rmf_agv_routes.json",
        )
        candidates.append(default_latest)
        for p in candidates:
            if p and os.path.exists(p):
                return p
        return explicit

    def _build_designer_transfer_plan(
        self,
        *,
        transfers_path: str,
        routes_path: str,
        start_index: int,
        max_count: int,
    ) -> Dict[str, Any]:
        if not os.path.exists(transfers_path):
            raise ValueError(f"transfers_path not found: {transfers_path}")
        if not os.path.exists(routes_path):
            raise ValueError(f"routes_path not found: {routes_path}")

        with open(transfers_path, encoding="utf-8") as f:
            transfers_payload = json.load(f)
        if not isinstance(transfers_payload, dict):
            raise ValueError("transfers json must be an object with key 'transfers'")
        transfers = transfers_payload.get("transfers")
        if not isinstance(transfers, list):
            raise ValueError("transfers json missing 'transfers' array")
        source_total = len(transfers)
        start = max(0, min(int(start_index), source_total))
        limit_count = max(0, int(max_count))
        end_index_exclusive = source_total if limit_count <= 0 else min(source_total, start + limit_count)

        dock_map = self._build_instance_dock_map(routes_path)
        entries: List[Dict[str, Any]] = []
        missing: List[Dict[str, Any]] = []
        for idx in range(start, end_index_exclusive):
            item = transfers[idx] if isinstance(transfers[idx], dict) else {}
            from_device = str(item.get("from_device") or "").strip()
            to_device = str(item.get("to_device") or "").strip()
            src = dock_map.get(from_device)
            dst = dock_map.get(to_device)
            if src is None or dst is None:
                missing.append(
                    {
                        "index": idx,
                        "from_device": from_device,
                        "to_device": to_device,
                        "missing_from": src is None,
                        "missing_to": dst is None,
                    }
                )
                continue
            sample_id = str(item.get("sample_id") or "")
            payload = [{"sku": sample_id or "sample", "quantity": 1}]
            entries.append(
                {
                    "index": idx,
                    "pickup": str(src["waypoint"]),
                    "dropoff": str(dst["waypoint"]),
                    "pickup_handler": str(src["pickup_handler"]),
                    "dropoff_handler": str(dst["dropoff_handler"]),
                    "sample_id": sample_id,
                    "task_ref": str(item.get("task_id") or ""),
                    "ready_time": item.get("ready_time"),
                    "deadline": item.get("deadline"),
                    "priority": str(item.get("priority") or ""),
                    "from_device": from_device,
                    "to_device": to_device,
                    "payload": payload,
                }
            )
        if missing:
            snippet = json.dumps(missing[:5], ensure_ascii=False)
            raise ValueError(
                f"instanceId to dock mapping incomplete in routes ({len(missing)} rows), examples={snippet}"
            )

        stat = os.stat(transfers_path)
        digest_seed = f"{transfers_path}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
        preview = [
            {
                "index": e["index"],
                "pickup": e["pickup"],
                "dropoff": e["dropoff"],
                "sample_id": e["sample_id"],
            }
            for e in entries[:5]
        ]
        return {
            "source_total": source_total,
            "run_total": len(entries),
            "start_index": start,
            "end_index_exclusive": end_index_exclusive,
            "remaining": len(entries),
            "entries": entries,
            "preview": preview,
            "transfers_digest": hashlib.sha256(digest_seed).hexdigest()[:16],
        }

    def _build_instance_dock_map(self, routes_path: str) -> Dict[str, Dict[str, str]]:
        with open(routes_path, encoding="utf-8") as f:
            routes_payload = json.load(f)
        waypoints = routes_payload.get("waypoints") if isinstance(routes_payload, dict) else None
        if not isinstance(waypoints, list):
            raise ValueError("routes json missing 'waypoints' array")
        mapping: Dict[str, Dict[str, str]] = {}
        for wp in waypoints:
            if not isinstance(wp, dict):
                continue
            instance_id = str(wp.get("instanceId") or "").strip()
            waypoint = str(wp.get("name") or "").strip()
            if not instance_id or not waypoint:
                continue
            pickup_handler = str(wp.get("pickupDispenser") or f"d_{waypoint}")
            dropoff_handler = str(wp.get("dropoffIngestor") or f"i_{waypoint}")
            if instance_id not in mapping:
                mapping[instance_id] = {
                    "waypoint": waypoint,
                    "pickup_handler": pickup_handler,
                    "dropoff_handler": dropoff_handler,
                }
        if not mapping:
            raise ValueError("no instanceId->dock mapping found in routes json")
        return mapping

    def _designer_transfer_worker(
        self,
        run_id: str,
        entries: List[Dict[str, Any]],
        on_error: str,
        retry_max: int,
        wait_timeout_s: float,
    ) -> None:
        try:
            for entry in entries:
                with self._designer_transfer_lock:
                    if str(self._designer_transfer_run.get("run_id") or "") != run_id:
                        return
                    if self._designer_transfer_cancel.is_set():
                        self._designer_transfer_run["status"] = "canceled"
                        end_index = int(self._designer_transfer_run.get("end_index_exclusive", 0))
                        cursor = int(self._designer_transfer_run.get("cursor", 0))
                        self._designer_transfer_run["remaining"] = max(0, end_index - cursor)
                        self._designer_transfer_run["updated_at_ms"] = int(time.time() * 1000)
                        self._designer_transfer_run["finished_at_ms"] = int(time.time() * 1000)
                        self._persist_designer_transfer_run_locked()
                        return

                index = int(entry["index"])
                transfer_ok = False
                last_error = ""
                for attempt in range(retry_max + 1):
                    res = self.dispatch_delivery(
                        pickup=str(entry["pickup"]),
                        dropoff=str(entry["dropoff"]),
                        pickup_handler=str(entry["pickup_handler"]),
                        dropoff_handler=str(entry["dropoff_handler"]),
                        payload=list(entry.get("payload") or []),
                    )
                    if not bool(res.get("success", False)):
                        last_error = f"dispatch failed: {res.get('error') or 'unknown'}"
                        logger.warning(f"[rmf] designer transfer index={index} attempt={attempt + 1} {last_error}")
                        continue

                    rmf_task_id = str(res.get("rmf_task_id") or res.get("task_id") or "")
                    with self._designer_transfer_lock:
                        if str(self._designer_transfer_run.get("run_id") or "") != run_id:
                            return
                        self._designer_transfer_run["dispatched"] = int(self._designer_transfer_run.get("dispatched", 0)) + 1
                        self._designer_transfer_run["last_task_id"] = rmf_task_id
                        self._designer_transfer_run["updated_at_ms"] = int(time.time() * 1000)
                        self._persist_designer_transfer_run_locked()

                    terminal = self._wait_task_terminal(rmf_task_id, timeout_s=wait_timeout_s)
                    if bool(terminal.get("success", False)):
                        transfer_ok = True
                        break
                    last_error = str(
                        terminal.get("error")
                        or f"task {rmf_task_id} ended with {terminal.get('status') or 'unknown'}"
                    )
                    logger.warning(
                        f"[rmf] designer transfer index={index} attempt={attempt + 1} "
                        f"task={rmf_task_id} failed: {last_error}"
                    )

                with self._designer_transfer_lock:
                    if str(self._designer_transfer_run.get("run_id") or "") != run_id:
                        return
                    if transfer_ok:
                        self._designer_transfer_run["completed"] = int(self._designer_transfer_run.get("completed", 0)) + 1
                        self._designer_transfer_run["cursor"] = index + 1
                        end_index = int(self._designer_transfer_run.get("end_index_exclusive", 0))
                        self._designer_transfer_run["remaining"] = max(0, end_index - (index + 1))
                        self._designer_transfer_run["last_error"] = ""
                        self._designer_transfer_run["last_error_index"] = -1
                        self._designer_transfer_run["updated_at_ms"] = int(time.time() * 1000)
                        self._persist_designer_transfer_run_locked()
                        continue

                    self._designer_transfer_run["failed"] = int(self._designer_transfer_run.get("failed", 0)) + 1
                    self._designer_transfer_run["last_error"] = last_error
                    self._designer_transfer_run["last_error_index"] = index
                    self._designer_transfer_run["updated_at_ms"] = int(time.time() * 1000)

                    if on_error == "skip":
                        self._designer_transfer_run["skipped"] = int(self._designer_transfer_run.get("skipped", 0)) + 1
                        self._designer_transfer_run["cursor"] = index + 1
                        end_index = int(self._designer_transfer_run.get("end_index_exclusive", 0))
                        self._designer_transfer_run["remaining"] = max(0, end_index - (index + 1))
                        self._persist_designer_transfer_run_locked()
                        continue

                    self._designer_transfer_run["status"] = "failed"
                    end_index = int(self._designer_transfer_run.get("end_index_exclusive", 0))
                    cursor = int(self._designer_transfer_run.get("cursor", index))
                    self._designer_transfer_run["remaining"] = max(0, end_index - cursor)
                    self._designer_transfer_run["finished_at_ms"] = int(time.time() * 1000)
                    self._persist_designer_transfer_run_locked()
                    return

            with self._designer_transfer_lock:
                if str(self._designer_transfer_run.get("run_id") or "") == run_id:
                    if self._designer_transfer_cancel.is_set():
                        self._designer_transfer_run["status"] = "canceled"
                    else:
                        self._designer_transfer_run["status"] = "completed"
                    end_index = int(self._designer_transfer_run.get("end_index_exclusive", 0))
                    cursor = int(self._designer_transfer_run.get("cursor", end_index))
                    self._designer_transfer_run["remaining"] = max(0, end_index - cursor)
                    self._designer_transfer_run["finished_at_ms"] = int(time.time() * 1000)
                    self._designer_transfer_run["updated_at_ms"] = int(time.time() * 1000)
                    self._persist_designer_transfer_run_locked()
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[rmf] designer transfers worker crashed: {e}")
            with self._designer_transfer_lock:
                if str(self._designer_transfer_run.get("run_id") or "") == run_id:
                    self._designer_transfer_run["status"] = "error"
                    self._designer_transfer_run["last_error"] = str(e)
                    self._designer_transfer_run["updated_at_ms"] = int(time.time() * 1000)
                    self._designer_transfer_run["finished_at_ms"] = int(time.time() * 1000)
                    self._persist_designer_transfer_run_locked()
        finally:
            with self._designer_transfer_lock:
                self._designer_transfer_thread = None
                self._designer_transfer_cancel.clear()

    def _rest_get(self, path: str, timeout: float = 15.0) -> Any:
        import requests

        api = str(self.config.get("api_url") or os.environ.get("RMF_API_URL") or "http://localhost:8000").rstrip("/")
        token = str(self.config.get("api_token") or _DEFAULT_API_JWT)
        resp = requests.get(
            f"{api}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GET {path} -> HTTP {resp.status_code}")
        return resp.json() if resp.content else {}

    def _query_task_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        tid = str(task_id or "").strip()
        if not tid:
            return None
        try:
            got = self._rest_get(f"/tasks/{tid}", timeout=8.0)
            if isinstance(got, dict):
                booking = got.get("booking")
                if isinstance(booking, dict) and str(booking.get("id") or "") == tid:
                    return got
        except Exception:
            pass
        try:
            limit = max(50, int(self.config.get("designer_task_query_limit") or 500))
            rows = self._rest_get(f"/tasks?limit={limit}", timeout=10.0)
            if isinstance(rows, dict):
                rows = rows.get("tasks") or []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    booking = row.get("booking") or {}
                    if str(booking.get("id") or "") == tid:
                        return row
        except Exception:
            return None
        return None

    def _wait_task_terminal(self, task_id: str, timeout_s: float = 1800.0, poll_interval_s: float = 2.0) -> Dict[str, Any]:
        tid = str(task_id or "").strip()
        if not tid:
            return {"success": False, "status": "invalid", "error": "empty task_id"}
        deadline = time.time() + max(1.0, float(timeout_s))
        last_status = "pending"
        terminals = {"completed", "done", "failed", "canceled", "cancelled", "aborted", "killed", "terminated"}
        while time.time() < deadline:
            row = self._query_task_by_id(tid)
            if isinstance(row, dict):
                status = str(row.get("status") or "").strip().lower()
                if status:
                    last_status = status
                if status in terminals:
                    if status in ("completed", "done"):
                        return {"success": True, "status": status, "task_id": tid, "task": row}
                    return {
                        "success": False,
                        "status": status,
                        "task_id": tid,
                        "task": row,
                        "error": f"task {tid} ended with status={status}",
                    }
            time.sleep(max(0.3, float(poll_interval_s)))
        return {
            "success": False,
            "status": "timeout",
            "task_id": tid,
            "last_status": last_status,
            "error": f"wait task timeout after {timeout_s:.1f}s",
        }

    # ============================================================ internals
    def _on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """event_collector 回调：更新本地缓存 + live source + 批量上报。"""
        if event_type == "robot_state":
            self._robot_states[payload.get("robotId", "")] = payload
            if self._live_source is not None:
                pose = payload.get("pose", {})
                self._live_source.update_pose(
                    payload.get("robotId", ""),
                    [pose.get("x", 0.0), pose.get("y", 0.0), 0.0],
                    metadata={"yaw": pose.get("yaw", 0.0), "fleet": payload.get("fleetName", "")},
                    source="rmf",
                )
        elif event_type == "task_state":
            self._task_states[payload.get("taskId", "")] = payload
        if self._reporter is not None:
            self._reporter.enqueue(event_type, payload)
        self._refresh_data()

    def _rest_publish(self, json_msg: str, request_id: str) -> None:
        """把任务信封经 REST 下发到本地 rmf-web api-server（#22 §3.2）——避开 rmf_task_msgs ROS ABI。

        dispatch_task_request → POST /tasks/dispatch_task；robot_task_request → POST /tasks/robot_task。
        """
        import requests

        api = str(self.config.get("api_url") or os.environ.get("RMF_API_URL") or "http://localhost:8000").rstrip("/")
        token = str(self.config.get("api_token") or _DEFAULT_API_JWT)
        envelope = json.loads(json_msg)
        path = "/tasks/robot_task" if envelope.get("type") == "robot_task_request" else "/tasks/dispatch_task"
        resp = requests.post(
            f"{api}{path}",
            data=json_msg,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            timeout=15,
        )
        body = resp.json() if resp.content else {}
        tid = ""
        if isinstance(body, dict):
            tid = str(((body.get("state") or {}).get("booking") or {}).get("id") or "")
        logger.info(f"[rmf] OS action 下发 → RMF task_id={tid} (HTTP {resp.status_code}) via {api}{path} req={request_id}")
        if resp.status_code != 200:
            raise RuntimeError(f"dispatch HTTP {resp.status_code}: {str(body)[:200]}")
        with self._dispatch_ack_lock:
            self._dispatch_ack[request_id] = tid

    def _dispatch(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if self._dispatcher is None:
                from unilabos.sim.fleet.rmf.task_dispatcher import RmfTaskDispatcher

                # OS action → RMF：经本地 api-server REST 真正下发（非 log-only，#22 §3.2-a）
                self._dispatcher = RmfTaskDispatcher(publish_fn=self._rest_publish)
            rid = self._dispatcher.dispatch(envelope)
            booking_id = ""
            with self._dispatch_ack_lock:
                booking_id = str(self._dispatch_ack.pop(rid, "") or "")
            if booking_id:
                return {"success": True, "task_id": booking_id, "rmf_task_id": booking_id, "request_id": rid}
            return {"success": True, "task_id": rid, "request_id": rid}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] dispatch 失败: {e}")
            return {"success": False, "task_id": "", "error": str(e)}

    def _ensure_gateway(self) -> None:
        if self._gateway is None:
            from unilabos.sim.fleet.rmf.gateway import RmfGateway, RmfGatewayConfig

            self._gateway = RmfGateway(RmfGatewayConfig(generated_map_dir=self.generated_map_dir))

    def _ensure_reporter(self, mode: str) -> str:
        from unilabos.sim.fleet.rmf.backend_reporter import BackendReporter

        if self._reporter is None:
            self._reporter = BackendReporter(self.lab_uuid)
        self._reporter.set_consistency(scene_hash=self._scene_hash, rmf_artifact_id=self._artifact_id)
        session_id = self._reporter.register_session(
            {"mode": mode, "scene_hash": self._scene_hash, "rmf_artifact_uuid": self._artifact_id}
        ) or f"sess-{uuid.uuid4().hex[:12]}"
        self._reporter.runtime_session_id = session_id
        self._reporter.start()
        return session_id

    def _fetch_published_scene(self) -> Optional[Dict[str, Any]]:
        """从后端拉取发布版 scene（惰性、可失败）。"""
        remote = self.config.get("remote_addr")
        if not remote or not self.lab_uuid:
            return None
        try:
            import requests

            from unilabos.config.config import BasicConfig

            headers = {"Authorization": f"Lab {BasicConfig.auth_secret()}"}
            url = f"{remote}/laboratories/{self.lab_uuid}/floorplan/published"
            resp = requests.get(url, headers=headers, timeout=5)
            return resp.json() if resp.content else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] 拉取发布版 scene 失败: {e}")
            return None

    def _write_artifacts(self, building: Dict[str, Any], semantic: Dict[str, Any]) -> str:
        try:
            import yaml

            os.makedirs(self.generated_map_dir, exist_ok=True)
            base = self.lab_uuid or "lab"
            with open(os.path.join(self.generated_map_dir, f"{base}.building.yaml"), "w", encoding="utf-8") as f:
                yaml.safe_dump(building, f, sort_keys=True, allow_unicode=True)
            with open(os.path.join(self.generated_map_dir, "semantic_map.json"), "w", encoding="utf-8") as f:
                json.dump(semantic, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rmf] 写编译产物失败: {e}")
        return self.generated_map_dir

    def _runtime_clock_status(self) -> Dict[str, Any]:
        mode = self._detect_runtime_mode()
        supports = self._detect_supports_sim_clock(mode)
        try:
            from unilabos.sim.context import get_runtime_context

            clock = get_runtime_context().clock
            rate = float(getattr(clock, "scale", 1.0))
            paused = bool(getattr(clock, "paused", False))
            sim_now = float(clock.now()) if hasattr(clock, "now") else 0.0
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "message": f"runtime context unavailable: {e}",
                "mode": mode,
                "rate": self._sim_rate,
                "paused": self._sim_paused,
                "sim_now": 0.0,
            }

        if mode not in ("sim", "twin"):
            return {
                "success": False,
                "message": f"sim clock is locked in mode={mode}",
                "mode": mode,
                "rate": rate,
                "paused": paused,
                "sim_now": sim_now,
            }
        if not supports:
            return {
                "success": False,
                "message": "sim clock service disabled by runtime capability",
                "mode": mode,
                "rate": rate,
                "paused": paused,
                "sim_now": sim_now,
            }
        return {
            "success": True,
            "message": "ok",
            "mode": mode,
            "rate": rate,
            "paused": paused,
            "sim_now": sim_now,
        }

    def _refresh_data(self) -> None:
        self._runtime_mode = self._detect_runtime_mode()
        self._supports_sim_clock = self._detect_supports_sim_clock(self._runtime_mode)
        self._supports_robot_speed_control = self._detect_supports_robot_speed_control()
        clock_status = self._runtime_clock_status()
        self._sim_rate = float(clock_status.get("rate", 1.0))
        self._sim_paused = bool(clock_status.get("paused", False))
        with self._designer_transfer_lock:
            run_state = dict(self._designer_transfer_run)
        self.data.update(
            {
                "runtime_status": self._runtime_status,
                "runtime_mode": self._runtime_mode,
                "supports_sim_clock": self._supports_sim_clock,
                "supports_robot_speed_control": self._supports_robot_speed_control,
                "sim_rate": self._sim_rate,
                "sim_paused": self._sim_paused,
                "scene_hash": self._scene_hash,
                "map_version": self._map_version,
                "designer_transfers_run": json.dumps(run_state, ensure_ascii=False),
            }
        )

    @staticmethod
    def _parse_bool(raw: Any) -> Optional[bool]:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("1", "true", "yes", "y", "on"):
                return True
            if s in ("0", "false", "no", "n", "off"):
                return False
        return None

    def _detect_runtime_mode(self) -> str:
        # 0) 环境变量强制覆盖（联调兜底）
        env_mode = str(os.environ.get("UNILAB_FORCE_RUNTIME_MODE") or "").strip().lower()
        if env_mode in ("real", "sim", "twin"):
            return env_mode
        # 1) 优先读取进程启动参数 --mode（run_edge.sh / 手工命令）
        try:
            import sys

            argv = [str(a).strip().lower() for a in getattr(sys, "argv", [])]
            if "--mode" in argv:
                i = argv.index("--mode")
                if i + 1 < len(argv):
                    arg_mode = argv[i + 1]
                    if arg_mode in ("real", "sim", "twin"):
                        return arg_mode
        except Exception:  # noqa: BLE001
            pass
        # 2) 其次按 RuntimeContext 实际模式上报
        try:
            from unilabos.sim.context import get_runtime_context

            mode = str(get_runtime_context().mode).strip().lower()
            if mode in ("real", "sim", "twin"):
                return mode
        except Exception:  # noqa: BLE001
            pass
        # 3) 最后才看图配置（便于联调时显式覆盖）
        cfg_mode = str(self.config.get("runtime_mode") or "").strip().lower()
        if cfg_mode in ("real", "sim", "twin"):
            return cfg_mode
        # 4) 兜底
        return "real"

    def _detect_supports_sim_clock(self, runtime_mode: str) -> bool:
        # 环境变量强制覆盖（联调兜底）
        env_override = self._parse_bool(os.environ.get("UNILAB_FORCE_SUPPORTS_SIM_CLOCK"))
        if env_override is not None:
            return env_override
        # real 模式下 sim clock 控制应视为不可用（避免 cloud 暴露无效按钮）
        if runtime_mode not in ("sim", "twin"):
            return False
        # 允许图配置显式覆盖
        cfg_override = self._parse_bool(self.config.get("supports_sim_clock"))
        if cfg_override is not None:
            return cfg_override
        try:
            from unilabos.sim.context import get_runtime_context

            ctx = get_runtime_context()
            return bool(getattr(ctx, "sim_services_enabled", False))
        except Exception:  # noqa: BLE001
            return False

    def _detect_supports_robot_speed_control(self) -> bool:
        # 当前版本默认不宣称支持“运行中调速”（待 set_robot_speed action 落地后再切 true）
        cfg_override = self._parse_bool(self.config.get("supports_robot_speed_control"))
        if cfg_override is not None:
            return cfg_override
        return False
