"""Plan 09 Task 2: YAML $ref resolution."""

from pathlib import Path

import pytest
import yaml

from unilabos.registry.yaml_ref import YamlRefCycleError, resolve_yaml_refs


def test_resolve_yaml_refs_loads_relative_file_and_json_pointer():
    file_path = Path(__file__).parent / "fixtures" / "ref_registry" / "devices" / "opentrons_flex.yaml"
    raw_data = yaml.safe_load(file_path.read_text(encoding="utf-8"))

    resolved = resolve_yaml_refs(raw_data, base_file=file_path)

    device = resolved["pylabrobot.lh.opentrons_flex"]
    assert "setup" in device["class"]["action_value_mappings"]
    assert device["class"]["status_types"] == {"initialized": "bool"}


def test_resolve_yaml_refs_detects_cycles(tmp_path):
    cycle_file = tmp_path / "cycle.yaml"
    cycle_file.write_text("value:\n  $ref: cycle.yaml#/value\n", encoding="utf-8")
    raw_data = yaml.safe_load(cycle_file.read_text(encoding="utf-8"))

    with pytest.raises(YamlRefCycleError):
        resolve_yaml_refs(raw_data, base_file=cycle_file)
