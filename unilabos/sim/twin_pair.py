"""Container for real and virtual drivers in twin mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TwinDriverPair:
    real: Any
    virtual: Any
    bridge: Any

    @property
    def ros_node_instance(self):
        return getattr(self.real, "ros_node_instance", self.real)

    @property
    def driver_instance(self):
        return getattr(self.real, "driver_instance", self.real)

    def shutdown(self) -> None:
        if hasattr(self.bridge, "shutdown"):
            self.bridge.shutdown()
