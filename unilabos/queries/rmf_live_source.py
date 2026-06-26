"""RmfLiveSource：把 RMF 运行态接入 Phase 3 查询 API（#17 §2.4-C / #18 §6.2-(5)）。

实现 `QuerySource` Protocol（见 `unilabos/queries/sources.py`）。与 `RosLiveSource` 类似，
持有一份由 `rmf.coordinator` 喂入的内存缓存：fleet/robot pose、door/lift 状态、
受限区域（safety zone）。注册进 `QueryEngine` 后，`query_pose/query_state` 即可返回
RMF 实时态；未命中返回 None，引擎回退到静态源。

缓存 + feed API 无 ROS 依赖，可单测。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from unilabos.queries.models import ActionSchema, Pose, QueryAffordance, SafetyZone, State, utc_timestamp


class RmfLiveSource:
    name = "rmf_live"

    def __init__(self, max_age_s: Optional[float] = None):
        self._poses: Dict[str, Tuple[Pose, float]] = {}
        self._states: Dict[str, Tuple[State, float]] = {}
        self._safety_zones: Dict[str, SafetyZone] = {}
        self.max_age_s = max_age_s

    # ------------------------------------------------------------- feed API
    def update_pose(
        self,
        name: str,
        xyz: List[float],
        frame_id: str = "lab_world",
        source: str = "rmf",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        pose = Pose(
            xyz=list(xyz),
            frame_id=frame_id,
            stamp=utc_timestamp(),
            source=source,
            metadata=dict(metadata or {}),
        )
        self._poses[name] = (pose, time.monotonic())

    def update_state(self, name: str, values: Dict[str, Any], source: str = "rmf") -> None:
        self._states[name] = (State(name=name, values=dict(values), stamp=utc_timestamp(), source=source), time.monotonic())

    def set_safety_zone(self, zone: SafetyZone) -> None:
        self._safety_zones[zone.id] = zone

    def update_restricted_zones(self, zones: List[Dict[str, Any]]) -> None:
        """从 semantic_map 的 restricted_zones（米制）填充 safety zone。"""
        for z in zones:
            zid = z.get("waypoint") or z.get("id") or f"zone_{len(self._safety_zones)}"
            self._safety_zones[zid] = SafetyZone(
                id=zid,
                zone_type="restricted",
                bbox_center=[float(z.get("x_m", 0.0)), float(z.get("y_m", 0.0)), 0.0],
                bbox_size=list(z.get("size", [])),
                source="rmf",
            )

    # --------------------------------------------------------------- helpers
    def _fresh(self, ts: float) -> bool:
        return self.max_age_s is None or (time.monotonic() - ts) <= self.max_age_s

    # --------------------------------------------------- QuerySource Protocol
    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        entry = self._poses.get(target)
        if entry is None or not self._fresh(entry[1]):
            return None
        return entry[0]

    def query_state(self, target: str) -> Optional[State]:
        entry = self._states.get(target)
        if entry is None or not self._fresh(entry[1]):
            return None
        return entry[0]

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        return []

    def query_action_schema(self, action: str) -> Optional[ActionSchema]:
        return None

    def query_safety_zones(self) -> List[SafetyZone]:
        return list(self._safety_zones.values())
