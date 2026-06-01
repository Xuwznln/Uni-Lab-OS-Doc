"""ROS service callbacks for simulation clock control."""

from __future__ import annotations

import ctypes
import os
from types import SimpleNamespace

from unilabos.sim.clock import SimClock

SIM_CLOCK_SERVICE_NAMES = {
    "pause": ("/sim/pause", "/unilab/sim/pause"),
    "resume": ("/sim/resume", "/unilab/sim/resume"),
    "set_rate": ("/sim/set_rate", "/unilab/sim/set_rate"),
    "status": ("/sim/status", "/unilab/sim/status"),
}


def _set_response(resp, **values):
    if resp is None:
        resp = SimpleNamespace()
    for key, value in values.items():
        setattr(resp, key, value)
    return resp


def handle_set_rate(clock: SimClock, req, resp=None):
    rate = getattr(req, "rate", getattr(req, "scale", 1.0))
    try:
        changed = clock.set_scale(rate)
        return _set_response(resp, success=changed, message="ok" if changed else "rate locked", rate=clock.scale)
    except Exception as exc:
        return _set_response(resp, success=False, message=str(exc), rate=clock.scale)


def handle_pause(clock: SimClock, req=None, resp=None):
    changed = clock.pause()
    return _set_response(resp, success=changed, message="paused" if changed else "pause locked")


def handle_resume(clock: SimClock, req=None, resp=None):
    changed = clock.resume()
    return _set_response(resp, success=changed, message="resumed" if changed else "resume locked")


def handle_status(clock: SimClock, req=None, resp=None):
    return _set_response(resp, mode=clock.mode, rate=clock.scale, paused=clock.paused, sim_now=clock.now())


def _preload_unilabos_msg_typesupport() -> None:
    """Conda ROS overlays may need generated message libs loaded RTLD_GLOBAL."""

    names = [
        "libunilabos_msgs__rosidl_generator_c.so",
        "libunilabos_msgs__rosidl_generator_py.so",
        "libunilabos_msgs__rosidl_typesupport_c.so",
        "libunilabos_msgs__rosidl_typesupport_fastrtps_c.so",
    ]
    for directory in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep):
        if not directory:
            continue
        for name in names:
            path = os.path.join(directory, name)
            if os.path.exists(path):
                try:
                    ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


class SimClockControlNode:
    def __init__(self, clock: SimClock, auto_start: bool = True):
        self.clock = clock
        self.node = None
        self.services = []
        if auto_start:
            self.start()

    def start(self) -> None:
        if self.node is not None:
            return
        import rclpy
        from rclpy.node import Node
        from std_srvs.srv import Trigger

        if not rclpy.ok():
            rclpy.init(args=None)
        self.node = Node("sim_clock_control")
        for service_name in SIM_CLOCK_SERVICE_NAMES["pause"]:
            self.services.append(self.node.create_service(Trigger, service_name, lambda req, resp: handle_pause(self.clock, req, resp)))
        for service_name in SIM_CLOCK_SERVICE_NAMES["resume"]:
            self.services.append(self.node.create_service(Trigger, service_name, lambda req, resp: handle_resume(self.clock, req, resp)))
        try:
            _preload_unilabos_msg_typesupport()
            from unilabos_msgs.srv import GetSimStatus, SetSimRate

            for service_name in SIM_CLOCK_SERVICE_NAMES["set_rate"]:
                self.services.append(self.node.create_service(SetSimRate, service_name, lambda req, resp: handle_set_rate(self.clock, req, resp)))
            for service_name in SIM_CLOCK_SERVICE_NAMES["status"]:
                self.services.append(self.node.create_service(GetSimStatus, service_name, lambda req, resp: handle_status(self.clock, req, resp)))
        except Exception:
            pass

    def shutdown(self) -> None:
        if self.node is not None:
            self.node.destroy_node()
            self.node = None
            self.services = []
