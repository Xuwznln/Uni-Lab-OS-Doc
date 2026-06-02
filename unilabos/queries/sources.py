from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from unilabos.queries.models import ActionSchema, Pose, QueryAffordance, SafetyZone, State


class QuerySource(Protocol):
    name: str

    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        ...

    def query_state(self, target: str) -> Optional[State]:
        ...

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        ...

    def query_action_schema(self, action: str) -> Optional[ActionSchema]:
        ...

    def query_safety_zones(self) -> List[SafetyZone]:
        ...


class EmptyQuerySource:
    name = "empty"

    def query_pose(self, target: str, frame: Optional[str] = None) -> Optional[Pose]:
        return None

    def query_state(self, target: str) -> Optional[State]:
        return None

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        return []

    def query_action_schema(self, action: str) -> Optional[ActionSchema]:
        return None

    def query_safety_zones(self) -> List[SafetyZone]:
        return []


def first_non_none(values: List[Any]) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
