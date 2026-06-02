from __future__ import annotations

from typing import Any, Dict, Optional

from unilabos.robo_unilabos.models import CommandResult, utc_timestamp
from unilabos.robo_unilabos.resource_map import ResourceMap


def result(
    command: str,
    observations: Dict[str, Any],
    ok: bool = True,
    error: Optional[str] = None,
    graph_source: Optional[str] = None,
) -> CommandResult:
    timestamp = utc_timestamp()
    provenance = {
        "timestamp_start": timestamp,
        "timestamp_end": timestamp,
    }
    if graph_source is not None:
        provenance["resource_map"] = graph_source
    return CommandResult(
        ok=ok,
        command=command,
        observations=observations,
        provenance=provenance,
        error=error,
    )


def list_resources(resource_map: ResourceMap, include_all: bool = False) -> CommandResult:
    resources = [
        resource.summary()
        for resource in resource_map.list_resources(robot_operable_only=not include_all)
    ]
    return result(
        "lab.list.resources",
        {"resources": resources, "count": len(resources)},
        graph_source=resource_map.source,
    )


def inspect_resource(resource_map: ResourceMap, resource_id: str) -> CommandResult:
    resource = resource_map.get_resource(resource_id)
    return result(
        "lab.inspect",
        {"resource": resource.to_dict()},
        graph_source=resource_map.source,
    )


def where(resource_map: ResourceMap, target: str) -> CommandResult:
    resolved = resource_map.resolve_target(target)
    pose = resolved.affordance.pose if resolved.affordance else resolved.resource.pose
    return result(
        "lab.where",
        {
            "target": resolved.target_id,
            "pose": pose.to_dict(),
        },
        graph_source=resource_map.source,
    )


def affordances(resource_map: ResourceMap, resource_id: str) -> CommandResult:
    resource = resource_map.get_resource(resource_id)
    return result(
        "lab.affordances",
        {
            "resource": resource.id,
            "affordances": [affordance.to_dict() for affordance in resource.affordances],
        },
        graph_source=resource_map.source,
    )


def reachable(resource_map: ResourceMap, target: str, robot_id: str) -> CommandResult:
    reachability = resource_map.reachable(target=target, robot_id=robot_id)
    return result(
        "lab.reachable",
        reachability,
        graph_source=resource_map.source,
    )

