"""`agv.RMFSim`：RMF 仿真 AGV 虚拟驱动（#17 §7.2 / #18 §6.2）。

不连接真实硬件，pose/status 来自 RMF fake/sim fleet（由 `rmf.coordinator` 的
event_collector 喂入 `update_from_rmf`）。可用于 CI / 浏览器 e2e / 无硬件演示。

注册方式：plain class + `registry/devices/robot_rmf.yaml`（与 `agv.SEER` 一致）。
simulation_meta 自标记为 AGV 调度域虚拟驱动——当前 `device_simulation_meta` 仅支持
`sim_engine` 字段，故暂存 `sim_engine="rmf"`，但其语义是 **fleet_engine**（仅对 AGV/fleet
设备生效，不作为全局 `--sim_engine` 解释，见 #17 §2.4-(5)）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# 自标记（供 Device Square / 本地配对识别）。注意 sim_engine 在此语义为 fleet_engine。
SIMULATION_META: Dict[str, Any] = {
    "driver_runtime_kind": "virtual",
    "virtual_driver_kind": "engine_adapter",
    "sim_engine": "rmf",
}


class RMFSimAgv:
    def __init__(
        self,
        robot_name: str = "agv_sim_01",
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
        self.config = config or {}
        self.data: Dict[str, Any] = {}
        self._pose: List[float] = [0.0, 0.0, 0.0]  # [x_m, y_m, yaw_rad]
        self._status: str = "idle"
        self._battery: float = 1.0
        self._task_id: str = ""

    async def initialize(self) -> bool:
        self.data.update({"pose": self._pose, "status": self._status})
        return True

    async def cleanup(self) -> bool:
        return True

    # ---------------------------------------------------------------- status
    @property
    def pose(self) -> List[float]:
        """[x_m, y_m, yaw_rad]；经 push_joint_state 高频上报（#18 §4.4），不走标量 device_status。"""
        return self._pose

    @property
    def status(self) -> str:
        """idle / moving / charging / error 等（标量，可走 device_status）。"""
        return self._status

    @property
    def battery(self) -> float:
        return self._battery

    @property
    def task_id(self) -> str:
        return self._task_id

    # ---------------------------------------------------- RMF → 驱动状态注入
    def update_from_rmf(
        self,
        x_m: float,
        y_m: float,
        yaw_rad: float,
        status: str = "idle",
        battery: Optional[float] = None,
        task_id: str = "",
    ) -> None:
        """由 coordinator 的 event_collector 用归一化后的 RobotState 更新本地缓存。"""
        self._pose = [float(x_m), float(y_m), float(yaw_rad)]
        self._status = status
        if battery is not None:
            self._battery = float(battery)
        self._task_id = task_id
        self.data.update({"pose": self._pose, "status": self._status})
