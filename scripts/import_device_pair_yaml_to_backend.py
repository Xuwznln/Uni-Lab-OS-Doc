#!/usr/bin/env python3
"""M-7: import the local device_pair.yaml into the Device Square backend.

Reads ``unilabos/registry/device_pair.yaml`` and creates ``simulation_driver_pair``
records via the backend admin API (POST /lab/square/admin/simulation-pairs). The
original YAML is never modified. Unmatched/failed entries are reported as diagnostics.

Usage:
    python scripts/import_device_pair_yaml_to_backend.py --dry-run
    python scripts/import_device_pair_yaml_to_backend.py            # actually calls backend
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

import yaml

DEFAULT_PAIR_FILE = Path("unilabos/registry/device_pair.yaml")
ADMIN_PATH = "/lab/square/admin/simulation-pairs"


def load_pairs(pair_file: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(pair_file.read_text(encoding="utf-8")) or {}
    return [dict(p) for p in raw.get("pairs", []) if isinstance(p, dict) and p.get("real")]


def to_admin_payload(pair: dict[str, Any]) -> dict[str, Any]:
    """Map a local device_pair.yaml entry to the backend admin payload (Plan 08 v2).

    Accepts both the v2 ``twin_capability`` block and the legacy
    ``twin_observed`` / ``twin_throttle_hz`` fields.
    """
    tc = pair.get("twin_capability")
    if isinstance(tc, dict):
        twin_capability = {
            "enabled": bool(tc.get("enabled", False)),
            "observed": list(tc.get("observed") or []),
            "throttle_hz": tc.get("throttle_hz", 10.0),
        }
    else:
        observed = list(pair.get("twin_observed") or [])
        twin_capability = {
            "enabled": bool(observed),
            "observed": observed,
            "throttle_hz": pair.get("twin_throttle_hz", 10.0),
        }
    return {
        "real_class": pair["real"],
        "virtual_class": pair.get("virtual"),
        "engine": pair.get("engine", "none"),
        "missing_sim_policy": pair.get("missing_sim_policy", "stub"),
        "twin_capability": twin_capability,
        "source_type": "package_hint",
        "status": "draft",
    }


def import_pairs(
    pairs: list[dict[str, Any]],
    create_pair_fn: Callable[[dict[str, Any]], dict[str, Any]] | None,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    created, failed = [], []
    for pair in pairs:
        payload = to_admin_payload(pair)
        if dry_run or create_pair_fn is None:
            created.append(payload["real_class"])
            continue
        try:
            create_pair_fn(payload)
            created.append(payload["real_class"])
        except Exception as exc:  # noqa: BLE001
            failed.append({"real": payload["real_class"], "error": str(exc)})
    return {"created": created, "failed": failed, "dry_run": dry_run}


def _default_create_fn():
    from unilabos.app.web.client import http_client

    def _create(payload: dict[str, Any]) -> dict[str, Any]:
        resp = http_client._session.post(
            f"{http_client.remote_addr}{ADMIN_PATH}",
            json=payload,
            headers={"Authorization": f"Lab {http_client.auth}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    return _create


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-file", type=Path, default=DEFAULT_PAIR_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    pairs = load_pairs(args.pair_file)
    create_fn = None if args.dry_run else _default_create_fn()
    result = import_pairs(pairs, create_fn, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
