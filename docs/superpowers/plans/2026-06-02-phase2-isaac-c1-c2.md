# Phase 2 Isaac C1 C2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the C1/C2 foundation for Route A by adding an in-process fake physics backend and an HTTP/JSON Isaac bridge client that implements the Uni-Lab-OS `PhysicsBackend` contract.

**Architecture:** C1 extends the existing `PhysicsBackend` protocol with scene loading and rendering, stores physics configuration on `RuntimeContext`, and adds a deterministic `FakePhysicsBackend` for unit and CLI wiring tests. C2 adds a small JSON protocol module plus `IsaacBridgeBackend`, which serializes every backend operation into HTTP requests so the edge process can talk to an Isaac worker without importing Isaac or ROS dependencies.

**Tech Stack:** Python 3.11, `typing.Protocol`, stdlib `urllib.request`, stdlib `json`, pytest.

---

## File Structure

- Create `unilabos/sim/backends/__init__.py`
  - Exposes physics backend implementations without importing Isaac-specific code.
- Create `unilabos/sim/backends/fake_physics.py`
  - Deterministic in-process `PhysicsBackend` for C1 tests and future `--physics fake`.
- Create `unilabos/sim/backends/isaac/__init__.py`
  - Package marker for Isaac IPC files.
- Create `unilabos/sim/backends/isaac/protocol.py`
  - Pure-Python HTTP/JSON request and response contract.
- Create `unilabos/sim/backends/isaac_bridge.py`
  - Edge-side `PhysicsBackend` client that calls a worker endpoint over HTTP.
- Create `tests/sim/backends/test_fake_physics.py`
  - Covers fake backend state, scene loading, command recording, observations, joint states, callbacks, and render bytes.
- Create `tests/sim/backends/test_isaac_protocol.py`
  - Covers JSON encoding and response decoding behavior.
- Create `tests/sim/backends/test_isaac_bridge.py`
  - Covers HTTP RPC methods against a local mock server.
- Modify `unilabos/sim/physics_backend.py`
  - Add `load_scene(scene_path: str) -> None` and `render(camera: str, width: int, height: int) -> bytes`.
- Modify `unilabos/sim/context.py`
  - Add `physics_backend_name`, `physics_endpoint`, and `physics_scene` to `RuntimeContext`.
- Modify `tests/sim/test_physics_backend.py`
  - Update the existing runtime protocol test double to satisfy the extended contract.
- Modify `tests/sim/test_context_and_clock.py`
  - Cover the new `RuntimeContext` physics configuration fields.

## Task 1: Extend PhysicsBackend Contract

**Files:**
- Modify: `unilabos/sim/physics_backend.py`
- Modify: `tests/sim/test_physics_backend.py`

- [ ] **Step 1: Write the failing protocol test**

Add these methods to `FakePhysics` in `tests/sim/test_physics_backend.py` and assert runtime protocol compatibility:

```python
    def load_scene(self, scene_path: str) -> None:
        self.scene_path = scene_path

    def render(self, camera: str, width: int, height: int) -> bytes:
        return f"{camera}:{width}x{height}".encode()


def test_physics_backend_scene_and_render_contract():
    backend = FakePhysics()
    backend.load_scene("/tmp/lab.usd")
    assert backend.scene_path == "/tmp/lab.usd"
    assert backend.render("/World/Camera", 320, 240) == b"/World/Camera:320x240"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/sim/test_physics_backend.py::test_physics_backend_scene_and_render_contract -q`

Expected: FAIL until `PhysicsBackend` declares `load_scene` and `render`, or runtime compatibility fails for a class missing those methods.

- [ ] **Step 3: Extend the protocol**

Add this to `PhysicsBackend`:

```python
    def load_scene(self, scene_path: str) -> None:
        ...

    def render(self, camera: str, width: int, height: int) -> bytes:
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/sim/test_physics_backend.py -q`

Expected: PASS.

- [ ] **Step 5: Commit point**

Commit message: `feat(sim): extend physics backend contract`

## Task 2: Add RuntimeContext Physics Configuration

**Files:**
- Modify: `unilabos/sim/context.py`
- Modify: `tests/sim/test_context_and_clock.py`

- [ ] **Step 1: Write the failing context test**

Add this test to `tests/sim/test_context_and_clock.py`:

```python
def test_runtime_context_stores_physics_configuration():
    ctx = RuntimeContext(
        mode="sim",
        physics_backend_name="isaac",
        physics_endpoint="http://127.0.0.1:8091",
        physics_scene="/tmp/lab.usd",
    )

    assert ctx.physics_backend_name == "isaac"
    assert ctx.physics_endpoint == "http://127.0.0.1:8091"
    assert ctx.physics_scene == "/tmp/lab.usd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/sim/test_context_and_clock.py::test_runtime_context_stores_physics_configuration -q`

Expected: FAIL with unexpected keyword argument before fields are added.

- [ ] **Step 3: Add dataclass fields**

Add these fields to `RuntimeContext`:

```python
    physics_backend_name: str = "none"
    physics_endpoint: Optional[str] = None
    physics_scene: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/sim/test_context_and_clock.py::test_runtime_context_stores_physics_configuration -q`

Expected: PASS.

- [ ] **Step 5: Commit point**

Commit message: `feat(sim): record physics backend configuration`

## Task 3: Add FakePhysicsBackend

**Files:**
- Create: `unilabos/sim/backends/__init__.py`
- Create: `unilabos/sim/backends/fake_physics.py`
- Create: `tests/sim/backends/test_fake_physics.py`

- [ ] **Step 1: Write failing fake backend tests**

Create `tests/sim/backends/test_fake_physics.py`:

```python
from unilabos.sim.backends.fake_physics import FakePhysicsBackend
from unilabos.sim.physics_backend import PhysicsBackend


def test_fake_backend_satisfies_physics_protocol():
    assert isinstance(FakePhysicsBackend(), PhysicsBackend)


def test_fake_backend_records_scene_commands_and_steps():
    backend = FakePhysicsBackend()
    backend.load_scene("/tmp/lab.usd")
    backend.set_command("arm", {"type": "move_j", "joint_positions": [1.0, 2.0]})
    backend.step(0.05)

    assert backend.scene_path == "/tmp/lab.usd"
    assert backend.commands["arm"] == {"type": "move_j", "joint_positions": [1.0, 2.0]}
    assert backend.sim_time == 0.05
    assert backend.get_observation("arm")["last_command"]["type"] == "move_j"


def test_fake_backend_tracks_joint_states_and_rigid_bodies():
    backend = FakePhysicsBackend()
    backend.set_joint_states("arm", {"joint_1": 1.25, "joint_2": -0.5})
    body_id = backend.attach_rigid_body("beaker", "beaker.usd", {"xyz": [0, 0, 0]})

    assert body_id == "beaker"
    assert backend.get_joint_states("arm") == {"joint_1": 1.25, "joint_2": -0.5}
    assert backend.get_observation("beaker")["asset_path"] == "beaker.usd"


def test_fake_backend_render_returns_png_like_bytes():
    backend = FakePhysicsBackend()
    image = backend.render("/World/Camera", 320, 240)

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"/World/Camera" in image


def test_fake_backend_contact_callback_receives_applied_wrench_event():
    backend = FakePhysicsBackend()
    events = []
    backend.register_contact_callback(events.append)

    backend.apply_wrench("arm", {"force": [1, 0, 0]})

    assert events == [{"type": "wrench", "body_id": "arm", "wrench": {"force": [1, 0, 0]}}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sim/backends/test_fake_physics.py -q`

Expected: FAIL with module import error before `FakePhysicsBackend` exists.

- [ ] **Step 3: Implement fake backend**

Create `unilabos/sim/backends/__init__.py`:

```python
"""Simulation physics backend implementations."""
```

Create `unilabos/sim/backends/fake_physics.py` with:

```python
from __future__ import annotations

from typing import Any, Callable


class FakePhysicsBackend:
    name = "fake"

    def __init__(self) -> None:
        self.scene_path: str | None = None
        self.sim_time = 0.0
        self.commands: dict[str, dict[str, Any]] = {}
        self.observations: dict[str, dict[str, Any]] = {}
        self.joint_states: dict[str, dict[str, float]] = {}
        self.rigid_bodies: dict[str, dict[str, Any]] = {}
        self.wrenches: list[tuple[str, dict[str, Any]]] = []
        self._contact_callbacks: list[Callable[[dict[str, Any]], None]] = []

    def reset(self) -> None:
        self.sim_time = 0.0
        self.commands.clear()
        self.observations.clear()
        self.joint_states.clear()
        self.wrenches.clear()

    def step(self, dt: float) -> None:
        self.sim_time += float(dt)

    def load_scene(self, scene_path: str) -> None:
        self.scene_path = str(scene_path)

    def get_observation(self, entity_id: str) -> dict[str, Any]:
        observation = dict(self.observations.get(entity_id, {}))
        if entity_id in self.commands:
            observation["last_command"] = dict(self.commands[entity_id])
        if entity_id in self.joint_states:
            observation["joint_positions"] = list(self.joint_states[entity_id].values())
            observation["joint_states"] = dict(self.joint_states[entity_id])
        if entity_id in self.rigid_bodies:
            observation.update(self.rigid_bodies[entity_id])
        observation.setdefault("entity_id", entity_id)
        observation.setdefault("sim_time", self.sim_time)
        return observation

    def set_observation(self, entity_id: str, observation: dict[str, Any]) -> None:
        self.observations[entity_id] = dict(observation)

    def set_command(self, entity_id: str, command: dict[str, Any]) -> None:
        self.commands[entity_id] = dict(command)

    def attach_rigid_body(self, name: str, asset_path: str, pose: dict[str, Any]) -> str:
        body_id = str(name)
        self.rigid_bodies[body_id] = {"name": str(name), "asset_path": str(asset_path), "pose": dict(pose)}
        return body_id

    def set_joint_states(self, body_id: str, joints: dict[str, float]) -> None:
        self.joint_states[body_id] = {str(key): float(value) for key, value in joints.items()}

    def get_joint_states(self, body_id: str) -> dict[str, float]:
        return dict(self.joint_states.get(body_id, {}))

    def apply_wrench(self, body_id: str, wrench: dict[str, Any]) -> None:
        payload = dict(wrench)
        self.wrenches.append((body_id, payload))
        event = {"type": "wrench", "body_id": body_id, "wrench": payload}
        for callback in list(self._contact_callbacks):
            callback(event)

    def register_contact_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._contact_callbacks.append(callback)

    def render(self, camera: str, width: int, height: int) -> bytes:
        meta = f"fake-render camera={camera} width={int(width)} height={int(height)}".encode()
        return b"\x89PNG\r\n\x1a\n" + meta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sim/backends/test_fake_physics.py -q`

Expected: PASS.

- [ ] **Step 5: Commit point**

Commit message: `feat(sim): add fake physics backend`

## Task 4: Add Isaac HTTP Protocol Helpers

**Files:**
- Create: `unilabos/sim/backends/isaac/__init__.py`
- Create: `unilabos/sim/backends/isaac/protocol.py`
- Create: `tests/sim/backends/test_isaac_protocol.py`

- [ ] **Step 1: Write failing protocol tests**

Create `tests/sim/backends/test_isaac_protocol.py`:

```python
import pytest

from unilabos.sim.backends.isaac.protocol import decode_response, encode_request


def test_encode_request_builds_compact_json_payload():
    payload = encode_request("set_command", {"entity_id": "arm", "command": {"type": "move_j"}})

    assert payload == b'{"op":"set_command","args":{"entity_id":"arm","command":{"type":"move_j"}}}'


def test_decode_response_returns_result_for_ok_payload():
    assert decode_response(b'{"ok":true,"result":{"joint_1":1.0}}') == {"joint_1": 1.0}


def test_decode_response_raises_for_worker_error():
    with pytest.raises(RuntimeError, match="Isaac worker RPC failed: bad scene"):
        decode_response(b'{"ok":false,"error":"bad scene"}')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sim/backends/test_isaac_protocol.py -q`

Expected: FAIL with module import error before protocol helpers exist.

- [ ] **Step 3: Implement protocol helpers**

Create `unilabos/sim/backends/isaac/__init__.py`:

```python
"""Isaac Sim bridge protocol package."""
```

Create `unilabos/sim/backends/isaac/protocol.py`:

```python
from __future__ import annotations

import json
from typing import Any


def encode_request(op: str, args: dict[str, Any] | None = None) -> bytes:
    payload = {"op": str(op), "args": dict(args or {})}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_response(data: bytes) -> Any:
    payload = json.loads(data.decode("utf-8"))
    if not payload.get("ok", False):
        error = payload.get("error", "unknown error")
        raise RuntimeError(f"Isaac worker RPC failed: {error}")
    return payload.get("result")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sim/backends/test_isaac_protocol.py -q`

Expected: PASS.

- [ ] **Step 5: Commit point**

Commit message: `feat(sim): add isaac bridge JSON protocol`

## Task 5: Add IsaacBridgeBackend HTTP Client

**Files:**
- Create: `unilabos/sim/backends/isaac_bridge.py`
- Create: `tests/sim/backends/test_isaac_bridge.py`

- [ ] **Step 1: Write failing HTTP bridge tests**

Create `tests/sim/backends/test_isaac_bridge.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sim/backends/test_isaac_bridge.py -q`

Expected: FAIL with module import error before `IsaacBridgeBackend` exists.

- [ ] **Step 3: Implement HTTP bridge**

Create `unilabos/sim/backends/isaac_bridge.py`:

```python
from __future__ import annotations

import base64
from typing import Any, Callable
from urllib import request
from urllib.error import HTTPError, URLError

from unilabos.sim.backends.isaac.protocol import decode_response, encode_request


class IsaacBridgeBackend:
    name = "isaac"

    def __init__(self, endpoint: str, timeout: float = 5.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = float(timeout)

    def _rpc(self, op: str, args: dict[str, Any] | None = None) -> Any:
        req = request.Request(
            f"{self.endpoint}/rpc",
            data=encode_request(op, args),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return decode_response(response.read())
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Isaac worker HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Isaac worker unavailable at {self.endpoint}: {exc.reason}") from exc

    def reset(self) -> None:
        self._rpc("reset")

    def step(self, dt: float) -> None:
        self._rpc("step", {"dt": float(dt)})

    def load_scene(self, scene_path: str) -> None:
        self._rpc("load_scene", {"scene_path": str(scene_path)})

    def get_observation(self, entity_id: str) -> dict[str, Any]:
        return dict(self._rpc("get_observation", {"entity_id": str(entity_id)}) or {})

    def set_command(self, entity_id: str, command: dict[str, Any]) -> None:
        self._rpc("set_command", {"entity_id": str(entity_id), "command": dict(command)})

    def attach_rigid_body(self, name: str, asset_path: str, pose: dict[str, Any]) -> str:
        result = self._rpc(
            "attach_rigid_body",
            {"name": str(name), "asset_path": str(asset_path), "pose": dict(pose)},
        )
        return str(result)

    def get_joint_states(self, body_id: str) -> dict[str, float]:
        result = self._rpc("get_joint_states", {"body_id": str(body_id)}) or {}
        return {str(key): float(value) for key, value in dict(result).items()}

    def apply_wrench(self, body_id: str, wrench: dict[str, Any]) -> None:
        self._rpc("apply_wrench", {"body_id": str(body_id), "wrench": dict(wrench)})

    def register_contact_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        raise NotImplementedError("IsaacBridgeBackend does not support edge-side contact callbacks yet")

    def render(self, camera: str, width: int, height: int) -> bytes:
        result = self._rpc("render", {"camera": str(camera), "width": int(width), "height": int(height)})
        if isinstance(result, dict) and result.get("encoding") == "base64":
            return base64.b64decode(str(result.get("data", "")))
        if isinstance(result, str):
            return base64.b64decode(result)
        raise TypeError(f"Isaac render returned unsupported payload: {type(result).__name__}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sim/backends/test_isaac_bridge.py -q`

Expected: PASS.

- [ ] **Step 5: Commit point**

Commit message: `feat(sim): add isaac HTTP physics bridge`

## Task 6: Run C1/C2 Regression Suite on 4090

**Files:**
- No source files modified in this task.

- [ ] **Step 1: Sync current branch to 4090**

Use git push/pull if the branch is remote-ready. If not, copy only these changed files to the matching 4090 checkout:

```bash
docs/superpowers/plans/2026-06-02-phase2-isaac-c1-c2.md
unilabos/sim/physics_backend.py
unilabos/sim/context.py
unilabos/sim/backends/__init__.py
unilabos/sim/backends/fake_physics.py
unilabos/sim/backends/isaac/__init__.py
unilabos/sim/backends/isaac/protocol.py
unilabos/sim/backends/isaac_bridge.py
tests/sim/test_physics_backend.py
tests/sim/test_context_and_clock.py
tests/sim/backends/test_fake_physics.py
tests/sim/backends/test_isaac_protocol.py
tests/sim/backends/test_isaac_bridge.py
```

- [ ] **Step 2: Run targeted C1/C2 tests on 4090**

Run in `conda activate unilab`:

```bash
pytest tests/sim/test_physics_backend.py \
  tests/sim/test_context_and_clock.py \
  tests/sim/backends/test_fake_physics.py \
  tests/sim/backends/test_isaac_protocol.py \
  tests/sim/backends/test_isaac_bridge.py -q
```

Expected: PASS.

- [ ] **Step 3: Run Phase 1/3 regression slice on 4090**

Run:

```bash
pytest tests/sim tests/queries tests/integration -q
```

Expected: PASS, preserving the previous 98-test baseline plus the new C1/C2 tests.

- [ ] **Step 4: Commit point**

Commit message: `test(sim): cover phase2 isaac bridge c1 c2`

## Self Review

- Spec coverage: C1 fake backend, protocol extension, runtime physics config, C2 JSON protocol, and C2 HTTP bridge are all covered by tasks.
- Placeholder scan: The plan contains concrete paths, commands, expected outcomes, and code snippets for each implementation step.
- Type consistency: The backend method names match `PhysicsBackend`: `reset`, `step`, `load_scene`, `get_observation`, `set_command`, `attach_rigid_body`, `get_joint_states`, `apply_wrench`, `register_contact_callback`, and `render`.
