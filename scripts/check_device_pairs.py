#!/usr/bin/env python3
"""Report devices that still fall back to NullDeviceStub in sim mode."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml


def collect_registry_devices(registry_dir: Path) -> set[str]:
    devices = set()
    for path in sorted((registry_dir / "devices").glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        devices.update(str(key) for key in raw.keys())
    return devices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-dir", type=Path, default=Path("unilabos/registry"))
    parser.add_argument("--pair-file", type=Path, default=Path("unilabos/registry/device_pair.yaml"))
    parser.add_argument("--fail-on-stub", action="store_true")
    args = parser.parse_args(argv)

    devices = collect_registry_devices(args.registry_dir)
    raw = yaml.safe_load(args.pair_file.read_text(encoding="utf-8")) or {}
    pair_map = {item["real"]: item for item in raw.get("pairs", [])}
    stubbed = sorted(
        name
        for name in devices
        if name not in pair_map or (pair_map[name].get("virtual") is None and pair_map[name].get("missing_sim_policy", "stub") == "stub")
    )
    print(f"registry_devices={len(devices)}")
    print(f"pair_entries={len(pair_map)}")
    print(f"stub_fallbacks={len(stubbed)}")
    for name in stubbed:
        print(f"STUB {name}")
    return 2 if args.fail_on_stub and stubbed else 0


if __name__ == "__main__":
    sys.exit(main())
