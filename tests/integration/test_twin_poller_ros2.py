"""ROS2 integration test of the twin poller (①).

Verifies that TwinPollerNode's timer actually drives bridge.poll_once() when spun
on a real ROS2 executor. Skipped if rclpy is missing.
"""

import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")


class _CountingBridge:
    def __init__(self):
        self.calls = 0

    def poll_once(self):
        self.calls += 1
        return True


class _Pair:
    def __init__(self, node_id, bridge):
        self.node_id = node_id
        self.bridge = bridge


@pytest.mark.integration
def test_twin_poller_drives_poll_once(ros_context):
    from rclpy.executors import SingleThreadedExecutor

    from unilabos.sim.twin_runtime import TwinPollerNode

    bridge = _CountingBridge()
    pairs = [_Pair("dev1", bridge)]
    poller = TwinPollerNode(pairs=lambda: pairs, poll_rate_hz=50.0, auto_start=True)

    executor = SingleThreadedExecutor()
    executor.add_node(poller.node)
    stop = threading.Event()

    def _spin():
        while not stop.is_set():
            executor.spin_once(timeout_sec=0.02)

    spinner = threading.Thread(target=_spin, daemon=True)
    spinner.start()
    try:
        time.sleep(0.5)  # at 50Hz expect ~25 ticks
        assert bridge.calls >= 5, f"poll_once should have fired repeatedly, got {bridge.calls}"
    finally:
        stop.set()
        spinner.join(timeout=2.0)
        executor.shutdown()
        poller.shutdown()


@pytest.mark.integration
def test_twin_poller_picks_up_late_pairs(ros_context):
    from rclpy.executors import SingleThreadedExecutor

    from unilabos.sim.twin_runtime import TwinPollerNode

    store = []
    poller = TwinPollerNode(pairs=lambda: store, poll_rate_hz=50.0, auto_start=True)
    executor = SingleThreadedExecutor()
    executor.add_node(poller.node)
    stop = threading.Event()
    spinner = threading.Thread(
        target=lambda: [executor.spin_once(timeout_sec=0.02) for _ in iter(lambda: not stop.is_set(), False)],
        daemon=True,
    )
    spinner.start()
    try:
        time.sleep(0.2)
        bridge = _CountingBridge()
        store.append(_Pair("late", bridge))  # added after poller started
        time.sleep(0.3)
        assert bridge.calls >= 3, f"late-added pair should be polled, got {bridge.calls}"
    finally:
        stop.set()
        spinner.join(timeout=2.0)
        executor.shutdown()
        poller.shutdown()
