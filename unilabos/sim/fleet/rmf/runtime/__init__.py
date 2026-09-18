"""RMF 运行时统一入口（OS-first + standalone）。

本包提供：
- 启动策略解析（policy）
- 统一起停能力（launcher）
- 命令行入口（cli）
"""

from __future__ import annotations

from unilabos.sim.fleet.rmf.runtime.launcher import RmfRuntimeLauncher, RmfRuntimeOptions
from unilabos.sim.fleet.rmf.runtime.policy import RmfStartupPolicy, build_startup_policy

__all__ = [
    "RmfRuntimeLauncher",
    "RmfRuntimeOptions",
    "RmfStartupPolicy",
    "build_startup_policy",
]

