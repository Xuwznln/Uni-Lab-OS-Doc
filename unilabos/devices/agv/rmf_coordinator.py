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
        self.config = config or {}
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
        return True

    async def cleanup(self) -> bool:
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
    ) -> Dict[str, Any]:
        """编译发布版 scene → building.yaml + semantic_map.json + 诊断（#18 §4.1）。"""
        from unilabos.sim.fleet.rmf.compiler import compile_scene

        if scene is None:
            scene = self._fetch_published_scene()
        if scene is None:
            return {"success": False, "artifact_id": "", "diagnostics": [{"level": "error", "code": "no_scene", "message": "无法获取发布版 scene"}]}

        ir, building, semantic = compile_scene(
            scene, robots, lab_uuid=self.lab_uuid, scene_hash=scene_hash or self._scene_hash
        )
        self._last_building = building
        self._last_semantic = semantic
        self._scene_hash = scene_hash or self._scene_hash
        self._diagnostics = ir.diagnostics_as_dicts()
        self._artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        self._map_version = self._artifact_id

        artifact_dir = self._write_artifacts(building, semantic)
        success = not ir.has_errors()
        self._refresh_data()
        logger.info(f"[rmf] compile_map success={success} artifact={self._artifact_id} dir={artifact_dir}")
        return {"success": success, "artifact_id": self._artifact_id, "diagnostics": self._diagnostics}

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
            "robot_states": list(self._robot_states.values()),
            "task_states": list(self._task_states.values()),
            "diagnostics": self._diagnostics,
        }

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
        return json.dumps(list(self._robot_states.values()), ensure_ascii=False)

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
