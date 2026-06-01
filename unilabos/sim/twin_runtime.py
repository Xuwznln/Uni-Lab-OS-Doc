"""Runtime driver for digital-twin bridges.

`TwinBridge` only knows how to mirror one real->virtual observation when asked
(`poll_once`). Nothing drives that polling on its own, so in ``--mode twin`` the
twin would never actually update. This module wires a ROS2 timer that pumps every
active ``TwinDriverPair.bridge`` at a fixed rate; each bridge still self-throttles
via its own ``throttle_hz``.

The polling logic (`collect_twin_pairs` / `poll_twin_pairs`) is dependency-free so
it can be unit-tested without a ROS2 environment.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, List, Union

logger = logging.getLogger(__name__)

PairsProvider = Union[Iterable[Any], Callable[[], Iterable[Any]]]


def collect_twin_pairs(devices: Any) -> List[Any]:
    """Return device instances that carry a twin ``bridge`` (i.e. TwinDriverPair)."""
    if devices is None:
        return []
    items = devices.values() if hasattr(devices, "values") else devices
    return [d for d in items if getattr(d, "bridge", None) is not None]


def poll_twin_pairs(pairs: Iterable[Any]) -> int:
    """Pump each pair's bridge once. Returns the number of bridges that updated."""
    updated = 0
    for pair in pairs:
        bridge = getattr(pair, "bridge", None)
        if bridge is None:
            continue
        try:
            if bridge.poll_once():
                updated += 1
        except Exception as exc:  # noqa: BLE001 - one bad device must not kill the loop
            logger.warning("twin poll failed for %s: %s", getattr(pair, "node_id", pair), exc)
    return updated


def _resolve(provider: PairsProvider) -> List[Any]:
    if callable(provider):
        try:
            return list(provider() or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("twin pairs provider failed: %s", exc)
            return []
    return list(provider or [])


class TwinPollerNode:
    """ROS2 node that periodically pumps all twin bridges.

    ``pairs`` may be a static iterable or a zero-arg callable that returns the
    current pairs (so devices created after start are picked up). rclpy is imported
    lazily so importing this module never requires a ROS2 environment.
    """

    def __init__(self, pairs: PairsProvider, poll_rate_hz: float = 50.0, auto_start: bool = True):
        self.pairs = pairs
        self.poll_rate_hz = float(poll_rate_hz) if poll_rate_hz and poll_rate_hz > 0 else 50.0
        self.node = None
        self.timer = None
        self.ticks = 0
        if auto_start:
            self.start()

    def start(self) -> None:
        if self.node is not None:
            return
        import rclpy
        from rclpy.node import Node

        if not rclpy.ok():
            rclpy.init(args=None)
        self.node = Node("sim_twin_poller")
        self.timer = self.node.create_timer(1.0 / self.poll_rate_hz, self.tick)

    def tick(self) -> int:
        self.ticks += 1
        return poll_twin_pairs(_resolve(self.pairs))

    def shutdown(self) -> None:
        if self.node is None:
            return
        if self.timer is not None:
            self.node.destroy_timer(self.timer)
        self.node.destroy_node()
        self.node = None
        self.timer = None
