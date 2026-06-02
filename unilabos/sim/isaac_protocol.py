from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional


def build_envelope(
    *,
    msg_type: str,
    payload: Dict[str, Any],
    session_id: str,
    source: str = "edge",
    target: str = "isaac-sim-main",
    trace_id: Optional[str] = None,
    need_ack: Optional[bool] = None,
    sequence: Optional[int] = None,
    error: Optional[Dict[str, Any]] = None,
    spec_version: str = "1.0.0",
) -> Dict[str, Any]:
    """Build Edge<->Isaac message envelope defined by product spec 16."""
    msg: Dict[str, Any] = {
        "spec_version": spec_version,
        "msg_id": str(uuid.uuid4()),
        "msg_type": msg_type,
        "timestamp_ms": int(time.time() * 1000),
        "session_id": session_id,
        "source": source,
        "target": target,
        "payload": payload,
        "error": error,
    }
    if trace_id is not None:
        msg["trace_id"] = trace_id
    if need_ack is not None:
        msg["need_ack"] = need_ack
    if sequence is not None:
        msg["sequence"] = sequence
    return msg

