"""M-2: resolve client (contract C-1), backend mocked."""

import json
from pathlib import Path

import pytest

from unilabos.sim.pairs.resolve_client import build_resolve_request, resolve_pairs

FIXTURES = Path(__file__).parent / "fixtures"


class FakeHTTPClient:
    def __init__(self, response):
        self._response = response
        self.last_request = None

    def resolve_simulation_pairs(self, payload):
        self.last_request = payload
        return self._response


def _ok_response():
    data = json.loads((FIXTURES / "bundle_ok.json").read_text(encoding="utf-8"))
    return {"code": 0, "data": data}


def test_build_resolve_request_sorts_and_dedups_classes():
    req = build_resolve_request(
        lab_uuid="L", edge_uuid="E", mode="sim",
        real_classes=["b", "a", "a"], unilabos_version="0.8.0",
    )
    assert req["real_classes"] == ["a", "b"]
    assert req["mode"] == "sim"
    assert req["package_locks"] == []


def test_resolve_pairs_returns_bundle():
    client = FakeHTTPClient(_ok_response())
    req = build_resolve_request(lab_uuid="L", edge_uuid="E", mode="sim", real_classes=["dalong_heaterstirrer"])
    bundle = resolve_pairs(client, req)
    assert len(bundle.pairs) == 2
    assert client.last_request["real_classes"] == ["dalong_heaterstirrer"]


def test_resolve_pairs_raises_on_error_code():
    client = FakeHTTPClient({"code": 1, "message": "boom"})
    with pytest.raises(RuntimeError):
        resolve_pairs(client, build_resolve_request(lab_uuid=None, edge_uuid=None, mode="sim", real_classes=[]))


def test_resolve_pairs_raises_on_invalid_bundle():
    bad = {"code": 0, "data": {"pairs": [{"real": "a", "missing_sim_policy": "bogus"}]}}
    with pytest.raises(ValueError):
        resolve_pairs(FakeHTTPClient(bad), build_resolve_request(lab_uuid=None, edge_uuid=None, mode="sim", real_classes=["a"]))
