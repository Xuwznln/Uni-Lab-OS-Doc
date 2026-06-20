"""Ensure virtual packages referenced by a pair bundle are available (M-3).

The actual fetch reuses the community package download/cache mechanism, injected
as ``fetch_fn`` so this module stays testable without network. ``make_downloader``
builds a default best-effort fetcher backed by ``unilabos.app.community_packages``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from unilabos.sim.pairs.bundle import PairBundle, VirtualPackageRef

logger = logging.getLogger(__name__)

# fetch_fn(ref) -> mounted path/str or None
FetchFn = Callable[[VirtualPackageRef], Any]


def iter_virtual_package_refs(bundle: PairBundle) -> list[VirtualPackageRef]:
    return [e.virtual_package for e in bundle.pairs if e.virtual_package is not None]


def download_virtual_packages(bundle: PairBundle, fetch_fn: FetchFn) -> list[Any]:
    """Call fetch_fn for each virtual_package ref; isolate per-ref failures."""
    results: list[Any] = []
    for ref in iter_virtual_package_refs(bundle):
        try:
            res = fetch_fn(ref)
            if res is not None:
                results.append(res)
        except Exception as exc:  # noqa: BLE001 - one bad package must not abort the rest
            logger.warning("failed to fetch virtual package %s@%s: %s", ref.normalized_name, ref.version, exc)
    return results


def make_downloader(http_client: Any) -> Callable[[PairBundle], None]:
    """Best-effort default downloader using community_packages (guarded).

    TODO: wire to the concrete community_packages download/cache API once stable.
    """
    def _default_fetch(ref: VirtualPackageRef):
        try:
            from unilabos.app import community_packages as _cp  # noqa: F401
        except Exception:
            logger.warning("community_packages unavailable; skip virtual package %s", ref.normalized_name)
            return None
        # Placeholder: real call to cp download-by-ref to be wired here.
        logger.info("virtual package fetch requested: %s@%s (%s)", ref.normalized_name, ref.version, ref.download_url)
        return None

    def _download(bundle: PairBundle) -> None:
        download_virtual_packages(bundle, _default_fetch)

    return _download
