"""M-8: @device decorator accepts and stores virtual driver marks."""

from unilabos.registry.decorators import device


def test_device_virtual_marks_in_registry_meta():
    @device(
        id="virtual_dalong_heaterstirrer_t",
        category=["heaterstirrer", "virtual_device"],
        displayname="大龙加热搅拌器 Mock",
        driver_runtime_kind="virtual",
        virtual_driver_kind="local_mock",
        sim_engine="none",
    )
    class VirtualDalongHeaterStirrerT:
        pass

    meta = VirtualDalongHeaterStirrerT._device_registry_meta
    assert meta["driver_runtime_kind"] == "virtual"
    assert meta["virtual_driver_kind"] == "local_mock"
    assert meta["sim_engine"] == "none"


def test_device_defaults_to_real():
    @device(id="real_pump_t", category=["pump"])
    class RealPumpT:
        pass

    meta = RealPumpT._device_registry_meta
    assert meta["driver_runtime_kind"] == "real"
    assert meta["virtual_driver_kind"] is None
    assert meta["sim_engine"] is None
