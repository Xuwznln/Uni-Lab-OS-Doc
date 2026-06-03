from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from unilabos.sim.backends.isaac_bridge import IsaacBridgeBackend
from unilabos.sim.physics_backend import PhysicsBackend


class _RpcHandler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, format, *args):
        return

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.calls.append(payload)
        op = payload["op"]
        args = payload["args"]
        if op == "get_observation":
            result = {"entity_id": args["entity_id"], "tcp_pose": [1, 2, 3, 0, 0, 0]}
        elif op == "get_joint_states":
            result = {"joint_1": 1.0}
        elif op == "attach_rigid_body":
            result = "beaker"
        elif op == "render":
            result = {"encoding": "base64", "data": base64.b64encode(b"png-bytes").decode("ascii")}
        else:
            result = None
        body = json.dumps({"ok": True, "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server():
    _RpcHandler.calls = []
    server = HTTPServer(("127.0.0.1", 0), _RpcHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_isaac_bridge_satisfies_physics_protocol():
    assert isinstance(IsaacBridgeBackend("http://127.0.0.1:9"), PhysicsBackend)


def test_isaac_bridge_forwards_backend_methods_over_http():
    server, endpoint = _start_server()
    try:
        backend = IsaacBridgeBackend(endpoint)

        backend.load_scene("/tmp/lab.usd")
        backend.reset()
        backend.step(0.05)
        backend.set_command("arm", {"type": "move_j"})
        observation = backend.get_observation("arm")
        joints = backend.get_joint_states("arm")
        body_id = backend.attach_rigid_body("beaker", "beaker.usd", {"xyz": [0, 0, 0]})
        backend.apply_wrench("arm", {"force": [1, 0, 0]})
        image = backend.render("/World/Camera", 320, 240)

        assert observation["entity_id"] == "arm"
        assert joints == {"joint_1": 1.0}
        assert body_id == "beaker"
        assert image == b"png-bytes"
        assert [call["op"] for call in _RpcHandler.calls] == [
            "load_scene",
            "reset",
            "step",
            "set_command",
            "get_observation",
            "get_joint_states",
            "attach_rigid_body",
            "apply_wrench",
            "render",
        ]
    finally:
        server.shutdown()
