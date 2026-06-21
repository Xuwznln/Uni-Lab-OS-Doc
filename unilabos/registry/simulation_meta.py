"""Virtual driver self-marking metadata (Plan 08 §6.3 / §6.3.1).

These four optional fields are part of a driver's own execution contract (NOT the
real<->virtual pairing, which lives in `simulation_driver_pair`). They let the
Device Square backend identify virtual drivers by field, not by name prefix.

All fields are optional and additive; a real device that declares nothing stays
exactly as before (`driver_runtime_kind` defaults to "real" and is NOT emitted).
"""

from __future__ import annotations

from typing import Any

# Order matters only for stable output; backend reads by key (Plan 08 v2 §6.3).
SIMULATION_META_KEYS = (
    "driver_runtime_kind",  # real / virtual
    "virtual_driver_kind",  # null_stub / local_mock / engine_adapter / remote_adapter / recorded_replay
    "sim_engine",           # none / isaac / gazebo / genesis / matterix / custom
)

DEFAULT_DRIVER_RUNTIME_KIND = "real"


def device_simulation_meta(
    driver_runtime_kind: str = DEFAULT_DRIVER_RUNTIME_KIND,
    virtual_driver_kind: str | None = None,
    sim_engine: str | None = None,
) -> dict[str, Any]:
    """Build the simulation meta dict used by the @device decorator's base_meta."""
    return {
        "driver_runtime_kind": driver_runtime_kind or DEFAULT_DRIVER_RUNTIME_KIND,
        "virtual_driver_kind": virtual_driver_kind,
        "sim_engine": sim_engine,
    }


def apply_simulation_meta(entry: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Copy non-empty simulation marks from *source* (ast_meta) into *entry* (upload payload).

    A real device (driver_runtime_kind == "real") emits nothing, keeping existing
    real-device payloads byte-for-byte unchanged.
    """
    for key in SIMULATION_META_KEYS:
        value = source.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "driver_runtime_kind" and value == DEFAULT_DRIVER_RUNTIME_KIND:
            continue
        entry[key] = value
    return entry
