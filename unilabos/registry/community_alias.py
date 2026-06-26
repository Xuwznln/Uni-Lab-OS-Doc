"""Community registry alias resolution (Plan 09 Task 7).

Graph ``class`` may reference a community variant id as ``community.<id>``. After
the community package is downloaded/mounted, the registry holds the local id
``<id>``; this module strips the prefix and validates the registry entry exists.
Complements the existing ``community_packages.apply_community_aliases`` (which
mutates the registry); these helpers are pure and used at graph->device lookup.
"""

from __future__ import annotations

from typing import Any

COMMUNITY_PREFIX = "community."


class CommunityAliasError(ValueError):
    pass


def normalize_community_class(class_name: str) -> str:
    if class_name.startswith(COMMUNITY_PREFIX):
        return class_name[len(COMMUNITY_PREFIX):]
    return class_name


def resolve_community_alias(class_name: str, device_registry: dict[str, Any]) -> str:
    normalized = normalize_community_class(class_name)
    if normalized not in device_registry:
        raise CommunityAliasError(
            f"Community class '{class_name}' resolved to '{normalized}', but no registry entry exists"
        )
    return normalized
