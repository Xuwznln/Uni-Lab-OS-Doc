from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from unilabos.queries.urdf_robot_model import URDFRobotModelSource


DEFAULT_TICKS_PER_REV = 4096.0


def load_robot_asset_manifest(asset: str | Path) -> Tuple[Dict[str, Any], Path]:
    """Load a robot asset manifest from a directory or manifest file."""
    asset_path = Path(asset)
    manifest_path = asset_path / "asset_manifest.json" if asset_path.is_dir() else asset_path
    if not manifest_path.exists():
        raise FileNotFoundError(f"Robot asset manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest, manifest_path.parent


def resolve_asset_path(asset_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else asset_root / path


def robot_model_source_from_asset(
    asset: str | Path,
    joint_positions: Optional[Dict[str, float]] = None,
) -> URDFRobotModelSource:
    manifest, asset_root = load_robot_asset_manifest(asset)
    urdf_path = resolve_asset_path(asset_root, manifest["urdf"])
    workspace = manifest.get("workspace", {})
    return URDFRobotModelSource.from_file(
        urdf_path,
        robot_id=manifest.get("robot_id", "robot_asset"),
        joint_positions=joint_positions or {},
        root_link=manifest.get("base_frame"),
        tool_link=manifest.get("tool_link"),
        tool_offset_xyz=[float(item) for item in manifest.get("tool_offset_xyz", [0.0, 0.0, 0.0])],
        workspace_center=workspace.get("center", [0.0, 0.0, 0.25]),
        workspace_size=workspace.get("size", [1.2, 1.2, 0.8]),
    )


def ticks_to_rad(ticks: float, center: float, ticks_per_rev: float = DEFAULT_TICKS_PER_REV) -> float:
    return (float(ticks) - float(center)) * 2.0 * math.pi / float(ticks_per_rev)


def logical_joints_from_mapping(position_ticks: Dict[str, Any], manifest_or_mapping: Dict[str, Any]) -> Dict[str, float]:
    servo_cfg = manifest_or_mapping.get("servo", manifest_or_mapping)
    joint_mapping = servo_cfg.get("logical_joint_mapping", servo_cfg)
    ticks_per_rev = float(servo_cfg.get("ticks_per_rev", DEFAULT_TICKS_PER_REV))

    logical: Dict[str, float] = {}
    for joint_name, spec in joint_mapping.items():
        formula = spec.get("formula", "single")
        scale = float(spec.get("scale", 1.0))

        if formula == "single":
            raw = _term_radians(spec, position_ticks, ticks_per_rev)
            logical[joint_name] = scale * raw
            continue

        if formula in {"linear_combination", "mirrored_pair", "pair_difference"}:
            terms = spec.get("terms", [])
            divisor = float(spec.get("divisor", 1.0))
            if divisor == 0.0:
                raise ValueError(f"Mapping divisor cannot be zero for joint {joint_name}")
            total = sum(_term_radians(term, position_ticks, ticks_per_rev) for term in terms)
            logical[joint_name] = scale * total / divisor
            continue

        raise ValueError(f"Unsupported robot asset joint mapping formula: {formula!r}")

    return logical


def _term_radians(term: Dict[str, Any], position_ticks: Dict[str, Any], ticks_per_rev: float) -> float:
    servo_name = term["servo"]
    center = float(term.get("center", 2048.0))
    sign = float(term.get("sign", 1.0))
    ticks = float(position_ticks.get(servo_name, center))
    return sign * ticks_to_rad(ticks, center, ticks_per_rev)
