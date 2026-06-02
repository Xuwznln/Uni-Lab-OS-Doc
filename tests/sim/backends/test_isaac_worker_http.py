from __future__ import annotations

import json
import threading
from urllib import request
from urllib.error import HTTPError

import pytest

from unilabos.sim.backends.isaac.worker_http import ThreadingHTTPServer, make_handler


class FakeWorkerState:
    def __init__(self):
        self.calls = []

    def health(self):
        return {"ok": True, "backend": "fake_isaac_worker"}

    def dispatch(self, op, args):
        self.calls.append((op, args))
        if op == "explode":
            raise RuntimeError("boom")
        return {"op": op, "args": args}


def _start(state):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _post(endpoint, payload):
    req = request.Request(
        f"{endpoint}/rpc",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=2.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_worker_health_endpoint():
    server, endpoint = _start(FakeWorkerState())
    try:
        with request.urlopen(f"{endpoint}/health", timeout=2.0) as resp:
            assert json.loads(resp.read().decode("utf-8")) == {"ok": True, "backend": "fake_isaac_worker"}
    finally:
        server.shutdown()


def test_worker_rpc_dispatches_request():
    state = FakeWorkerState()
    server, endpoint = _start(state)
    try:
        payload = _post(endpoint, {"op": "step", "args": {"dt": 0.05}})

        assert payload == {"ok": True, "result": {"op": "step", "args": {"dt": 0.05}}}
        assert state.calls == [("step", {"dt": 0.05})]
    finally:
        server.shutdown()


def test_worker_rpc_returns_json_error_for_exception():
    server, endpoint = _start(FakeWorkerState())
    try:
        with pytest.raises(HTTPError) as exc:
            _post(endpoint, {"op": "explode", "args": {}})

        assert exc.value.code == 500
        body = json.loads(exc.value.read().decode("utf-8"))
        assert body == {"ok": False, "error": "boom"}
    finally:
        server.shutdown()
