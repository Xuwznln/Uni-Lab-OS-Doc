from __future__ import annotations

from typing import Any

from unilabos.sim.context import get_runtime_context


def dispatch_device_command(entity_id: str, command: dict[str, Any]) -> bool:
    backend = get_runtime_context().physics
    if backend is None:
        return False
    backend.set_command(str(entity_id), dict(command))
    return True
