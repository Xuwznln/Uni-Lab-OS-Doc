from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from unilabos.robo_unilabos.models import Affordance, RobotOperableResource


@dataclass(frozen=True)
class ResourceTarget:
    resource: RobotOperableResource
    affordance: Optional[Affordance] = None

    @property
    def target_id(self) -> str:
        if self.affordance is None:
            return self.resource.id
        return f"{self.resource.id}.{self.affordance.id}"

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "target": self.target_id,
            "resource": self.resource.summary(),
        }
        if self.affordance is not None:
            payload["affordance"] = self.affordance.to_dict()
        return payload


class ResourceMap:
    """Robot-facing view over a Uni-Lab-OS graph JSON file."""

    def __init__(self, resources: Iterable[RobotOperableResource], source: Optional[str] = None):
        self.source = source
        self._resources = {resource.id: resource for resource in resources}
        self._name_index = {resource.name: resource.id for resource in resources if resource.name}

    @classmethod
    def from_file(cls, graph_path: str | Path) -> "ResourceMap":
        path = Path(graph_path)
        with path.open("r", encoding="utf-8") as handle:
            graph = json.load(handle)
        return cls.from_graph(graph, source=str(path))

    @classmethod
    def from_graph(cls, graph: Dict[str, Any], source: Optional[str] = None) -> "ResourceMap":
        nodes = graph.get("nodes", [])
        if not isinstance(nodes, list):
            raise ValueError("Robo-UniLabOS graph must contain a list field named 'nodes'")
        resources = [
            RobotOperableResource.from_graph_node(node)
            for node in nodes
            if isinstance(node, dict) and (node.get("id") or node.get("name"))
        ]
        return cls(resources, source=source)

    def list_resources(self, robot_operable_only: bool = True) -> List[RobotOperableResource]:
        resources = sorted(self._resources.values(), key=lambda item: item.id)
        if robot_operable_only:
            return [resource for resource in resources if resource.is_robot_operable]
        return resources

    def get_resource(self, resource_id: str) -> RobotOperableResource:
        resolved_id = self._name_index.get(resource_id, resource_id)
        if resolved_id not in self._resources:
            raise KeyError(f"Unknown resource: {resource_id}")
        return self._resources[resolved_id]

    def resolve_target(self, target: str) -> ResourceTarget:
        resource_id, separator, affordance_id = target.partition(".")
        resource = self.get_resource(resource_id)
        if not separator:
            return ResourceTarget(resource=resource)
        affordance = resource.affordance(affordance_id)
        if affordance is None:
            raise KeyError(f"Unknown affordance: {target}")
        return ResourceTarget(resource=resource, affordance=affordance)

    def reachable(self, target: str, robot_id: str) -> Dict[str, Any]:
        resolved = self.resolve_target(target)
        candidates = self._reachable_candidates(resolved)
        robot_known = robot_id in self._resources or robot_id in self._name_index
        reachable = robot_id in candidates
        reason = "explicit_reachable_by_match" if reachable else "robot_not_listed_for_target"
        if not candidates:
            reason = "target_has_no_reachability_contract"
        if not robot_known:
            reason = "unknown_robot"
        return {
            "target": resolved.target_id,
            "robot": robot_id,
            "reachable": reachable and robot_known,
            "reachable_by": candidates,
            "reason": reason,
        }

    def _reachable_candidates(self, target: ResourceTarget) -> List[str]:
        candidates: List[str] = []
        if target.affordance is not None:
            candidates.extend(target.affordance.reachable_by)
            if target.affordance.access_zone:
                zone = target.resource.access_zone(target.affordance.access_zone)
                if zone is not None:
                    candidates.extend(zone.reachable_by)
        candidates.extend(target.resource.reachable_by)
        seen = set()
        unique = []
        for candidate in candidates:
            if candidate not in seen:
                unique.append(candidate)
                seen.add(candidate)
        return unique

    def to_dict(self, robot_operable_only: bool = True) -> Dict[str, Any]:
        return {
            "source": self.source,
            "resources": [
                resource.to_dict()
                for resource in self.list_resources(robot_operable_only=robot_operable_only)
            ],
        }

