"""ROS-free contracts for the virtual devices used by protocol simulations."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
import yaml

from unilabos.devices.virtual.virtual_gas_source import VirtualGasSource
from unilabos.devices.virtual.virtual_multiway_valve import VirtualMultiwayValve
from unilabos.devices.virtual.virtual_sample_demo import VirtualSampleDemo
from unilabos.devices.virtual.virtual_solenoid_valve import VirtualSolenoidValve
from unilabos.devices.virtual.virtual_solid_dispenser import VirtualSolidDispenser
from unilabos.devices.virtual.virtual_vacuum_pump import VirtualVacuumPump
from unilabos.devices.virtual.virtual_valve import VirtualValve


class InstantNode:
    async def sleep(self, _seconds: float) -> None:
        return None


def run(awaitable):
    return asyncio.run(awaitable)


def test_all_virtual_registry_entries_import_without_ros() -> None:
    registry_path = Path(__file__).parents[2] / "unilabos" / "registry" / "devices" / "virtual_device.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))

    assert len(registry) == 14
    for name, definition in registry.items():
        module_name, class_name = definition["class"]["module"].split(":")
        device_class = getattr(importlib.import_module(module_name), class_name)
        assert device_class.__name__ == class_name, name


@pytest.mark.parametrize("device_class", [VirtualGasSource, VirtualVacuumPump])
def test_binary_sources_report_boolean_state(device_class) -> None:
    device = device_class("source")
    run(device.initialize())

    assert device.is_open() is True
    assert device.is_closed() is False
    device.close()
    assert device.is_open() is False
    assert device.is_closed() is True
    device.open()
    assert device.is_open() is True


def test_virtual_valve_constructs_and_validates_positions() -> None:
    valve = VirtualValve("valve", {"positions": 4})
    run(valve.initialize())

    assert valve.status == "Idle"
    assert run(valve.set_position(2)) is True
    assert valve.current_position == 2
    assert valve.valve_state == "Open"
    assert run(valve.set_position(5)) is False
    assert run(valve.close()) is True
    assert valve.current_position == 0


def test_virtual_solenoid_valve_handles_commands_and_invalid_types() -> None:
    valve = VirtualSolenoidValve("solenoid")
    valve.post_init(InstantNode())
    run(valve.initialize())

    assert run(valve.open())["success"] is True
    assert valve.is_open is True
    assert run(valve.set_valve_position(1))["success"] is False
    assert run(valve.close())["success"] is True
    assert valve.is_closed() is True


def test_virtual_multiway_valve_routes_and_rejects_invalid_positions(monkeypatch) -> None:
    from unilabos.devices.virtual import virtual_multiway_valve

    monkeypatch.setattr(virtual_multiway_valve.time, "sleep", lambda _seconds: None)
    valve = VirtualMultiwayValve("valve", positions=3)

    assert valve.is_at_pump_position()
    assert "位置 0" in valve.flow_path
    assert "位置 2" in valve.set_position(2)
    assert valve.is_at_port(2)
    assert "失败" in valve.set_position(4)
    assert valve.valve_state == "Error"
    valve.reset()
    assert valve.is_at_pump_position()


def test_virtual_sample_demo_preserves_sample_provenance(monkeypatch) -> None:
    monkeypatch.setattr("unilabos.devices.virtual.virtual_sample_demo.random.gauss", lambda *_: 0.0)
    monkeypatch.setattr("unilabos.devices.virtual.virtual_sample_demo.random.uniform", lambda *_: 0.5)
    device = VirtualSampleDemo("samples")

    measured = run(device.measure_samples([1.0, 2.0]))
    assert measured["concentrations"] == [1.0, 2.0]
    assert measured["absorbance"] == [0.05, 0.1]

    split = run(device.split_and_measure([3.0, 6.0], split_count=2))
    assert split["volumes"] == [1.5, 1.5, 3.0, 3.0]
    assert split["unilabos_samples"] == [0, 0, 1, 1]
    analyzed = run(device.analyze_readings(split["readings"], split["unilabos_samples"]))
    assert analyzed["passed"] == [True, True, True, True]
    assert analyzed["unilabos_samples"] == split["unilabos_samples"]


def test_virtual_solid_dispenser_parses_units_and_enforces_capacity() -> None:
    dispenser = VirtualSolidDispenser("powder", {"max_capacity": 5})
    dispenser.post_init(InstantNode())
    run(dispenser.initialize())

    assert dispenser.parse_mass_string("2.5 mg") == pytest.approx(0.0025)
    assert dispenser.parse_mol_string("2 mmol") == pytest.approx(0.002)
    result = run(dispenser.add_solid("reactor", "magnesium", mass="2 g"))
    assert result["success"] is True
    assert dispenser.dispensed_amount == pytest.approx(2.0)
    assert dispenser.total_operations == 1
    rejected = run(dispenser.add_solid("reactor", "magnesium", mass="6 g"))
    assert rejected["return_info"] == "exceeds_capacity"
