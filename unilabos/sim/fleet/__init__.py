"""Fleet 调度域（fleet/task/traffic scheduling domains）。

与 `unilabos/sim/backends/`（PhysicsBackend：物理世界后端）正交：本目录承载
*图驱动* 的车队/任务/交通调度引擎接入（如 Open-RMF）。RMF 不是 PhysicsBackend，
也不是全局 `--sim_engine` 取值，而是由 graph 中 `rmf.coordinator` + AGV
`managed_by` 推导启用。详见 product_designs/.../17-plan-rmf-edge-integration.md §2.1
与 18-rmf-os-data-exchange-and-bridging.md §6。
"""
