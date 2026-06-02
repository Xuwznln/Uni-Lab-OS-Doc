from __future__ import annotations

from dataclasses import dataclass

from unilabos.sim.clock import SimClock
from unilabos.sim.clock_control import SimClockControlNode
from unilabos.sim.clock_publisher import SimClockPublisher
from unilabos.sim.context import RuntimeContext, init_runtime_context


@dataclass
class RuntimeServices:
    context: RuntimeContext
    clock_publisher: SimClockPublisher | None = None
    clock_control: SimClockControlNode | None = None

    def shutdown(self) -> None:
        if self.clock_control is not None:
            self.clock_control.shutdown()
        if self.clock_publisher is not None:
            self.clock_publisher.shutdown()


def configure_runtime(
    mode: str = "real",
    sim_rate: float = 1.0,
    sim_paused: bool = False,
    start_ros_services: bool = False,
) -> RuntimeServices:
    clock = SimClock(mode=mode, scale=sim_rate)
    context = RuntimeContext(mode=mode, clock=clock, sim_paused=sim_paused)
    init_runtime_context(context)
    services = RuntimeServices(context=context)
    if start_ros_services and mode in ("sim", "twin"):
        services.clock_publisher = SimClockPublisher(context.clock)
        services.clock_control = SimClockControlNode(context.clock)
    return services
