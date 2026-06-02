from pathlib import Path

import pytest

from unilabos.registry.pair_registry import PairRegistry


def test_pair_registry_lookup_and_default(tmp_path: Path):
    pair_file = tmp_path / "device_pair.yaml"
    pair_file.write_text(
        """
pairs:
  - real: pump
    virtual: virtual_transferpump
    twin_observed: [status]
    twin_throttle_hz: 4
  - real: nmr
    virtual: null
    missing_sim_policy: fail
""",
        encoding="utf-8",
    )
    registry = PairRegistry(pair_file)
    pump = registry.lookup("pump")
    assert pump.virtual == "virtual_transferpump"
    assert pump.twin_observed == ["status"]
    assert pump.twin_throttle_hz == 4
    assert registry.lookup("unknown").missing_sim_policy == "stub"
    assert registry.lookup("unknown").explicit is False
    assert registry.lookup("nmr").missing_sim_policy == "fail"


def test_pair_registry_rejects_invalid_policy(tmp_path: Path):
    pair_file = tmp_path / "device_pair.yaml"
    pair_file.write_text("pairs:\n- real: x\n  missing_sim_policy: maybe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing_sim_policy"):
        PairRegistry(pair_file)
