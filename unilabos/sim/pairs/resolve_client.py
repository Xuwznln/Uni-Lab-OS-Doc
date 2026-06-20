"""Edge resolve client (contract C-1 / work package M-2).

Builds the resolve request from graph real classes and calls the Device Square
Edge resolve API, returning a typed PairBundle. Offline/cache fallback lives in
``cache.py`` / ``edge_setup.py``; this module only does the live call + parse.
"""

from __future__ import annotations

from typing import Any

from unilabos.sim.pairs.bundle import PairBundle, parse_bundle, validate_bundle


def build_resolve_request(
    *,
    lab_uuid: str | None,
    edge_uuid: str | None,
    mode: str,
    real_classes: list[str],
    package_locks: list[dict[str, Any]] | None = None,
    unilabos_version: str | None = None,
) -> dict[str, Any]:
    return {
        "lab_uuid": lab_uuid,
        "edge_uuid": edge_uuid,
        "mode": mode,
        "real_classes": sorted(set(real_classes)),
        "package_locks": package_locks or [],
        "unilabos_version": unilabos_version,
    }


def resolve_pairs(http_client, request: dict[str, Any]) -> PairBundle:
    """Call the resolve API via the HTTP client and parse the bundle.

    ``http_client.resolve_simulation_pairs(request)`` must return the parsed JSON
    response dict ``{"code": 0, "data": {...}}``.
    """
    resp = http_client.resolve_simulation_pairs(request)
    if not isinstance(resp, dict):
        raise ValueError("resolve response must be a dict")
    if resp.get("code", 0) != 0:
        raise RuntimeError(f"resolve failed: code={resp.get('code')} msg={resp.get('message')}")
    bundle = parse_bundle(resp.get("data") or {})
    errors = validate_bundle(bundle)
    if errors:
        raise ValueError("invalid pair bundle: " + "; ".join(errors))
    return bundle
