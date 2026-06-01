"""ROS /clock publisher for simulation and twin modes."""

from __future__ import annotations

from types import SimpleNamespace

from unilabos.sim.clock import SimClock


def build_clock_msg(seconds: float):
    sec = int(seconds)
    nanosec = int((seconds - sec) * 1_000_000_000)
    try:
        from rosgraph_msgs.msg import Clock

        msg = Clock()
        msg.clock.sec = sec
        msg.clock.nanosec = nanosec
        return msg
    except Exception:
        return SimpleNamespace(clock=SimpleNamespace(sec=sec, nanosec=nanosec))


class SimClockPublisher:
    def __init__(self, clock: SimClock, rate_hz: int = 100, auto_start: bool = True):
        self.clock = clock
        self.rate_hz = int(rate_hz)
        self.enabled = clock.mode in ("sim", "twin")
        self.node = None
        self.publisher = None
        self.timer = None
        if auto_start and self.enabled:
            self.start()

    def start(self) -> None:
        if not self.enabled or self.node is not None:
            return
        import rclpy
        from rclpy.node import Node
        from rosgraph_msgs.msg import Clock

        if not rclpy.ok():
            rclpy.init(args=None)
        self.node = Node("sim_clock_publisher")
        self.publisher = self.node.create_publisher(Clock, "/clock", 10)
        self.timer = self.node.create_timer(1.0 / self.rate_hz, self.publish_once)

    def publish_once(self):
        msg = build_clock_msg(self.clock.now())
        if self.publisher is not None:
            self.publisher.publish(msg)
        return msg

    def shutdown(self) -> None:
        if self.node is None:
            return
        if self.timer is not None:
            self.node.destroy_timer(self.timer)
        self.node.destroy_node()
        self.node = None
        self.timer = None
        self.publisher = None
