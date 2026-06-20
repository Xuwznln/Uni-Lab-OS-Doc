"""M-8: virtual driver self-marking metadata (Plan 08 §6.3 / §6.3.1)."""

from unilabos.registry.simulation_meta import (
    SIMULATION_META_KEYS,
    apply_simulation_meta,
    device_simulation_meta,
)


def test_keys_order_and_membership():
    assert SIMULATION_META_KEYS == (
        "driver_runtime_kind", "simulation_kind", "supported_modes", "sim_engine",
    )


def test_device_simulation_meta_defaults_real():
    m = device_simulation_meta()
    assert m == {
        "driver_runtime_kind": "real",
        "simulation_kind": None,
        "supported_modes": [],
        "sim_engine": None,
    }


def test_device_simulation_meta_virtual():
    m = device_simulation_meta("virtual", "mock", ["sim", "twin"], "none")
    assert m["driver_runtime_kind"] == "virtual"
    assert m["simulation_kind"] == "mock"
    assert m["supported_modes"] == ["sim", "twin"]
    assert m["sim_engine"] == "none"


def test_apply_writes_virtual_marks():
    entry: dict = {}
    apply_simulation_meta(entry, {
        "driver_runtime_kind": "virtual",
        "simulation_kind": "mock",
        "supported_modes": ["sim", "twin"],
        "sim_engine": "none",
    })
    assert entry == {
        "driver_runtime_kind": "virtual",
        "simulation_kind": "mock",
        "supported_modes": ["sim", "twin"],
        "sim_engine": "none",
    }


def test_apply_skips_real_device_unchanged():
    """Real device (driver_runtime_kind=real, nothing else) emits nothing."""
    entry = {"existing": 1}
    apply_simulation_meta(entry, device_simulation_meta())  # all defaults => real
    assert entry == {"existing": 1}


def test_apply_skips_empty_values():
    entry: dict = {}
    apply_simulation_meta(entry, {
        "driver_runtime_kind": "virtual",
        "simulation_kind": None,
        "supported_modes": [],
        "sim_engine": "",
    })
    # only the meaningful field survives
    assert entry == {"driver_runtime_kind": "virtual"}
