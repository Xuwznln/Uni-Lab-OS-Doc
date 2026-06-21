"""M-3/M-4/M-5: edge orchestration (resolve -> generate -> init registry), offline-safe."""

import json
from pathlib import Path

from unilabos.registry.pair_registry import lookup, reset_pair_registry
from unilabos.sim.pairs.bundle import parse_bundle
from unilabos.sim.pairs.cache import PairCache, compute_graph_hash
from unilabos.sim.pairs.edge_setup import (
    collect_real_classes,
    setup_simulation_pairs,
    warn_missing_virtual_classes,
)
from unilabos.sim.pairs.generate import bundle_to_pairs_yaml

FIXTURES = Path(__file__).parent / "fixtures"
GRAPH = {"nodes": [
    {"id": "hs1", "class": "dalong_heaterstirrer"},
    {"id": "nmr1", "class": "qone_nmr"},
]}


def _ok_response():
    return {"code": 0, "data": json.loads((FIXTURES / "bundle_ok.json").read_text(encoding="utf-8"))}


class FakeClient:
    def __init__(self, response=None, raises=False):
        self._response = response
        self._raises = raises
        self.calls = 0

    def resolve_simulation_pairs(self, payload):
        self.calls += 1
        self.last_request = payload
        if self._raises:
            raise ConnectionError("backend down")
        return self._response


def test_collect_real_classes_from_node_link():
    assert collect_real_classes(GRAPH) == ["dalong_heaterstirrer", "qone_nmr"]


def test_setup_real_mode_returns_none(tmp_path):
    assert setup_simulation_pairs(graph=GRAPH, mode="real", http_client=FakeClient(), cache_dir=tmp_path) is None


def test_setup_resolves_and_points_registry(tmp_path):
    downloaded = []
    try:
        path = setup_simulation_pairs(
            graph=GRAPH, mode="sim", http_client=FakeClient(_ok_response()), cache_dir=tmp_path,
            downloader=lambda b: downloaded.append(len(b.pairs)),
        )
        assert path is not None and path.is_file()
        assert downloaded == [2]  # downloader invoked with the bundle
        assert lookup("dalong_heaterstirrer").virtual == "community.dalong.virtual_heaterstirrer"
        assert lookup("qone_nmr").missing_sim_policy == "fail"
        # cache written
        assert PairCache(tmp_path).load_manifest() is not None
    finally:
        reset_pair_registry()


def test_warn_missing_virtual_classes():
    bundle = parse_bundle(_ok_response()["data"])
    # registry has neither virtual; only dalong's virtual is non-null in fixture
    missing = warn_missing_virtual_classes(bundle, device_registry={})
    assert missing == ["community.dalong.virtual_heaterstirrer"]
    # when present, no missing
    assert warn_missing_virtual_classes(bundle, {"community.dalong.virtual_heaterstirrer": {}}) == []


def test_setup_passes_engine_into_resolve(tmp_path):
    client = FakeClient(_ok_response())
    try:
        setup_simulation_pairs(graph=GRAPH, mode="sim", http_client=client, cache_dir=tmp_path, engine="gazebo")
        assert client.last_request["engine"] == "gazebo"
    finally:
        reset_pair_registry()


def test_setup_offline_uses_compatible_cache(tmp_path):
    # pre-seed cache as if a previous online run happened
    b = parse_bundle(_ok_response()["data"])
    rc = ["dalong_heaterstirrer", "qone_nmr"]
    PairCache(tmp_path).write(b, bundle_to_pairs_yaml(b), lab_uuid=None, edge_uuid=None,
                              graph_hash=compute_graph_hash(rc), real_classes=rc)
    try:
        path = setup_simulation_pairs(graph=GRAPH, mode="sim", http_client=FakeClient(raises=True), cache_dir=tmp_path)
        assert path is not None and path.is_file()
        assert lookup("dalong_heaterstirrer").virtual == "community.dalong.virtual_heaterstirrer"
    finally:
        reset_pair_registry()


def test_setup_offline_no_cache_returns_none(tmp_path):
    try:
        path = setup_simulation_pairs(graph=GRAPH, mode="sim", http_client=FakeClient(raises=True), cache_dir=tmp_path)
        assert path is None  # caller keeps repository default device_pair.yaml
    finally:
        reset_pair_registry()
