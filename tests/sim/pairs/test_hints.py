"""M-1: read package-bundled simulation pair hints (contract C-2)."""

from pathlib import Path

from unilabos.sim.pairs.hints import collect_all_pair_hints, read_pair_hints

FIXTURES = Path(__file__).parent / "fixtures"


def test_read_pair_hints_from_package_dir(tmp_path):
    (tmp_path / "unilab_simulation_pairs.yaml").write_text(
        "pairs:\n"
        "  - real: dalong_heaterstirrer\n"
        "    virtual: community.dalong.virtual_heaterstirrer\n"
        "    pair_type: mock\n"
        "    supported_modes: [sim, twin]\n"
        "    missing_sim_policy: stub\n"
        "    priority: 100\n",
        encoding="utf-8",
    )
    hints = read_pair_hints(tmp_path)
    assert len(hints) == 1
    assert hints[0]["real"] == "dalong_heaterstirrer"
    assert hints[0]["virtual"] == "community.dalong.virtual_heaterstirrer"
    assert hints[0]["pair_type"] == "mock"


def test_read_pair_hints_absent_returns_empty(tmp_path):
    assert read_pair_hints(tmp_path) == []


def test_read_pair_hints_malformed_returns_empty(tmp_path):
    (tmp_path / "unilab_simulation_pairs.yaml").write_text("pairs: [ : not valid", encoding="utf-8")
    assert read_pair_hints(tmp_path) == []


def test_read_pair_hints_skips_entries_without_real(tmp_path):
    (tmp_path / "unilab_simulation_pairs.yaml").write_text(
        "pairs:\n  - virtual: only_virtual\n  - real: good\n", encoding="utf-8"
    )
    hints = read_pair_hints(tmp_path)
    assert [h["real"] for h in hints] == ["good"]


def test_read_pair_hints_from_contract_fixture():
    hints = read_pair_hints(FIXTURES / "hint_pkg")
    assert hints[0]["real"] == "dalong_heaterstirrer"
    assert hints[0]["engine"] == "gazebo"
    assert hints[0]["twin_capability"]["throttle_hz"] == 20


def test_collect_all_pair_hints_dedups_by_real_virtual(tmp_path):
    pkg_a = tmp_path / "a"
    pkg_b = tmp_path / "b"
    pkg_a.mkdir()
    pkg_b.mkdir()
    (pkg_a / "unilab_simulation_pairs.yaml").write_text(
        "pairs:\n  - real: dev1\n    virtual: v1\n    priority: 100\n", encoding="utf-8"
    )
    (pkg_b / "unilab_simulation_pairs.yaml").write_text(
        "pairs:\n  - real: dev1\n    virtual: v1\n    priority: 5\n"  # duplicate (dev1,v1) -> dropped
        "  - real: dev2\n    virtual: v2\n",
        encoding="utf-8",
    )
    collected = collect_all_pair_hints([pkg_a, pkg_b])
    keys = {(h["real"], h.get("virtual")) for h in collected}
    assert keys == {("dev1", "v1"), ("dev2", "v2")}
    # first occurrence wins
    dev1 = next(h for h in collected if h["real"] == "dev1")
    assert dev1["priority"] == 100
