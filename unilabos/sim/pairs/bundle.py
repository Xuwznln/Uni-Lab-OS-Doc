"""Pair Bundle data structures + parsing/validation (contract C-1 / work package M-2).

The Edge resolve API (`POST /lab/square/edge/simulation-pairs/resolve`) returns a
"pair bundle" describing, for each real device class in the graph, which virtual
driver replaces it (or the missing-sim policy). This module parses the bundle's
`data` payload into typed objects and validates it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_POLICIES = ("stub", "skip", "fail")


@dataclass(frozen=True)
class VirtualPackageRef:
    normalized_name: str
    version: str
    download_url: str | None = None
    sha256: str | None = None
    class_namespace: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VirtualPackageRef":
        return cls(
            normalized_name=str(data.get("normalized_name", "")),
            version=str(data.get("version", "")),
            download_url=data.get("download_url"),
            sha256=data.get("sha256"),
            class_namespace=data.get("class_namespace"),
        )


@dataclass(frozen=True)
class PairBundleEntry:
    real: str
    virtual: str | None = None
    missing_sim_policy: str = "stub"
    twin_observed: list[str] = field(default_factory=list)
    twin_throttle_hz: float = 10.0
    virtual_package: VirtualPackageRef | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PairBundleEntry":
        vp = data.get("virtual_package")
        return cls(
            real=str(data["real"]),
            virtual=data.get("virtual"),
            missing_sim_policy=str(data.get("missing_sim_policy", "stub")),
            twin_observed=[str(x) for x in (data.get("twin_observed") or [])],
            twin_throttle_hz=float(data.get("twin_throttle_hz", 10.0)),
            virtual_package=VirtualPackageRef.from_dict(vp) if isinstance(vp, dict) else None,
        )


@dataclass(frozen=True)
class PairBundle:
    bundle_version: str
    pairs: list[PairBundleEntry] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


def parse_bundle(data: dict[str, Any]) -> PairBundle:
    """Parse the resolve API `data` object into a PairBundle. Tolerant of missing optional fields."""
    if not isinstance(data, dict):
        raise ValueError("bundle data must be a dict")
    pairs = [PairBundleEntry.from_dict(p) for p in (data.get("pairs") or []) if isinstance(p, dict) and p.get("real")]
    return PairBundle(
        bundle_version=str(data.get("bundle_version", "")),
        pairs=pairs,
        warnings=[dict(w) for w in (data.get("warnings") or []) if isinstance(w, dict)],
    )


def validate_bundle(bundle: PairBundle) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors: list[str] = []
    seen: set[str] = set()
    for entry in bundle.pairs:
        if entry.real in seen:
            errors.append(f"duplicate real class in bundle: {entry.real}")
        seen.add(entry.real)
        if entry.missing_sim_policy not in VALID_POLICIES:
            errors.append(f"{entry.real}: invalid missing_sim_policy={entry.missing_sim_policy}")
        if entry.virtual is None and entry.missing_sim_policy == "stub":
            # allowed (null-virtual + stub), no error
            pass
        if entry.virtual is not None and not str(entry.virtual).strip():
            errors.append(f"{entry.real}: empty virtual class")
    return errors
