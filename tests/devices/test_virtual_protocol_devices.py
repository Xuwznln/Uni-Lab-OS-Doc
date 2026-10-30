"""Protocol-facing behavior for the issue-43 virtual instrument set."""

from __future__ import annotations

import asyncio

import pytest

from unilabos.devices.virtual.virtual_centrifuge import VirtualCentrifuge
from unilabos.devices.virtual.virtual_column import VirtualColumn
from unilabos.devices.virtual.virtual_filter import VirtualFilter
from unilabos.devices.virtual.virtual_heatchill import VirtualHeatChill
from unilabos.devices.virtual.virtual_rotavap import VirtualRotavap
from unilabos.devices.virtual.virtual_separator import VirtualSeparator
from unilabos.devices.virtual.virtual_stirrer import VirtualStirrer
from unilabos.devices.virtual.virtual_transferpump import VirtualTransferPump


class InstantNode:
    async def sleep(self, _seconds: float) -> None:
        return None


class FastClock:
    def __init__(self) -> None:
        self.value = 0.0

    def time(self) -> float:
        self.value += 100.0
        return self.value


def run(awaitable):
    return asyncio.run(awaitable)


def attach(device):
    device.post_init(InstantNode())
    return device


def test_virtual_centrifuge_initializes_completes_and_rejects_bad_speed(monkeypatch) -> None:
    from unilabos.devices.virtual import virtual_centrifuge

    monkeypatch.setattr(virtual_centrifuge.time_module, "time", FastClock().time)
    device = attach(VirtualCentrifuge("centrifuge", {"max_speed": 1000}))
    assert run(device.initialize()) is True
    assert run(device.centrifuge("vessel", speed=500, time=1, temp=25)) is True
    assert device.centrifuge_state == "Completed"
    assert device.progress == 100.0
    assert run(device.centrifuge("vessel", speed=50, time=1, temp=25)) is False
    assert device.centrifuge_state == "Error"


def test_virtual_column_runs_to_completion() -> None:
    device = attach(VirtualColumn("column"))
    assert run(device.initialize()) is True
    assert run(
        device.run_column(
            "source",
            "product",
            "silica",
            rf="0.4",
            solvent1="ethyl_acetate",
            solvent2="hexane",
            ratio="40:60",
        )
    ) is True
    assert device.column_state == "Ready"
    assert device.progress == 100.0
    assert device.final_volume == pytest.approx(100.0)


def test_virtual_filter_covers_temperature_and_volume_boundaries(monkeypatch) -> None:
    from unilabos.devices.virtual import virtual_filter

    monkeypatch.setattr(virtual_filter.time_module, "time", FastClock().time)
    device = attach(VirtualFilter("filter", {"max_volume": 100}))
    assert run(device.initialize()) is True
    assert run(device.filter({"id": "source"}, volume=20, temp=25)) is True
    assert device.filtered_volume == pytest.approx(20)
    assert run(device.filter({"id": "source"}, volume=101, temp=25)) is False
    assert run(device.filter({"id": "source"}, volume=20, temp=25, stir=True, stir_speed=5000)) is False


def test_virtual_heat_chill_supports_ros_goal_and_rejects_limits(monkeypatch) -> None:
    from unilabos.devices.virtual import virtual_heatchill

    monkeypatch.setattr(virtual_heatchill.time_module, "time", FastClock().time)
    device = attach(VirtualHeatChill("heater", {"max_temp": 100}))
    assert run(device.initialize()) is True
    assert run(
        device.heat_chill(
            temp=50,
            time=1,
            stir=True,
            stir_speed=300,
            purpose="contract",
            vessel={},
            temp_spec="",
            time_spec="",
            pressure="",
            reflux_solvent="",
        )
    ) is True
    assert device.operation_mode == "Completed"
    assert run(device.heat_chill_start(150, "invalid")) is False
    assert run(device.heat_chill_stop()) is True
    assert device.operation_mode == "Stopped"


def test_virtual_rotavap_handles_solvent_adjustment_and_limits(monkeypatch) -> None:
    from unilabos.devices.virtual import virtual_rotavap

    monkeypatch.setattr(virtual_rotavap.time_module, "time", FastClock().time)
    device = attach(VirtualRotavap("rotavap", {"max_temp": 120, "max_rotation_speed": 500}))
    assert run(device.initialize()) is True
    assert run(device.evaporate("flask", time=1, temp=40, stir_speed=100, solvent="ethanol")) is True
    assert device.rotavap_state == "Completed"
    assert device.evaporated_volume == pytest.approx(100.0)
    assert run(device.evaporate("flask", time=1, temp=5, stir_speed=100)) is False


def test_virtual_separator_validates_contract_and_completes() -> None:
    device = attach(VirtualSeparator("separator"))
    assert run(device.initialize()) is True
    assert run(
        device.separate(
            purpose="extract",
            product_phase="top",
            from_vessel="source",
            separation_vessel="sep",
            to_vessel="product",
            stir_time=0,
            settling_time=0,
            repeats=1,
        )
    ) is True
    assert device.separator_state == "Ready"
    assert device.progress == 100.0
    assert run(
        device.separate(
            purpose="extract",
            product_phase="middle",
            from_vessel="source",
            separation_vessel="sep",
            to_vessel="product",
        )
    ) is False


def test_virtual_stirrer_supports_timed_and_continuous_modes(monkeypatch) -> None:
    from unilabos.devices.virtual import virtual_stirrer

    monkeypatch.setattr(virtual_stirrer.time_module, "time", FastClock().time)
    device = attach(VirtualStirrer("stirrer", {"max_speed": 1000}))
    assert run(device.initialize()) is True
    assert run(device.stir(stir_time=0, stir_speed=300, settling_time=0)) is True
    assert device.operation_mode == "Completed"
    assert run(device.start_stir("vessel", 300, "mix")) is True
    assert device.is_stirring is True
    assert run(device.stop_stir("vessel")) is True
    assert device.is_stirring is False
    assert run(device.start_stir("vessel", 5000)) is False


def test_virtual_transfer_pump_clamps_position_and_transfers() -> None:
    device = attach(VirtualTransferPump("pump", {"max_volume": 5}))
    assert run(device.initialize()) is True
    assert run(device.set_position(10, max_velocity=0))['success'] is True
    assert device.position == pytest.approx(5.0)
    assert device.is_full() is True
    awaitable = device.transfer(
        from_vessel="source",
        to_vessel="target",
        volume=2,
        amount="",
        time=0,
        viscous=False,
        rinsing_solvent="",
        rinsing_volume=0,
        rinsing_repeats=0,
        solid=False,
    )
    assert run(awaitable) is None
    assert device.current_volume == pytest.approx(3.0)
    assert run(device.empty_syringe()) is None
    assert device.is_empty() is True
