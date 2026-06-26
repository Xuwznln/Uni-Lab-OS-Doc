"""Discover external package registry directories (Plan 09 Task 1).

An external package may expose its Uni-Lab-OS registry YAML via:
1. ``pyproject.toml`` ``[tool.unilabos.registry] paths = [...]``
2. (future) entry point group ``unilabos.registry``
3. fallback ``unilabos_registry/`` directory at the package/repo root.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    import tomli as tomllib

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "unilabos.registry"


def discover_registry_paths_from_project(project_root: Path | str) -> list[Path]:
    root = Path(project_root).resolve()
    pyproject_paths = _read_pyproject_registry_paths(root)
    if pyproject_paths:
        return pyproject_paths

    fallback = root / "unilabos_registry"
    if fallback.is_dir():
        return [fallback]

    return []


def discover_registry_paths_from_entry_points() -> list[Path]:
    """Discover registry dirs from installed packages declaring the ``unilabos.registry``
    entry point group (Plan 09 §2.3 #2).

    Each entry point resolves to a callable returning a path or list of paths (e.g.
    ``my_package.unilabos_registry:registry_paths``); a non-callable path value is also
    accepted. Per-entry failures are isolated.
    """
    paths: list[Path] = []
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - importlib.metadata < 3.10 API
        eps = entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            obj = ep.load()
            result = obj() if callable(obj) else obj
            items = result if isinstance(result, (list, tuple)) else [result]
            for item in items:
                candidate = Path(item).resolve()
                if candidate.is_dir():
                    paths.append(candidate)
        except Exception as exc:  # noqa: BLE001 - one bad package must not abort discovery
            logger.warning("failed to load registry entry point %r: %s", getattr(ep, "name", ep), exc)
    # de-duplicate, order-preserving
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _read_pyproject_registry_paths(project_root: Path) -> list[Path]:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return []

    data = _load_toml(pyproject)
    registry_config = data.get("tool", {}).get("unilabos", {}).get("registry", {})
    raw_paths = registry_config.get("paths", [])
    if not isinstance(raw_paths, list):
        return []

    paths: list[Path] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str):
            continue
        registry_path = (project_root / raw_path).resolve()
        if registry_path.is_dir():
            paths.append(registry_path)
    return paths


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        data = tomllib.load(file)
    if not isinstance(data, dict):
        return {}
    return data
