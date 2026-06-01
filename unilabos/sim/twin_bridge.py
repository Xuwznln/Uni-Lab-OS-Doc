"""Single-direction real-to-virtual digital twin bridge."""

from __future__ import annotations

import time
from typing import Any, Iterable


class TwinBridge:
    def __init__(
        self,
        real_driver: Any,
        virtual_driver: Any,
        node_id: str,
        observed_fields: Iterable[str] | None = None,
        throttle_hz: float = 10.0,
    ):
        self.real_driver = real_driver
        self.virtual_driver = virtual_driver
        self.node_id = node_id
        self.observed_fields = list(observed_fields or [])
        self.throttle_hz = float(throttle_hz or 0)
        self._last_update = 0.0
        self.updates = 0

    def should_update(self) -> bool:
        if self.throttle_hz <= 0:
            return True
        return (time.monotonic() - self._last_update) >= (1.0 / self.throttle_hz)

    def update_from_observation(self, observation: dict[str, Any]) -> bool:
        if not self.should_update():
            return False
        if not hasattr(self.virtual_driver, "set_observed_state"):
            raise TypeError(f"virtual driver for {self.node_id} does not implement set_observed_state")
        fields = self.observed_fields or list(observation.keys())
        for field in fields:
            if field in observation:
                self.virtual_driver.set_observed_state(field, observation[field])
        self._last_update = time.monotonic()
        self.updates += 1
        return True

    def poll_once(self) -> bool:
        if hasattr(self.real_driver, "get_observation"):
            observation = self.real_driver.get_observation()
        else:
            observation = {field: getattr(self.real_driver, field) for field in self.observed_fields if hasattr(self.real_driver, field)}
        return self.update_from_observation(observation)

    def shutdown(self) -> None:
        pass
