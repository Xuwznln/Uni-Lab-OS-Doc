from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from unilabos.queries.labutopia.task_configs import (
    LabUtopiaTaskConfigSource,
    _paths_from_config,
    _target_entries_from_config,
)
from unilabos.queries.labutopia.usd_source import LabUtopiaUsdSource
from unilabos.queries.models import Pose, QueryAffordance, SafetyZone, State
from unilabos.queries.sources import QuerySource


UsdSourceFactory = Callable[[Union[str, Path]], QuerySource]


def labutopia_asset_id_from_prim_path(prim_path: str) -> str:
    normalized = prim_path.strip("/")
    if not normalized:
        return "World"
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", normalized).strip("_") or "World"


class LabUtopiaSceneSource:
    name = "labutopia_scene"

    def __init__(
        self,
        task_source: LabUtopiaTaskConfigSource,
        labutopia_root: Optional[str | Path] = None,
        config_directory: Optional[str | Path] = None,
        usd_source_factory: UsdSourceFactory = LabUtopiaUsdSource,
    ):
        self.task_source = task_source
        self.labutopia_root = Path(labutopia_root).expanduser() if labutopia_root else None
        self.config_directory = Path(config_directory).expanduser() if config_directory else None
        self.usd_source_factory = usd_source_factory
        self._usd_sources: Dict[str, QuerySource] = {}

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        labutopia_root: Optional[str | Path] = None,
        usd_source_factory: UsdSourceFactory = LabUtopiaUsdSource,
    ) -> "LabUtopiaSceneSource":
        config_directory = Path(directory)
        return cls(
            task_source=LabUtopiaTaskConfigSource.from_directory(config_directory),
            labutopia_root=labutopia_root,
            config_directory=config_directory,
            usd_source_factory=usd_source_factory,
        )

    @property
    def configs(self):
        return self.task_source.configs

    def resolve_usd_path(self, usd_path: Optional[str]) -> Optional[Path]:
        if not usd_path:
            return None
        path = Path(usd_path).expanduser()
        if path.is_absolute():
            return path

        candidates: List[Path] = []
        if self.labutopia_root is not None:
            candidates.append(self.labutopia_root / path)
        if self.config_directory is not None:
            candidates.extend([self.config_directory.parent / path, self.config_directory / path])
        candidates.append(path)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else path

    def _usd_source_for_config(self, config: Dict) -> Optional[QuerySource]:
        resolved = self.resolve_usd_path(config.get("usd_path"))
        if resolved is None:
            return None
        key = str(resolved)
        if key not in self._usd_sources:
            self._usd_sources[key] = self.usd_source_factory(resolved)
        return self._usd_sources[key]

    def _configs_for_target(self, target: str) -> List[Dict]:
        matched = []
        for config in self.configs:
            if target == str(config.get("name")):
                matched.append(config)
                continue
            for entry in _target_entries_from_config(config):
                if target == entry["path"]:
                    matched.append(config)
                    break
        return matched

    def _navigation_entry_for_target(self, target: str) -> Optional[Dict]:
        if not target.startswith("navigation://"):
            return None
        for config in self.configs:
            for entry in _target_entries_from_config(config):
                if target == entry["path"]:
                    return entry
        return None

    def _target_entries_for_target(self, target: str) -> List[Dict]:
        entries: List[Dict] = []
        for config in self.configs:
            for entry in _target_entries_from_config(config):
                if target == entry["path"]:
                    entries.append(
                        {
                            **entry,
                            "labutopia_task_name": config.get("name") or config.get("_file_stem"),
                            "task_type": config.get("task_type"),
                            "controller_type": config.get("controller_type"),
                            "source_file": config.get("_source_file"),
                            "usd_path": config.get("usd_path"),
                        }
                    )
        return entries

    def _pose_from_position_range(self, target: str, entry: Dict, frame: Optional[str] = None) -> Optional[Pose]:
        constraints = dict(entry.get("constraints") or {})
        position_range = constraints.get("position_range")
        if not isinstance(position_range, dict):
            return None
        xyz: List[float] = []
        for axis in ("x", "y", "z"):
            bounds = position_range.get(axis)
            if isinstance(bounds, list) and len(bounds) >= 2:
                xyz.append((float(bounds[0]) + float(bounds[1])) / 2.0)
            elif isinstance(bounds, list) and len(bounds) == 1:
                xyz.append(float(bounds[0]))
            else:
                xyz.append(0.0)
        return Pose(
            xyz=xyz,
            frame_id=frame or "labutopia_world",
            source=self.name,
            metadata={
                "target": target,
                "source_type": "labutopia_task_config_position_range",
                "role": entry.get("role"),
                "position_range": position_range,
                "labutopia_task_name": entry.get("labutopia_task_name"),
                "source_file": entry.get("source_file"),
                "usd_path": entry.get("usd_path"),
            },
        )

    def _config_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        for entry in self._target_entries_for_target(target):
            pose = self._pose_from_position_range(target, entry, frame=frame)
            if pose is not None:
                return pose
        return None

    def _config_state(self, target: str) -> Optional[State]:
        for entry in self._target_entries_for_target(target):
            constraints = dict(entry.get("constraints") or {})
            position_range = constraints.get("position_range")
            if not isinstance(position_range, dict):
                continue
            pose = self._pose_from_position_range(target, entry)
            values = {
                "target_type": "task_config_target",
                "prim_path": target,
                "role": entry.get("role"),
                "center_xyz": pose.xyz if pose is not None else None,
                "position_range": position_range,
                "constraints": constraints,
                "labutopia_task_name": entry.get("labutopia_task_name"),
                "task_type": entry.get("task_type"),
                "controller_type": entry.get("controller_type"),
                "source_file": entry.get("source_file"),
                "usd_path": entry.get("usd_path"),
            }
            return State(name=target, source=self.name, values=values)
        return None

    def _navigation_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        entry = self._navigation_entry_for_target(target)
        if entry is None:
            return None
        constraints = dict(entry.get("constraints") or {})
        x_bounds = constraints.get("x_bounds")
        y_bounds = constraints.get("y_bounds")
        if isinstance(x_bounds, list) and len(x_bounds) >= 2:
            x = (float(x_bounds[0]) + float(x_bounds[1])) / 2.0
        else:
            x = 0.0
        if isinstance(y_bounds, list) and len(y_bounds) >= 2:
            y = (float(y_bounds[0]) + float(y_bounds[1])) / 2.0
        else:
            y = 0.0
        return Pose(
            xyz=[x, y, 0.0],
            frame_id=frame or "labutopia_world",
            source=self.name,
            metadata={
                "target": target,
                "source_type": "labutopia_navigation_config",
                "role": entry.get("role"),
                "x_bounds": x_bounds,
                "y_bounds": y_bounds,
                "offset_radius": constraints.get("offset_radius"),
                "scene_asset_path": constraints.get("scene_asset_path"),
                "barrier_image_path": constraints.get("barrier_image_path"),
                "resolved_navigation_config_path": constraints.get("resolved_navigation_config_path"),
            },
        )

    def _navigation_state(self, target: str) -> Optional[State]:
        entry = self._navigation_entry_for_target(target)
        if entry is None:
            return None
        constraints = dict(entry.get("constraints") or {})
        pose = self._navigation_pose(target)
        center = pose.xyz if pose is not None else [0.0, 0.0, 0.0]
        return State(
            name=target,
            source=self.name,
            values={
                "target_type": "navigation_goal",
                "navigation_target": target,
                "role": entry.get("role"),
                "center_xyz": center,
                "x_bounds": constraints.get("x_bounds"),
                "y_bounds": constraints.get("y_bounds"),
                "offset_radius": constraints.get("offset_radius"),
                "scene_asset_path": constraints.get("scene_asset_path"),
                "barrier_image_path": constraints.get("barrier_image_path"),
                "navigation_config_path": constraints.get("navigation_config_path"),
                "resolved_navigation_config_path": constraints.get("resolved_navigation_config_path"),
            },
        )

    def _all_usd_sources(self) -> List[QuerySource]:
        sources: List[QuerySource] = []
        for config in self.configs:
            source = self._usd_source_for_config(config)
            if source is not None and source not in sources:
                sources.append(source)
        return sources

    def query_pose(self, target: str, frame: Optional[str] = None):
        navigation_pose = self._navigation_pose(target, frame=frame)
        if navigation_pose is not None:
            return navigation_pose
        sources = [self._usd_source_for_config(config) for config in self._configs_for_target(target)]
        sources.extend(source for source in self._all_usd_sources() if source not in sources)
        for source in sources:
            if source is None:
                continue
            try:
                pose = source.query_pose(target, frame=frame)
            except (RuntimeError, ValueError):
                continue
            if pose is not None:
                return pose
        return self._config_pose(target, frame=frame)

    def query_state(self, target: str):
        task_state = self.task_source.query_state(target)
        if task_state is not None:
            return task_state
        navigation_state = self._navigation_state(target)
        if navigation_state is not None:
            return navigation_state
        sources = [self._usd_source_for_config(config) for config in self._configs_for_target(target)]
        sources.extend(source for source in self._all_usd_sources() if source not in sources)
        for source in sources:
            if source is None:
                continue
            try:
                state = source.query_state(target)
            except (RuntimeError, ValueError):
                continue
            if state is not None:
                return state
        return self._config_state(target)

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        affordances = self.task_source.query_affordance(target, kind=kind)
        enriched = []
        for affordance in affordances:
            if affordance.pose is not None or not affordance.target:
                enriched.append(affordance)
                continue
            pose = self.query_pose(affordance.target)
            if pose is None:
                enriched.append(affordance)
                continue
            enriched.append(
                QueryAffordance(
                    id=affordance.id,
                    kind=affordance.kind,
                    pose=pose,
                    action_primitives=list(affordance.action_primitives),
                    target=affordance.target,
                    constraints=dict(affordance.constraints),
                    metadata={
                        **dict(affordance.metadata),
                        "source": self.name,
                        "task_config_source": self.task_source.name,
                        "scene_aware_pose_enriched": True,
                    },
                )
            )
        return enriched

    def query_action_schema(self, action: str):
        return self.task_source.query_action_schema(action)

    def query_task_schema(self, task_name: str):
        return self.task_source.query_task_schema(task_name)

    def query_safety_zones(self) -> List[SafetyZone]:
        zones: List[SafetyZone] = []
        seen = set()
        for config in self.configs:
            for entry in _target_entries_from_config(config):
                target = entry["path"]
                if target in seen:
                    continue
                seen.add(target)
                state = self.query_state(target)
                values = state.values if state is not None else {}
                center = self._center_from_state(values)
                size = values.get("bbox_size")
                if not center or not isinstance(size, list):
                    continue
                zones.append(
                    SafetyZone(
                        id=f"{labutopia_asset_id_from_prim_path(target)}.bbox",
                        zone_type="collision",
                        frame_id="labutopia_world",
                        bbox_center=[float(item) for item in center],
                        bbox_size=[float(item) for item in size],
                        source=self.name,
                        metadata={
                            "prim_path": target,
                            "usd_path": values.get("usd_path"),
                        },
                    )
                )
        return zones

    def _center_from_state(self, values: Dict) -> List[float]:
        bbox_min = values.get("bbox_min")
        bbox_max = values.get("bbox_max")
        if isinstance(bbox_min, list) and isinstance(bbox_max, list) and len(bbox_min) == 3 and len(bbox_max) == 3:
            return [(float(bbox_min[i]) + float(bbox_max[i])) / 2.0 for i in range(3)]
        return []

    def config_paths(self, config: Dict) -> Dict:
        return _paths_from_config(config)
