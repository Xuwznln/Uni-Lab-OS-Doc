"""`agv.RMFSim`：OS 内部 mock 小车节点（#22 §0.1 / #18 §10.3）。

作为 OS graph 的**独立 root 节点**（不是 `rmf.coordinator` 的 children）：`__init__` 内启动 mock
`/agv/*` HTTP 服务(:`edge_port`) + 模拟运动；RMF 节点（`rmf.coordinator`）的 `fleet_manager`
经 `/agv/*` 驱动它。真车换 `agv.SEER_RMF`（同 `/agv/*` 契约，链路不变）。

注册：plain class + `registry/devices/robot_rmf.yaml`；sim 模式不被 stub 靠 `device_pair.yaml`
给 `agv.RMFSim` 注册自配对（`real == virtual`）。框架不调用 async `initialize()`，故 mock 服务
在 `__init__` 启动（同 `rmf.coordinator`）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from unilabos.utils.log import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("agv.RMFSim")

# 自标记（供 Device Square / 本地配对识别）。sim_engine 在此语义为 fleet_engine。
SIMULATION_META: Dict[str, Any] = {
    "driver_runtime_kind": "virtual",
    "virtual_driver_kind": "engine_adapter",
    "sim_engine": "rmf",
}


class RMFSimAgv:
    def __init__(
        self,
        robot_name: str = "unilab_agv1",
        fleet_name: str = "unilab_agv",
        footprint_radius: float = 0.35,
        charger_waypoint: str = "",
        initial_waypoint: str = "",
        device_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        self.device_id = device_id or robot_name
        self.robot_name = robot_name
        self.fleet_name = fleet_name
        self.footprint_radius = footprint_radius
        self.charger_waypoint = charger_waypoint
        self.initial_waypoint = initial_waypoint
        # graph 节点 config 的额外键经 **kwargs 传入，合并进 self.config（同 rmf.coordinator）
        self.config = {**(config or {}), **kwargs}
        self.data: Dict[str, Any] = {}

        # OS 内部 mock 小车：__init__ 内起 /agv/* HTTP 服务 + 模拟运动（框架不调用 async initialize()）
        self._hw = None  # MockAgvHardware
        self._server = None  # MockAgvServer
        self._start_mock_server()

    def _start_mock_server(self) -> None:
        """启动 mock 小车硬件 + `/agv/*` HTTP 服务（#22 §0.1）。`config.enable_mock=false` 可关。"""
        if str(self.config.get("enable_mock", True)).lower() in ("0", "false", "no"):
            return
        try:
            from unilabos.sim.fleet.rmf.edge.agv_http_server import MockAgvServer
            from unilabos.sim.fleet.rmf.edge.mock_agv import MockAgvHardware
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[agv] mock 服务不可用（导入失败）: {e}")
            return

        x = float(self.config.get("start_x", 0.0))
        y = float(self.config.get("start_y", 0.0))
        yaw = float(self.config.get("start_yaw", 0.0))
        speed = float(self.config.get("linear_speed", 0.5))
        host = str(self.config.get("edge_host") or "127.0.0.1")
        port = int(self.config.get("edge_port") or 8090)

        self._hw = MockAgvHardware(self.robot_name, x=x, y=y, yaw=yaw, linear_speed=speed)
        try:
            self._server = MockAgvServer([self._hw], log=lambda m: logger.info(m))
            self._server.start(host, port)
            logger.info(
                f"[agv] mock AGV 节点上线：/agv/* http://{host}:{port} "
                f"robot={self.robot_name}@({x},{y}) fleet={self.fleet_name}（OS 内部小车，#22 §0.1）"
            )
        except OSError as e:
            logger.warning(f"[agv] mock /agv/* 监听 {host}:{port} 失败（端口被占？）: {e}")
            self._server = None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[agv] mock 服务启动失败: {e}")
            self._server = None

    async def initialize(self) -> bool:
        self.data.update({"pose": self.pose, "status": self.status})
        return True

    async def cleanup(self) -> bool:
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        return True

    # ---------------------------------------------------------------- status
    @property
    def pose(self) -> List[float]:
        """[x_m, y_m, yaw_rad]，取自 mock 硬件实时位姿；经 push_joint_state 上报（#18 §4.4）。"""
        if self._hw is not None:
            st = self._hw.state()
            return [float(st["x"]), float(st["y"]), float(st["yaw"])]
        return [0.0, 0.0, 0.0]

    @property
    def status(self) -> str:
        """idle / moving 等（标量，可走 device_status）。"""
        return self._hw.status if self._hw is not None else "idle"

    @property
    def battery(self) -> float:
        return float(self._hw.battery) if self._hw is not None else 1.0

    @property
    def task_id(self) -> str:
        return ""

    # ---------------------------------------------------- 兼容旧接口（现由 mock 自模拟，保留空实现）
    def update_from_rmf(
        self,
        x_m: float,
        y_m: float,
        yaw_rad: float,
        status: str = "idle",
        battery: Optional[float] = None,
        task_id: str = "",
    ) -> None:
        """历史接口（旧设计由 coordinator event_collector 注入位姿）。

        新设计中 mock 小车经 `/agv/*` 收 `fleet_manager` 指令、自行模拟运动并上报，
        无需外部注入；保留为 no-op 以兼容旧调用。
        """
        return None
