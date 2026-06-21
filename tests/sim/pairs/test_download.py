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


def test_make_downloader_calls_real_extract(monkeypatch):
    """make_downloader's default fetch calls community_packages._download_and_extract_package."""
    import unilabos.app.community_packages as cp
    from unilabos.sim.pairs.download import make_downloader

    calls = []

    def fake_extract(download_url, working_dir, normalized, version, sha256, http_client):
        calls.append((download_url, normalized, version, sha256))
        return f"/mnt/{normalized}"

    monkeypatch.setattr(cp, "_download_and_extract_package", fake_extract)

    out = make_downloader(http_client=None, working_dir="/tmp/wd")(_bundle())
    assert out == ["/mnt/dalong-sim-drivers"]
    assert calls == [(
        "https://example.invalid/dalong-sim-drivers-0.2.1.whl",
        "dalong-sim-drivers", "0.2.1", "sha256:abc123",
    )]
