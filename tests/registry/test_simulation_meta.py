"""M-8: virtual driver self-marking metadata (Plan 08 §6.3 / §6.3.1)."""

from unilabos.registry.simulation_meta import (
    SIMULATION_META_KEYS,
    apply_simulation_meta,
    device_simulation_meta,
)


def test_keys_order_and_membership():
    assert SIMULATION_META_KEYS == (
        "driver_runtime_kind", "virtual_driver_kind", "sim_engine",
    )


def test_device_simulation_meta_defaults_real():
    m = device_simulation_meta()
    assert m == {
        "driver_runtime_kind": "real",
        "virtual_driver_kind": None,
        "sim_engine": None,
    }


def test_device_simulation_meta_virtual():
    m = device_simulation_meta("virtual", "local_mock", "none")
    assert m["driver_runtime_kind"] == "virtual"
    assert m["virtual_driver_kind"] == "local_mock"
    assert m["sim_engine"] == "none"


def test_apply_writes_virtual_marks():
    entry: dict = {}
    apply_simulation_meta(entry, {
        "driver_runtime_kind": "virtual",
        "virtual_driver_kind": "local_mock",
        "sim_engine": "none",
    })
    assert entry == {
        "driver_runtime_kind": "virtual",
        "virtual_driver_kind": "local_mock",
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
        "virtual_driver_kind": None,
        "sim_engine": "",
    })
    # only the meaningful field survives
    assert entry == {"driver_runtime_kind": "virtual"}
