"""M-8: @device decorator accepts and stores virtual driver marks."""

from unilabos.registry.decorators import device


def test_device_virtual_marks_in_registry_meta():
    @device(
        id="virtual_dalong_heaterstirrer_t",
        category=["heaterstirrer", "virtual_device"],
        displayname="大龙加热搅拌器 Mock",
        driver_runtime_kind="virtual",
        simulation_kind="mock",
        supported_modes=["sim", "twin"],
        sim_engine="none",
    )
    class VirtualDalongHeaterStirrerT:
        pass

    meta = VirtualDalongHeaterStirrerT._device_registry_meta
    assert meta["driver_runtime_kind"] == "virtual"
    assert meta["simulation_kind"] == "mock"
    assert meta["supported_modes"] == ["sim", "twin"]
    assert meta["sim_engine"] == "none"


def test_device_defaults_to_real():
    @device(id="real_pump_t", category=["pump"])
    class RealPumpT:
        pass

    meta = RealPumpT._device_registry_meta
    assert meta["driver_runtime_kind"] == "real"
    assert meta["simulation_kind"] is None
    assert meta["supported_modes"] == []
    assert meta["sim_engine"] is None
