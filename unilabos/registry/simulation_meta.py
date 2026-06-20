"""Virtual driver self-marking metadata (Plan 08 §6.3 / §6.3.1).

These four optional fields are part of a driver's own execution contract (NOT the
real<->virtual pairing, which lives in `simulation_driver_pair`). They let the
Device Square backend identify virtual drivers by field, not by name prefix.

All fields are optional and additive; a real device that declares nothing stays
exactly as before (`driver_runtime_kind` defaults to "real" and is NOT emitted).
"""

from __future__ import annotations

from typing import Any

# Order matters only for stable output; backend reads by key.
SIMULATION_META_KEYS = (
    "driver_runtime_kind",  # real / virtual
    "simulation_kind",      # stub / mock / physics / digital_twin / engine_adapter
    "supported_modes",      # ["sim"] / ["sim", "twin"]
    "sim_engine",           # none / gazebo / isaac / genesis / ...
)

DEFAULT_DRIVER_RUNTIME_KIND = "real"


def device_simulation_meta(
    driver_runtime_kind: str = DEFAULT_DRIVER_RUNTIME_KIND,
    simulation_kind: str | None = None,
    supported_modes: list[str] | None = None,
    sim_engine: str | None = None,
) -> dict[str, Any]:
    """Build the simulation meta dict used by the @device decorator's base_meta."""
    return {
        "driver_runtime_kind": driver_runtime_kind or DEFAULT_DRIVER_RUNTIME_KIND,
        "simulation_kind": simulation_kind,
        "supported_modes": supported_modes or [],
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
