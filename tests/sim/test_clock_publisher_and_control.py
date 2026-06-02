from types import SimpleNamespace

from unilabos.sim.clock import SimClock
from unilabos.sim.clock_control import SIM_CLOCK_SERVICE_NAMES, handle_pause, handle_resume, handle_set_rate, handle_status
from unilabos.sim.clock_publisher import SimClockPublisher, build_clock_msg


def test_build_clock_msg_splits_sec_nanosec():
    msg = build_clock_msg(3.25)
    assert msg.clock.sec == 3
    assert 249_000_000 <= msg.clock.nanosec <= 251_000_000


def test_clock_publisher_disabled_in_real_mode_without_ros_node():
    pub = SimClockPublisher(SimClock("real"), auto_start=False)
    assert pub.enabled is False
    assert pub.publish_once().clock.sec > 0


def test_clock_control_set_rate_pause_resume_status():
    clock = SimClock("sim", scale=1.0)
    resp = handle_set_rate(clock, SimpleNamespace(rate=5.0))
    assert resp.success is True
    assert clock.scale == 5.0
    assert handle_pause(clock).success is True
    assert clock.paused is True
    assert handle_resume(clock).success is True
    status = handle_status(clock)
    assert status.mode == "sim"
    assert status.rate == 5.0
    assert status.sim_now >= 0.0


def test_clock_control_rejects_real_rate_change():
    clock = SimClock("real")
    resp = handle_set_rate(clock, SimpleNamespace(rate=5.0))
    assert resp.success is False
    assert resp.rate == 1.0


def test_clock_control_declares_legacy_and_unilab_namespaced_services():
    assert "/sim/set_rate" in SIM_CLOCK_SERVICE_NAMES["set_rate"]
    assert "/unilab/sim/set_rate" in SIM_CLOCK_SERVICE_NAMES["set_rate"]
    assert "/unilab/sim/pause" in SIM_CLOCK_SERVICE_NAMES["pause"]
