"""M-6: check_device_pairs --bundle reporting."""

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_device_pairs",
    Path(__file__).parents[3] / "scripts" / "check_device_pairs.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

FIXTURES = Path(__file__).parent / "fixtures"


def test_report_bundle(tmp_path, capsys):
    # accept full response shape {data:{...}}
    resp = {"data": json.loads((FIXTURES / "bundle_ok.json").read_text(encoding="utf-8"))}
    bundle_file = tmp_path / "bundle.json"
    bundle_file.write_text(json.dumps(resp), encoding="utf-8")

    rc = mod.report_bundle(bundle_file)
    out = capsys.readouterr().out
    assert rc == 0
    assert "bundle_pairs=2" in out
    assert "coverage=1/2" in out          # only dalong has virtual
    assert "FAIL qone_nmr" in out         # qone_nmr -> fail policy
