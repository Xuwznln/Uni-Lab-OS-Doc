from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from unilabos.queries.models import Pose, SafetyZone, State


class LabUtopiaUsdSource:
    name = "labutopia_usd"
    _PRIM_PATH_ALIASES = {
        "/World/Table1/Desk1/surface/Cube": (
            "/World/table/surface/mesh",
            "/World/table/surface",
            "/World/table",
        ),
    }

    def __init__(self, usd_path: str | Path):
        self.usd_path = str(usd_path)
        self._stage = None

    def _load_stage(self):
        if self._stage is not None:
            return self._stage
        try:
            from pxr import Usd
        except ImportError as exc:
            raise RuntimeError("pxr is required for direct USD queries; use asset cards as a fallback") from exc
        stage = Usd.Stage.Open(self.usd_path)
        if stage is None:
            raise ValueError(f"Could not open USD stage: {self.usd_path}")
        self._stage = stage
        return self._stage

    def _parent_candidates(self, target: str) -> List[str]:
        path = target.rstrip("/")
        candidates: List[str] = []
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            if path in {"", "/", "/World"}:
                break
            candidates.append(path)
        return candidates

    def _resolve_prim(self, target: str):
        stage = self._load_stage()
        candidates = [target]
        candidates.extend(self._PRIM_PATH_ALIASES.get(target, ()))
        candidates.extend(self._parent_candidates(target))
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            prim = stage.GetPrimAtPath(candidate)
            if prim and prim.IsValid():
                return prim, candidate
        lowered = target.lower()
        for prim in stage.Traverse():
            path = str(prim.GetPath())
            if path.lower() == lowered:
                return prim, path
        return None, None

    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        prim, resolved_path = self._resolve_prim(target)
        if prim is None:
            return None
        from pxr import Usd, UsdGeom

        transform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        translation = transform.ExtractTranslation()
        try:
            rotation = transform.ExtractRotationQuat()
            quat = rotation.GetImaginary()
            quat_xyzw = [float(quat[0]), float(quat[1]), float(quat[2]), float(rotation.GetReal())]
        except Exception:
            quat_xyzw = [0.0, 0.0, 0.0, 1.0]
        return Pose(
            xyz=[float(translation[0]), float(translation[1]), float(translation[2])],
            quat_xyzw=quat_xyzw,
            frame_id=frame or "labutopia_world",
            source=self.name,
            metadata={
                "usd_path": self.usd_path,
                "prim_path": target,
                "resolved_prim_path": resolved_path,
                "resolved_by_fallback": resolved_path != target,
                "type_name": prim.GetTypeName(),
            },
        )

    def query_state(self, target: str) -> Optional[State]:
        prim, resolved_path = self._resolve_prim(target)
        if prim is None:
            return None
        values: dict[str, Any] = {
            "prim_path": target,
            "resolved_prim_path": resolved_path,
            "resolved_by_fallback": resolved_path != target,
            "type_name": prim.GetTypeName(),
            "children": [str(child.GetPath()) for child in prim.GetChildren()],
            "usd_path": self.usd_path,
        }
        try:
            from pxr import Usd, UsdGeom

            cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), includedPurposes=[UsdGeom.Tokens.default_])
            bbox = cache.ComputeWorldBound(prim)
            bounds = bbox.ComputeAlignedRange()
            min_point = bounds.GetMin()
            max_point = bounds.GetMax()
            values["bbox_min"] = [float(min_point[i]) for i in range(3)]
            values["bbox_max"] = [float(max_point[i]) for i in range(3)]
            values["bbox_size"] = [float(max_point[i] - min_point[i]) for i in range(3)]
        except Exception as exc:
            values["bbox_error"] = str(exc)
        return State(name=target, values=values, source=self.name)

    def query_affordance(self, target: str, kind: Optional[str] = None):
        return []

    def query_action_schema(self, action: str):
        return None

    def query_safety_zones(self) -> List[SafetyZone]:
        return []
