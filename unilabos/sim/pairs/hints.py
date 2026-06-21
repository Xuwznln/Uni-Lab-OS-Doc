"""Read package-bundled simulation pair hints (contract C-2 / work package M-1).

A package may ship a ``unilab_simulation_pairs.yaml`` declaring recommended
real -> virtual driver pairs. These are *hints*, not the final relation: the
backend normalizes them into draft/active ``simulation_driver_pair`` records.
This module only reads them and prepares them for the registry upload payload.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

import yaml

logger = logging.getLogger(__name__)

HINTS_FILENAME = "unilab_simulation_pairs.yaml"


def read_pair_hints(package_dir: str | Path) -> list[dict[str, Any]]:
    """Return the pair hints declared in ``<package_dir>/unilab_simulation_pairs.yaml``.

    Returns an empty list when the file is absent or malformed (best-effort: a bad
    hints file must never break package upload).
    """
    path = Path(package_dir) / HINTS_FILENAME
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # noqa: BLE001
        logger.warning("invalid %s in %s: %s", HINTS_FILENAME, package_dir, exc)
        return []
    raw_pairs = data.get("pairs", [])
    if not isinstance(raw_pairs, list):
        return []
    hints: list[dict[str, Any]] = []
    for item in raw_pairs:
        if isinstance(item, dict) and item.get("real"):
            hints.append(dict(item))
    return hints


def collect_all_pair_hints(package_dirs: Iterable[str | Path]) -> list[dict[str, Any]]:
    """Aggregate hints across multiple package dirs, de-duplicated by (real, virtual).

    First occurrence wins (earlier package dirs take precedence).
    """
    seen: set[tuple[str, str | None]] = set()
    result: list[dict[str, Any]] = []
    for package_dir in package_dirs:
        for hint in read_pair_hints(package_dir):
            key = (str(hint.get("real")), hint.get("virtual"))
            if key in seen:
                continue
            seen.add(key)
            result.append(hint)
    return result
