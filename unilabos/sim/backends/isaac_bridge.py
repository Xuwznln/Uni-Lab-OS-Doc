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
        except TimeoutError as exc:
            raise RuntimeError(f"Isaac worker timed out after {self.timeout:.1f}s during {op}") from exc

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
