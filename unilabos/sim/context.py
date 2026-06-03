"""Process-wide simulation runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from unilabos.sim.clock import SimClock
from unilabos.sim.physics_backend import PhysicsBackend

RuntimeMode = Literal["real", "sim", "twin"]
MissingSimPolicy = Literal["stub", "skip", "fail"]


@dataclass
class RuntimeContext:
    mode: RuntimeMode = "real"
    clock: SimClock = field(default_factory=lambda: SimClock("real"))
    physics: Optional[PhysicsBackend] = None
    physics_backend_name: str = "none"
    physics_endpoint: Optional[str] = None
    physics_scene: Optional[str] = None
    physics_timeout: float = 120.0
    missing_sim_policy_default: MissingSimPolicy = "stub"
    sim_paused: bool = False
    sim_services_enabled: bool = True
    query_api_enabled: bool = True
    query_grpc_port: int = 50051
    query_labutopia_assets: Optional[str] = None
    query_labutopia_config: Optional[str] = None
    query_labutopia_usd: Optional[str] = None

    def __post_init__(self) -> None:
        if self.mode not in ("real", "sim", "twin"):
            raise ValueError(f"Unsupported runtime mode: {self.mode}")
        if self.clock.mode != self.mode:
            self.clock = SimClock(mode=self.mode, scale=self.clock.scale)
        if self.sim_paused:
            self.clock.pause()


_current: RuntimeContext | None = None


def init_runtime_context(ctx: RuntimeContext) -> None:
    global _current
    _current = ctx


def get_runtime_context() -> RuntimeContext:
    return _current if _current is not None else RuntimeContext()


def _reset_for_test() -> None:
    global _current
    _current = None
