from __future__ import annotations

from typing import Any, Dict, Optional

from unilabos.queries.engine import QueryEngine


class QueryService:
    """Serialization boundary shared by future gRPC/ROS2 query services."""

    def __init__(self, engine: QueryEngine):
        self.engine = engine

    def query_pose(self, target: str, frame: Optional[str] = None) -> Dict[str, Any]:
        return self.engine.query_pose(target, frame=frame).to_dict()

    def query_state(self, target: str) -> Dict[str, Any]:
        return self.engine.query_state(target).to_dict()

    def query_affordance(self, target: str, kind: Optional[str] = None) -> Dict[str, Any]:
        return {"affordances": [item.to_dict() for item in self.engine.query_affordance(target, kind=kind)]}

    def query_action_schema(self, action: str) -> Dict[str, Any]:
        return self.engine.query_action_schema(action).to_dict()

    def query_safety_zones(self) -> Dict[str, Any]:
        return {"safety_zones": [item.to_dict() for item in self.engine.query_safety_zones()]}

    def query_verification(
        self,
        task_id: str,
        context: Optional[Dict[str, Any]] = None,
        action: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.engine.query_verification(task_id, context=context, action=action).to_dict()
