from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from unilabos.queries.models import Pose, QueryAffordance, SafetyZone, State
from unilabos.robo_unilabos.resource_map import ResourceMap


def _pose_from_robo(pose, source: str) -> Pose:
    return Pose(
        xyz=[
            float(pose.position.get("x", 0.0)),
            float(pose.position.get("y", 0.0)),
            float(pose.position.get("z", 0.0)),
        ],
        frame_id=pose.frame_id,
        source=source,
        metadata={
            "unit": pose.unit,
            "orientation_rpy": dict(pose.orientation_rpy),
        },
    )


class ResourceMapSource:
    name = "resource_map"

    def __init__(self, resource_map: ResourceMap):
        self.resource_map = resource_map

    @classmethod
    def from_file(cls, graph_path: str | Path) -> "ResourceMapSource":
        return cls(ResourceMap.from_file(graph_path))

    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        try:
            resolved = self.resource_map.resolve_target(target)
        except KeyError:
            return None
        pose = resolved.affordance.pose if resolved.affordance else resolved.resource.pose
        result = _pose_from_robo(pose, self.name)
        if frame is not None:
            return Pose(
                xyz=result.xyz,
                quat_xyzw=result.quat_xyzw,
                frame_id=frame,
                stamp=result.stamp,
                source=result.source,
                metadata={**result.metadata, "original_frame_id": result.frame_id},
            )
        return result

    def query_state(self, target: str) -> Optional[State]:
        try:
            resource = self.resource_map.get_resource(target)
        except KeyError:
            return None
        return State(
            name=resource.id,
            values={
                "state_variables": dict(resource.state_variables),
                "locks": dict(resource.locks),
                "safety": dict(resource.safety),
                "device_endpoints": dict(resource.device_endpoints),
            },
            source=self.name,
        )

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        try:
            resource = self.resource_map.get_resource(target)
        except KeyError:
            return []
        affordances = []
        for affordance in resource.affordances:
            if kind is not None and affordance.kind != kind:
                continue
            affordances.append(
                QueryAffordance(
                    id=affordance.id,
                    kind=affordance.kind,
                    pose=_pose_from_robo(affordance.pose, self.name),
                    action_primitives=list(affordance.action_primitives),
                    target=f"{resource.id}.{affordance.id}",
                    constraints=dict(affordance.constraints),
                    metadata={
                        "reachable_by": list(affordance.reachable_by),
                        "access_zone": affordance.access_zone,
                        "device_endpoint": dict(affordance.device_endpoint),
                    },
                )
            )
        return affordances

    def query_action_schema(self, action: str):
        return None

    def query_safety_zones(self) -> List[SafetyZone]:
        zones: List[SafetyZone] = []
        for resource in self.resource_map.list_resources(robot_operable_only=False):
            for zone in resource.access_zones:
                zones.append(
                    SafetyZone(
                        id=f"{resource.id}.{zone.id}",
                        zone_type="no_go" if zone.keepout else "workspace",
                        frame_id=zone.frame_id,
                        bbox_center=[
                            float(zone.pose.position.get("x", 0.0)),
                            float(zone.pose.position.get("y", 0.0)),
                            float(zone.pose.position.get("z", 0.0)),
                        ],
                        source=self.name,
                        metadata={
                            "resource": resource.id,
                            "reachable_by": list(zone.reachable_by),
                            **dict(zone.metadata),
                        },
                    )
                )
        return zones
