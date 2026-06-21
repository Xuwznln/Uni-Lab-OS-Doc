"""M-5: pair bundle cache + manifest + offline compatibility."""

import json
from pathlib import Path

from unilabos.sim.pairs.bundle import parse_bundle
from unilabos.sim.pairs.cache import PairCache, compute_graph_hash
from unilabos.sim.pairs.generate import bundle_to_pairs_yaml

FIXTURES = Path(__file__).parent / "fixtures"


def _bundle():
    return parse_bundle(json.loads((FIXTURES / "bundle_ok.json").read_text(encoding="utf-8")))


def test_write_and_load_manifest(tmp_path):
    cache = PairCache(tmp_path)
    b = _bundle()
    rc = ["dalong_heaterstirrer", "qone_nmr"]
    path = cache.write(b, bundle_to_pairs_yaml(b), lab_uuid="L", edge_uuid="E",
                       graph_hash=compute_graph_hash(rc), real_classes=rc)
    assert path.is_file()
    m = cache.load_manifest()
    assert m["lab_uuid"] == "L"
    assert set(m["real_classes"]) == set(rc)


def test_is_compatible_exact_hash(tmp_path):
    cache = PairCache(tmp_path)
    b = _bundle()
    rc = ["dalong_heaterstirrer", "qone_nmr"]
    gh = compute_graph_hash(rc)
    cache.write(b, bundle_to_pairs_yaml(b), lab_uuid=None, edge_uuid=None, graph_hash=gh, real_classes=rc)
    assert cache.is_compatible(gh, rc) is True


def test_is_compatible_subset(tmp_path):
    cache = PairCache(tmp_path)
    b = _bundle()
    rc = ["dalong_heaterstirrer", "qone_nmr"]
    cache.write(b, bundle_to_pairs_yaml(b), lab_uuid=None, edge_uuid=None,
                graph_hash=compute_graph_hash(rc), real_classes=rc)
    # requesting a subset of cached classes -> compatible
    assert cache.is_compatible(compute_graph_hash(["dalong_heaterstirrer"]), ["dalong_heaterstirrer"]) is True


def test_is_incompatible_when_extra_class(tmp_path):
    cache = PairCache(tmp_path)
    b = _bundle()
    rc = ["dalong_heaterstirrer"]
    cache.write(b, bundle_to_pairs_yaml(b), lab_uuid=None, edge_uuid=None,
                graph_hash=compute_graph_hash(rc), real_classes=rc)
    assert cache.is_compatible(compute_graph_hash(["dalong_heaterstirrer", "new_dev"]),
                               ["dalong_heaterstirrer", "new_dev"]) is False


def test_is_incompatible_when_no_cache(tmp_path):
    assert PairCache(tmp_path).is_compatible("sha256:x", ["a"]) is False
