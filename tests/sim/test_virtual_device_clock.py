import time

from unilabos.devices.virtual.virtual_gas_source import VirtualGasSource
from unilabos.devices.virtual.virtual_multiway_valve import VirtualMultiwayValve
from unilabos.sim.backends.fake_physics import FakePhysicsBackend
from unilabos.sim.clock import SimClock
from unilabos.sim.context import RuntimeContext, _reset_for_test, init_runtime_context


def setup_function():
    _reset_for_test()


def teardown_function():
    _reset_for_test()


def test_virtual_gas_source_status_uses_sim_clock():
    init_runtime_context(RuntimeContext(mode="sim", clock=SimClock("sim", scale=100.0)))
    dev = VirtualGasSource()
    t0 = time.monotonic()
    dev.set_status("CLOSED")
    assert time.monotonic() - t0 < 0.2
    assert dev.status == "CLOSED"


def test_virtual_multiway_valve_uses_sim_clock():
    init_runtime_context(RuntimeContext(mode="sim", clock=SimClock("sim", scale=100.0)))
    valve = VirtualMultiwayValve(positions=8)
    t0 = time.monotonic()
    result = valve.set_position(8)
    assert time.monotonic() - t0 < 0.3
    assert "8" in result


def test_virtual_multiway_valve_dispatches_position_to_physics():
    physics = FakePhysicsBackend()
    init_runtime_context(RuntimeContext(mode="sim", clock=SimClock("sim", scale=100.0), physics=physics))
    valve = VirtualMultiwayValve(id="valve_a", positions=8)

    valve.set_position(3)

    assert physics.commands["valve_a"] == {
        "type": "set_position",
        "position": 3,
        "device": "virtual_multiway_valve",
    }
