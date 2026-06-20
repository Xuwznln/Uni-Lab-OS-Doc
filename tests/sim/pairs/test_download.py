"""M-3: virtual package download wrapper (fetch injected)."""

import json
from pathlib import Path

from unilabos.sim.pairs.bundle import parse_bundle
from unilabos.sim.pairs.download import download_virtual_packages, iter_virtual_package_refs

FIXTURES = Path(__file__).parent / "fixtures"


def _bundle():
    return parse_bundle(json.loads((FIXTURES / "bundle_ok.json").read_text(encoding="utf-8")))


def test_iter_refs_only_entries_with_package():
    refs = iter_virtual_package_refs(_bundle())
    assert [r.normalized_name for r in refs] == ["dalong-sim-drivers"]


def test_download_calls_fetch_per_ref():
    fetched = []
    out = download_virtual_packages(_bundle(), lambda ref: fetched.append(ref.normalized_name) or f"/mnt/{ref.normalized_name}")
    assert fetched == ["dalong-sim-drivers"]
    assert out == ["/mnt/dalong-sim-drivers"]


def test_download_isolates_failures():
    def boom(ref):
        raise RuntimeError("network")
    # must not raise
    assert download_virtual_packages(_bundle(), boom) == []
