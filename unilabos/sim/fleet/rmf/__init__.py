"""Open-RMF fleet 调度域接入（#17 §6.1 / #18 §6）。

RMF = 图驱动的 fleet/task/traffic 调度域（**非** PhysicsBackend，**非** 全局 sim_engine）。
本包提供：坐标换算、Pascal→building.yaml 编译器、task 信封下发、运行态采集归一化、
后端批量上报、RMF core 进程治理。设备 facade（rmf.coordinator / agv.RMFSim /
agv.SEER_RMF）在 `unilabos/devices/agv/` 中调用本包。
"""

from __future__ import annotations

from unilabos.sim.fleet.rmf.coordinate_transform import pascal_to_rmf, rmf_to_pascal
from unilabos.sim.fleet.rmf.task_dispatcher import (
    RmfTaskDispatcher,
    build_cancel_request,
    build_delivery_request,
    build_go_to_request,
    build_patrol_request,
)

__all__ = [
    "pascal_to_rmf",
    "rmf_to_pascal",
    "RmfTaskDispatcher",
    "build_go_to_request",
    "build_delivery_request",
    "build_patrol_request",
    "build_cancel_request",
]
