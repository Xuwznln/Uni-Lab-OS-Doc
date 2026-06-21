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


def make_downloader(http_client: Any, working_dir: Any = None) -> Callable[[PairBundle], list[Any]]:
    """Default downloader: fetch each bundle virtual_package via the real
    community-package download/extract mechanism (guarded, best-effort).

    Returns the list of extracted package dirs so the caller can mount them.
    Refs without a ``download_url`` are skipped.
    """
    def _default_fetch(ref: VirtualPackageRef):
        if not ref.download_url:
            logger.info("virtual package %s has no download_url; skip", ref.normalized_name)
            return None
        try:
            from unilabos.app.community_packages import _download_and_extract_package
            from unilabos.config.config import BasicConfig
        except Exception as exc:  # noqa: BLE001
            logger.warning("community_packages unavailable; skip virtual package %s: %s", ref.normalized_name, exc)
            return None
        wd = working_dir if working_dir is not None else getattr(BasicConfig, "working_dir", ".")
        package_dir = _download_and_extract_package(
            ref.download_url, wd, ref.normalized_name, ref.version, ref.sha256 or "", http_client
        )
        logger.info("virtual package %s@%s extracted -> %s", ref.normalized_name, ref.version, package_dir)
        return str(package_dir)

    def _download(bundle: PairBundle) -> list[Any]:
        return download_virtual_packages(bundle, _default_fetch)

    return _download
