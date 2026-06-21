"""Pair bundle cache + manifest (work package M-5 / Plan §10.2-10.3).

Stores the resolved bundle, the generated device_pair.yaml, and a manifest under
``<base_dir>/simulation_pairs/`` so the Edge can run offline when the backend is
unreachable (as long as the cached bundle still covers the graph's real classes).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from unilabos.sim.pairs.bundle import PairBundle

SUBDIR = "simulation_pairs"
MANIFEST = "manifest.json"
GENERATED_YAML = "device_pair.generated.yaml"


def compute_graph_hash(real_classes: list[str]) -> str:
    payload = ",".join(sorted(set(real_classes)))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PairCache:
    def __init__(self, base_dir: str | Path):
        self.dir = Path(base_dir) / SUBDIR

    def manifest_path(self) -> Path:
        return self.dir / MANIFEST

    def generated_yaml_path(self) -> Path:
        return self.dir / GENERATED_YAML

    def write(
        self,
        bundle: PairBundle,
        yaml_text: str,
        *,
        lab_uuid: str | None,
        edge_uuid: str | None,
        graph_hash: str,
        real_classes: list[str],
        engine: str = "none",
        real_template_uuids: list[str] | None = None,
    ) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.generated_yaml_path().write_text(yaml_text, encoding="utf-8")
        bundle_file = self.dir / f"bundle-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        bundle_file.write_text(
            json.dumps({"bundle_version": bundle.bundle_version, "pairs": _bundle_pairs_json(bundle)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest = {
            "bundle_version": bundle.bundle_version,
            "lab_uuid": lab_uuid,
            "edge_uuid": edge_uuid,
            "engine": engine,
            "graph_hash": graph_hash,
            "real_classes": sorted(set(real_classes)),
            "real_template_uuids": sorted(set(real_template_uuids or [])),
            "generated_yaml": GENERATED_YAML,
            "bundle_file": bundle_file.name,
        }
        self.manifest_path().write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.generated_yaml_path()

    def load_manifest(self) -> dict[str, Any] | None:
        p = self.manifest_path()
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def is_compatible(self, graph_hash: str, real_classes: list[str]) -> bool:
        """Cache is usable offline if it exists, the generated yaml is present, and it
        covers the requested real classes (exact graph_hash match, or superset)."""
        manifest = self.load_manifest()
        if not manifest or not self.generated_yaml_path().is_file():
            return False
        if manifest.get("graph_hash") == graph_hash:
            return True
        cached = set(manifest.get("real_classes") or [])
        return set(real_classes).issubset(cached)


def _bundle_pairs_json(bundle: PairBundle) -> list[dict[str, Any]]:
    out = []
    for e in bundle.pairs:
        out.append({
            "real": e.real, "engine": e.engine, "virtual": e.virtual,
            "missing_sim_policy": e.missing_sim_policy,
            "is_default": e.is_default, "priority": e.priority,
            "twin_capability": {
                "enabled": e.twin_capability.enabled,
                "observed": list(e.twin_capability.observed),
                "throttle_hz": e.twin_capability.throttle_hz,
            },
        })
    return out
