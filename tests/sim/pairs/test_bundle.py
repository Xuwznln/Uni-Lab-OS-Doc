"""M-2: parse + validate pair bundle (contract C-1)."""

import json
from pathlib import Path

import pytest

from unilabos.sim.pairs.bundle import PairBundle, parse_bundle, validate_bundle

FIXTURES = Path(__file__).parent / "fixtures"


def _bundle_data():
    return json.loads((FIXTURES / "bundle_ok.json").read_text(encoding="utf-8"))


def test_parse_bundle_ok():
    bundle = parse_bundle(_bundle_data())
    assert isinstance(bundle, PairBundle)
    assert bundle.bundle_version == "2026-06-20T00:00:00Z"
    assert bundle.engine == "gazebo"
    assert len(bundle.pairs) == 2

    first = bundle.pairs[0]
    assert first.real == "dalong_heaterstirrer"
    assert first.engine == "gazebo"
    assert first.virtual == "community.dalong.virtual_heaterstirrer"
    assert first.missing_sim_policy == "stub"
    assert first.is_default is True
    assert first.priority == 100
    assert first.twin_capability.enabled is True
    assert first.twin_capability.observed == ["temperature", "rpm"]
    assert first.twin_capability.throttle_hz == 20
    assert first.virtual_package is not None
    assert first.virtual_package.normalized_name == "dalong-sim-drivers"
    assert first.virtual_package.version == "0.2.1"

    second = bundle.pairs[1]
    assert second.real == "qone_nmr"
    assert second.engine == "gazebo"
    assert second.virtual is None
    assert second.missing_sim_policy == "fail"
    assert second.twin_capability.enabled is False
    assert second.virtual_package is None


def test_validate_bundle_ok():
    assert validate_bundle(parse_bundle(_bundle_data())) == []


def test_validate_bundle_bad_policy():
    bundle = parse_bundle({"bundle_version": "x", "pairs": [{"real": "a", "missing_sim_policy": "bogus"}]})
    errors = validate_bundle(bundle)
    assert any("invalid missing_sim_policy" in e for e in errors)


def test_validate_bundle_duplicate_real():
    bundle = parse_bundle({"pairs": [{"real": "a"}, {"real": "a"}]})
    assert any("duplicate" in e for e in validate_bundle(bundle))


def test_parse_bundle_rejects_non_dict():
    with pytest.raises(ValueError):
        parse_bundle([])  # type: ignore[arg-type]


def test_parse_bundle_skips_entries_without_real():
    bundle = parse_bundle({"pairs": [{"virtual": "v"}, {"real": "ok"}]})
    assert [p.real for p in bundle.pairs] == ["ok"]
