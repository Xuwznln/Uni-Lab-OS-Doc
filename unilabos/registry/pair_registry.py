"""Registry for real/virtual device pairs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

import yaml

MissingSimPolicy = Literal["stub", "skip", "fail"]


@dataclass(frozen=True)
class PairEntry:
    real: str
    virtual: str | None = None
    missing_sim_policy: MissingSimPolicy = "stub"
    twin_observed: list[str] = field(default_factory=list)
    twin_throttle_hz: float = 10.0
    engine: str = "none"
    explicit: bool = True


class PairRegistry:
    def __init__(self, path: str | Path | None = None, default_policy: MissingSimPolicy = "stub"):
        self.path = Path(path) if path else Path(__file__).with_name("device_pair.yaml")
        self.default_policy = default_policy
        self._pairs = self._load()

    def _load(self) -> dict[str, PairEntry]:
        if not self.path.exists():
            return {}
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        pairs = {}
        for item in raw.get("pairs", []):
            policy = item.get("missing_sim_policy", self.default_policy)
            if policy not in ("stub", "skip", "fail"):
                raise ValueError(f"invalid missing_sim_policy for {item.get('real')}: {policy}")
            real = item["real"]
            # Plan 08 v2: twin_capability {enabled, observed, throttle_hz};
            # legacy fallback: twin_observed / twin_throttle_hz.
            tc = item.get("twin_capability")
            if isinstance(tc, dict):
                enabled = bool(tc.get("enabled", False))
                twin_observed = [str(x) for x in (tc.get("observed") or [])] if enabled else []
                twin_throttle_hz = float(tc.get("throttle_hz", 10.0))
            else:
                twin_observed = [str(x) for x in (item.get("twin_observed") or [])]
                twin_throttle_hz = float(item.get("twin_throttle_hz", 10.0))
            pairs[real] = PairEntry(
                real=real,
                virtual=item.get("virtual"),
                missing_sim_policy=policy,
                twin_observed=twin_observed,
                twin_throttle_hz=twin_throttle_hz,
                engine=str(item.get("engine", "none")),
            )
        return pairs

    def lookup(self, real_class_name: str) -> PairEntry:
        return self._pairs.get(
            real_class_name,
            PairEntry(real=real_class_name, virtual=None, missing_sim_policy=self.default_policy, explicit=False),
        )

    def iter_stub_devices(self) -> Iterator[PairEntry]:
        for entry in self._pairs.values():
            if entry.virtual is None and entry.missing_sim_policy == "stub":
                yield entry


_default_registry: PairRegistry | None = None


def get_pair_registry() -> PairRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = PairRegistry()
    return _default_registry


def init_pair_registry(path: str | Path, default_policy: MissingSimPolicy = "stub") -> PairRegistry:
    """Point the global PairRegistry at a runtime-generated device_pair.yaml (M-4).

    Edge calls this after compiling the cloud pair bundle, so ``lookup()`` /
    ``initialize_device`` use the generated pairs without changing Phase 1A APIs.
    """
    global _default_registry
    _default_registry = PairRegistry(path, default_policy=default_policy)
    return _default_registry


def reset_pair_registry() -> None:
    """Reset the global registry (tests)."""
    global _default_registry
    _default_registry = None


def lookup(real_class_name: str) -> PairEntry:
    return get_pair_registry().lookup(real_class_name)
