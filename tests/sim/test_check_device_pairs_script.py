from pathlib import Path

from scripts.check_device_pairs import main


def test_check_device_pairs_reports_stub_fallbacks(tmp_path: Path, capsys):
    registry = tmp_path / "registry"
    devices = registry / "devices"
    devices.mkdir(parents=True)
    (devices / "devices.yaml").write_text("pump: {}\nunknown: {}\n", encoding="utf-8")
    pair_file = registry / "device_pair.yaml"
    pair_file.write_text("pairs:\n- real: pump\n  virtual: virtual_pump\n", encoding="utf-8")
    assert main(["--registry-dir", str(registry), "--pair-file", str(pair_file)]) == 0
    out = capsys.readouterr().out
    assert "stub_fallbacks=1" in out
    assert "STUB unknown" in out


def test_check_device_pairs_can_fail_on_stub(tmp_path: Path):
    registry = tmp_path / "registry"
    devices = registry / "devices"
    devices.mkdir(parents=True)
    (devices / "devices.yaml").write_text("unknown: {}\n", encoding="utf-8")
    pair_file = registry / "device_pair.yaml"
    pair_file.write_text("pairs: []\n", encoding="utf-8")
    assert main(["--registry-dir", str(registry), "--pair-file", str(pair_file), "--fail-on-stub"]) == 2
