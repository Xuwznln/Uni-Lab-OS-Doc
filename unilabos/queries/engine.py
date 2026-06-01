from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from unilabos.hal.registry import HALRegistry
from unilabos.queries.action_schema import ActionSchemaRegistry
from unilabos.queries.models import ActionSchema, Pose, QueryAffordance, SafetyZone, State, VerificationResult
from unilabos.queries.sources import QuerySource
from unilabos.queries.verification import VerificationEngine


class QueryNotFound(KeyError):
    pass


class QueryEngine:
    def __init__(
        self,
        sources: Optional[Iterable[QuerySource]] = None,
        action_schemas: Optional[ActionSchemaRegistry] = None,
        hal_registry: Optional[HALRegistry] = None,
        verification: Optional[VerificationEngine] = None,
    ):
        self.sources = list(sources or [])
        self.action_schemas = action_schemas or ActionSchemaRegistry.with_builtin_schemas()
        self.hal_registry = hal_registry or HALRegistry()
        self.verification = verification or VerificationEngine()

    def add_source(self, source: QuerySource) -> None:
        self.sources.append(source)

    def query_pose(self, target: str, frame: Optional[str] = None) -> Pose:
        hal_pose = self._query_hal_pose(target, frame)
        if hal_pose is not None:
            return hal_pose
        for source in self.sources:
            pose = source.query_pose(target, frame=frame)
            if pose is not None:
                return pose
        raise QueryNotFound(f"Pose not found: {target}")

    def query_state(self, target: str) -> State:
        hal_state = self._query_hal_state(target)
        if hal_state is not None:
            return hal_state
        for source in self.sources:
            state = source.query_state(target)
            if state is not None:
                return state
        raise QueryNotFound(f"State not found: {target}")

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
        result: List[QueryAffordance] = []
        for source in self.sources:
            result.extend(source.query_affordance(target, kind=kind))
        if not result:
            raise QueryNotFound(f"Affordance not found: {target}")
        return [self._ensure_affordance_pose(item) for item in result]

    def query_action_schema(self, action: str) -> ActionSchema:
        for source in self.sources:
            schema = source.query_action_schema(action)
            if schema is not None:
                return schema
        schema = self.action_schemas.get(action)
        if schema is not None:
            return schema
        raise QueryNotFound(f"Action schema not found: {action}")

    def query_safety_zones(self) -> List[SafetyZone]:
        zones: List[SafetyZone] = []
        for source in self.sources:
            zones.extend(source.query_safety_zones())
        return zones

    def query_verification(
        self,
        task_id: str,
        context: Optional[Dict[str, Any]] = None,
        action: Optional[str] = None,
        postconditions: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> VerificationResult:
        checks = postconditions
        if checks is None and action is not None:
            checks = self.query_action_schema(action).postconditions
        return self.verification.verify(task_id=task_id, context=context, postconditions=checks)

    def _query_hal_pose(self, target: str, frame: Optional[str]) -> Optional[Pose]:
        robot_id, sep, robot_target = target.partition(".")
        if not sep:
            return None
        hal = self.hal_registry.get(robot_id)
        if hal is None:
            return None
        return hal.get_pose(frame or robot_target or "tool0")

    def _query_hal_state(self, target: str) -> Optional[State]:
        hal = self.hal_registry.get(target)
        if hal is None:
            return None
        joint_state = hal.get_joint_state()
        values = {
            "joint_state": joint_state.to_dict(),
        }
        extra_state = getattr(hal, "get_state_values", None)
        if callable(extra_state):
            values.update(extra_state())
        return State(
            name=target,
            values=values,
            source="hal",
        )

    def _ensure_affordance_pose(self, affordance: QueryAffordance) -> QueryAffordance:
        if affordance.pose is not None or not affordance.target:
            return affordance
        try:
            pose = self.query_pose(affordance.target)
        except QueryNotFound:
            return affordance
        return QueryAffordance(
            id=affordance.id,
            kind=affordance.kind,
            pose=pose,
            action_primitives=list(affordance.action_primitives),
            target=affordance.target,
            constraints=dict(affordance.constraints),
            metadata={**dict(affordance.metadata), "pose_enriched": True},
        )
