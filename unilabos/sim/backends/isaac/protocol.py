from __future__ import annotations

import json
from typing import Any


def encode_request(op: str, args: dict[str, Any] | None = None) -> bytes:
    payload = {"op": str(op), "args": dict(args or {})}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_request(data: bytes) -> tuple[str, dict[str, Any]]:
    payload = json.loads(data.decode("utf-8"))
    op = payload.get("op")
    if not op:
        raise ValueError("RPC request missing op")
    args = payload.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("RPC request args must be an object")
    return str(op), dict(args)


def encode_response(result: Any = None) -> bytes:
    return json.dumps({"ok": True, "result": result}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode_error(error: str) -> bytes:
    return json.dumps({"ok": False, "error": str(error)}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_response(data: bytes) -> Any:
    payload = json.loads(data.decode("utf-8"))
    if not payload.get("ok", False):
        error = payload.get("error", "unknown error")
        raise RuntimeError(f"Isaac worker RPC failed: {error}")
    return payload.get("result")
