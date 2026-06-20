"""M-7: device_pair.yaml -> backend admin import script."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "import_device_pair_yaml_to_backend",
    Path(__file__).parents[3] / "scripts" / "import_device_pair_yaml_to_backend.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def _write_pairs(tmp_path):
    p = tmp_path / "device_pair.yaml"
    p.write_text(
        "pairs:\n"
        "  - real: dalong_heaterstirrer\n"
        "    virtual: virtual_heatchill\n"
        "    missing_sim_policy: stub\n"
        "    twin_observed: [temperature, rpm]\n"
        "  - real: qone_nmr\n"
        "    virtual: null\n"
        "    missing_sim_policy: fail\n",
        encoding="utf-8",
    )
    return p


def test_to_admin_payload_maps_fields():
    payload = mod.to_admin_payload({"real": "x", "virtual": "vx", "missing_sim_policy": "stub", "twin_observed": ["a"]})
    assert payload["real_class"] == "x"
    assert payload["virtual_class"] == "vx"
    assert payload["pair_type"] == "mock"
    assert payload["supported_modes"] == ["sim", "twin"]
    assert payload["twin_config"]["observed"] == ["a"]


def test_to_admin_payload_stub_when_no_virtual():
    payload = mod.to_admin_payload({"real": "y", "virtual": None, "missing_sim_policy": "fail"})
    assert payload["pair_type"] == "stub"
    assert payload["virtual_class"] is None
    assert payload["supported_modes"] == ["sim"]


def test_import_dry_run_does_not_call(tmp_path):
    pairs = mod.load_pairs(_write_pairs(tmp_path))
    calls = []
    result = mod.import_pairs(pairs, lambda p: calls.append(p), dry_run=True)
    assert calls == []
    assert set(result["created"]) == {"dalong_heaterstirrer", "qone_nmr"}


def test_import_calls_create_per_pair(tmp_path):
    pairs = mod.load_pairs(_write_pairs(tmp_path))
    calls = []
    result = mod.import_pairs(pairs, lambda p: calls.append(p["real_class"]) or {"code": 0}, dry_run=False)
    assert set(calls) == {"dalong_heaterstirrer", "qone_nmr"}
    assert result["failed"] == []


def test_import_collects_failures(tmp_path):
    pairs = mod.load_pairs(_write_pairs(tmp_path))

    def flaky(p):
        if p["real_class"] == "qone_nmr":
            raise RuntimeError("409 conflict")
        return {"code": 0}

    result = mod.import_pairs(pairs, flaky, dry_run=False)
    assert result["created"] == ["dalong_heaterstirrer"]
    assert result["failed"][0]["real"] == "qone_nmr"
