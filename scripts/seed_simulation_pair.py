#!/usr/bin/env python3
"""Self-service seeding: create + activate one simulation_driver_pair in a Device
Square backend via the admin API (Plan 08 v2 §7.2). Lets us produce an active pair
for end-to-end testing WITHOUT waiting for a colleague to seed data.

Admin contract (reverse-engineered against the deployed test backend):
    POST /lab/square/admin/simulation-pairs          -> create (returns pair uuid)
    POST /lab/square/admin/simulation-pairs/{uuid}/activate
Required body field confirmed by the backend: `real_resource_template_uuid`
(template UUID, not class name). Obtain template UUIDs from the device square UI
or the registry-upload response.

Usage:
    python scripts/seed_simulation_pair.py \
        --real-template-uuid <uuid> --virtual-template-uuid <uuid> \
        --engine gazebo --missing-sim-policy stub --addr test --ak <ak> --sk <sk>
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable

CREATE_PATH = "/lab/square/admin/simulation-pairs"


def build_create_payload(
    *,
    real_template_uuid: str,
    virtual_template_uuid: str | None,
    engine: str,
    missing_sim_policy: str,
    priority: int,
    is_default: bool,
    twin_observed: list[str] | None,
    twin_throttle_hz: float,
) -> dict[str, Any]:
    twin = list(twin_observed or [])
    return {
        "real_resource_template_uuid": real_template_uuid,
        "virtual_resource_template_uuid": virtual_template_uuid,
        "engine": engine,
        "missing_sim_policy": missing_sim_policy,
        "priority": priority,
        "is_default": is_default,
        "status": "active",
        "twin_capability": {
            "enabled": bool(twin),
            "observed": twin,
            "throttle_hz": twin_throttle_hz,
        },
    }


def seed_pair(
    payload: dict[str, Any],
    create_fn: Callable[[dict[str, Any]], dict[str, Any]],
    activate_fn: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Create the pair, then (optionally) activate it. Returns a summary dict."""
    created = create_fn(payload)
    data = created.get("data", created) if isinstance(created, dict) else {}
    pair_uuid = data.get("uuid") or data.get("id")
    activated = None
    if pair_uuid and activate_fn is not None:
        activated = activate_fn(str(pair_uuid))
    return {"created": created, "pair_uuid": pair_uuid, "activated": activated}


def _default_fns(addr_remote: str | None):
    from unilabos.app.web.client import http_client

    base = addr_remote or http_client.remote_addr

    def _create(payload: dict[str, Any]) -> dict[str, Any]:
        resp = http_client._session.post(
            f"{base}{CREATE_PATH}",
            json=payload,
            headers={"Authorization": f"Lab {http_client.auth}"},
            timeout=30,
        )
        return resp.json()

    def _activate(uuid: str) -> dict[str, Any]:
        resp = http_client._session.post(
            f"{base}{CREATE_PATH}/{uuid}/activate",
            json={"is_default": True},
            headers={"Authorization": f"Lab {http_client.auth}"},
            timeout=30,
        )
        return resp.json()

    return _create, _activate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-template-uuid", required=True)
    parser.add_argument("--virtual-template-uuid", default=None)
    parser.add_argument("--engine", default="none")
    parser.add_argument("--missing-sim-policy", default="stub", choices=["stub", "skip", "fail"])
    parser.add_argument("--priority", type=int, default=100)
    parser.add_argument("--no-default", action="store_true")
    parser.add_argument("--twin-observed", nargs="*", default=None)
    parser.add_argument("--twin-throttle-hz", type=float, default=10.0)
    parser.add_argument("--remote-addr", default=None, help="Override backend API base (else http_client.remote_addr)")
    args = parser.parse_args(argv)

    payload = build_create_payload(
        real_template_uuid=args.real_template_uuid,
        virtual_template_uuid=args.virtual_template_uuid,
        engine=args.engine,
        missing_sim_policy=args.missing_sim_policy,
        priority=args.priority,
        is_default=not args.no_default,
        twin_observed=args.twin_observed,
        twin_throttle_hz=args.twin_throttle_hz,
    )
    create_fn, activate_fn = _default_fns(args.remote_addr)
    result = seed_pair(payload, create_fn, activate_fn)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("pair_uuid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
