"""Transport-agnostic dispatch over QueryService.

Both the ROS2 service node and any future gRPC/HTTP server reuse this single
``{op, args} -> {ok, result|error}`` boundary so the six query operations are
serialized identically regardless of transport.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from unilabos.api.query_service import QueryService
from unilabos.queries.engine import QueryNotFound

QUERY_OPS = (
    "query_pose",
    "query_state",
    "query_affordance",
    "query_action_schema",
    "query_safety_zones",
    "query_verification",
)


def dispatch(service: QueryService, op: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Call one query op by name with kwargs. Never raises; errors come back as dicts."""
    args = dict(args or {})
    if op not in QUERY_OPS:
        return {"ok": False, "op": op, "error": f"unknown op: {op}", "code": "unknown_op"}
    method = getattr(service, op)
    try:
        result = method(**args)
        return {"ok": True, "op": op, "result": result}
    except QueryNotFound as exc:
        return {"ok": False, "op": op, "error": str(exc), "code": "not_found"}
    except TypeError as exc:
        return {"ok": False, "op": op, "error": str(exc), "code": "bad_args"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "op": op, "error": str(exc), "code": "error"}


def dispatch_json(service: QueryService, command: str) -> str:
    """Parse a JSON command ``{"op": ..., "args": {...}}`` and return a JSON response."""
    try:
        payload = json.loads(command) if command else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"ok": False, "error": f"invalid json: {exc}", "code": "bad_json"})
    op = payload.get("op", "")
    args = payload.get("args", {})
    return json.dumps(dispatch(service, op, args), ensure_ascii=False)
