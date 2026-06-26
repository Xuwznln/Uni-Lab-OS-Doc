"""`rmf.coordinator`：RMF 调度域的 OS device facade（#17 §7.1 / #18 §6.2-(3)）。

聚合编译器 + gateway + task_dispatcher + event_collector + backend_reporter，对外以
OS device action/status 暴露：compile_map / start_runtime / stop_runtime /
dispatch_go_to / dispatch_delivery / cancel_task / query_runtime。

RMF 启用入口：graph 中出现本设备即代表该实验室启用 RMF（图驱动，#17 §2.1）。
重运行时（ROS/进程）惰性接线；离线（无 RMF）时编译/信封组装仍可用，便于测试。
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("rmf.coordinator")


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

        # 组件（惰性）
        self._dispatcher = None
        self._gateway = None
        self._reporter = None
        self._live_source = None
        self._last_building: Optional[Dict[str, Any]] = None
        self._last_semantic: Optional[Dict[str, Any]] = None
        self._last_transfer_plan: Optional[Dict[str, Any]] = None

        # 车队主控制层（#18 §10.4）：OS 即 fleet owner，接收 RMF fleet_adapter 指令并驱动 AGV 硬件。
        # 框架不调用 async initialize()，故在 __init__（构造时必执行）直接启动。
        self._fleet_manager = None  # EdgeFleetManager
        self._start_fleet_manager()

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

    def dispatch_go_to(self, place: str = "", robot_name: str = "", orientation_deg: Optional[float] = None) -> Dict[str, Any]:
        from unilabos.sim.fleet.rmf.task_dispatcher import build_go_to_request

        envelope = build_go_to_request(
            place, orientation_deg, fleet=self.fleet_name if robot_name else None, robot=robot_name or None
        )
        return self._dispatch(envelope)

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

    def cancel_task(self, task_id: str = "") -> Dict[str, Any]:
        from unilabos.sim.fleet.rmf.task_dispatcher import build_cancel_request

        return self._dispatch(build_cancel_request(task_id))

    def query_runtime(self) -> Dict[str, Any]:
        return {
            "robot_states": self._current_robot_states(),
            "task_states": list(self._task_states.values()),
            "diagnostics": self._diagnostics,
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

    def _dispatch(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if self._dispatcher is None:
                from unilabos.sim.fleet.rmf.task_dispatcher import RmfTaskDispatcher

                # 未接线 ROS 时，用日志型 publish 兜底（离线/测试）
                self._dispatcher = RmfTaskDispatcher(publish_fn=lambda j, r: logger.info(f"[rmf] task_api_requests <- {r}: {j}"))
            rid = self._dispatcher.dispatch(envelope)
            return {"success": True, "task_id": rid}
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

    def _refresh_data(self) -> None:
        self.data.update(
            {
                "runtime_status": self._runtime_status,
                "scene_hash": self._scene_hash,
                "map_version": self._map_version,
            }
        )
