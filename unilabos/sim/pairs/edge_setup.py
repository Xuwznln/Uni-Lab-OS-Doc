"""Edge orchestration: resolve cloud pairs -> download virtual pkgs -> generate YAML
-> point PairRegistry at it (work packages M-3/M-4/M-5; Plan §10.1).

Called once at Edge startup in sim/twin mode. Offline-safe: falls back to a
compatible cache, then to the repository default device_pair.yaml.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Iterable

from unilabos.registry.pair_registry import init_pair_registry
from unilabos.sim.pairs.bundle import PairBundle
from unilabos.sim.pairs.cache import PairCache, compute_graph_hash
from unilabos.sim.pairs.generate import bundle_to_pairs_yaml
from unilabos.sim.pairs.resolve_client import build_resolve_request, resolve_pairs

logger = logging.getLogger(__name__)

# downloader: given a bundle, ensure virtual packages are installed/mounted (M-3).
Downloader = Callable[[PairBundle], None]


def collect_real_classes(graph: Any) -> list[str]:
    """Extract device class names from a node-link graph dict (or nx-like)."""
    classes: list[str] = []
    nodes: Iterable[Any]
    if isinstance(graph, dict):
        nodes = graph.get("nodes", [])
    elif hasattr(graph, "nodes"):
        try:
            nodes = [data for _, data in graph.nodes(data=True)]  # networkx
        except TypeError:
            nodes = list(graph.nodes)
    else:
        nodes = graph or []
    for node in nodes:
        cls = node.get("class") if isinstance(node, dict) else None
        if isinstance(cls, str) and cls:
            classes.append(cls)
    return sorted(set(classes))


def warn_missing_virtual_classes(bundle: PairBundle, device_registry: dict[str, Any]) -> list[str]:
    """Warn for bundle virtual classes not present in the device registry (Plan §10.4 step 4).

    Returns the list of missing virtual class names (also useful for tests).
    """
    missing: list[str] = []
    for entry in bundle.pairs:
        if entry.virtual and entry.virtual not in device_registry:
            missing.append(entry.virtual)
            logger.warning(
                "simulation pairs: virtual class '%s' (for real '%s') not found in registry; "
                "install its package or sim will fall back to stub/policy",
                entry.virtual, entry.real,
            )
    return missing


def setup_simulation_pairs(
    *,
    graph: Any,
    mode: str,
    http_client: Any,
    cache_dir: str | Path,
    engine: str = "none",
    lab_uuid: str | None = None,
    edge_uuid: str | None = None,
    unilabos_version: str | None = None,
    package_locks: list[dict[str, Any]] | None = None,
    downloader: Downloader | None = None,
    device_registry: dict[str, Any] | None = None,
) -> Path | None:
    """Returns the device_pair.yaml path that PairRegistry was pointed at, or None
    (caller then keeps the repository default device_pair.yaml)."""
    if mode not in ("sim", "twin"):
        return None

    real_classes = collect_real_classes(graph)
    if not real_classes:
        logger.info("simulation pairs: no device classes in graph; using default device_pair.yaml")
        return None

    cache = PairCache(cache_dir)
    graph_hash = compute_graph_hash(real_classes)

    try:
        request = build_resolve_request(
            lab_uuid=lab_uuid, edge_uuid=edge_uuid, mode=mode, engine=engine,
            real_classes=real_classes, package_locks=package_locks, unilabos_version=unilabos_version,
        )
        bundle = resolve_pairs(http_client, request)
        if not any(getattr(p, "virtual", None) for p in bundle.pairs):
            logger.info("simulation pairs: backend returned no usable virtual driver; falling back to repository default device_pair.yaml")
            return None
        if downloader is not None:
            downloader(bundle)
        if device_registry is not None:
            warn_missing_virtual_classes(bundle, device_registry)
        yaml_text = bundle_to_pairs_yaml(bundle)
        path = cache.write(
            bundle, yaml_text, lab_uuid=lab_uuid, edge_uuid=edge_uuid,
            graph_hash=graph_hash, real_classes=real_classes, engine=engine,
        )
        logger.info(f"simulation pairs: resolved {len(bundle.pairs)} pairs -> {path}")
    except Exception as exc:  # noqa: BLE001 - network/backend errors -> offline fallback
        if cache.is_compatible(graph_hash, real_classes):
            path = cache.generated_yaml_path()
            logger.warning(f"simulation pairs: backend unavailable ({exc}); using cached bundle {path}")
        else:
            logger.warning(
                f"simulation pairs: backend unavailable ({exc}) and no compatible cache; "
                f"falling back to repository default device_pair.yaml"
            )
            return None

    init_pair_registry(path)
    return Path(path)
