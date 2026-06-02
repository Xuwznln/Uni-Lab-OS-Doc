from __future__ import annotations

import json
from pathlib import Path

from scripts import create_isaac_lab_layout


def test_parse_args_defaults(tmp_path):
    args = create_isaac_lab_layout.parse_args(
        [
            "--builder-out",
            str(tmp_path / "builder.py"),
            "--manifest-out",
            str(tmp_path / "manifest.json"),
            "--stage-out",
            "/tmp/roboarm_lab_a.usda",
        ]
    )

    assert args.layout == "central-island"
    assert args.builder_out == str(tmp_path / "builder.py")
    assert args.manifest_out == str(tmp_path / "manifest.json")
    assert args.stage_out == "/tmp/roboarm_lab_a.usda"
    assert args.check_assets is False


def test_main_writes_builder_and_manifest(tmp_path):
    builder = tmp_path / "builder.py"
    manifest = tmp_path / "manifest.json"

    result = create_isaac_lab_layout.main(
        [
            "--builder-out",
            str(builder),
            "--manifest-out",
            str(manifest),
            "--stage-out",
            "/tmp/roboarm_lab_a.usda",
        ]
    )

    assert result == 0
    assert builder.exists()
    assert manifest.exists()
    assert "URDFParseAndImportFile" in builder.read_text()
    manifest_payload = json.loads(Path(manifest).read_text())
    assert manifest_payload["layout"] == "roboarm_chem_04_central_island"
    assert manifest_payload["query_targets"]["robot"] == "/World/Lab/RoboArmChem04"
