"""H: admin seeding script (create + activate), backend mocked."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "seed_simulation_pair",
    Path(__file__).parents[3] / "scripts" / "seed_simulation_pair.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def test_build_create_payload():
    p = mod.build_create_payload(
        real_template_uuid="r-uuid", virtual_template_uuid="v-uuid", engine="gazebo",
        missing_sim_policy="stub", priority=100, is_default=True,
        twin_observed=["temperature"], twin_throttle_hz=20,
    )
    assert p["real_resource_template_uuid"] == "r-uuid"
    assert p["virtual_resource_template_uuid"] == "v-uuid"
    assert p["engine"] == "gazebo"
    assert p["status"] == "active"
    assert p["twin_capability"] == {"enabled": True, "observed": ["temperature"], "throttle_hz": 20}


def test_seed_pair_creates_then_activates():
    created_calls, activated_calls = [], []

    def create_fn(payload):
        created_calls.append(payload)
        return {"code": 0, "data": {"uuid": "pair-123"}}

    def activate_fn(uuid):
        activated_calls.append(uuid)
        return {"code": 0}

    result = mod.seed_pair({"real_resource_template_uuid": "r"}, create_fn, activate_fn)
    assert result["pair_uuid"] == "pair-123"
    assert activated_calls == ["pair-123"]
    assert result["activated"] == {"code": 0}


def test_seed_pair_no_activate_when_no_uuid():
    result = mod.seed_pair({}, lambda p: {"code": 1, "error": "bad"}, lambda u: {"code": 0})
    assert result["pair_uuid"] is None
    assert result["activated"] is None
