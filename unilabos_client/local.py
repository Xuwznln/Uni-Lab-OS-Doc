from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from unilabos.api import QueryService
from unilabos.hal.mock import MockHAL
from unilabos.queries.action_catalog_source import ActionCatalogSource
from unilabos.queries.engine import QueryEngine


class RoboUniLabOS:
    """Local Python client for the Phase 13 query API.

    This is intentionally transport-free. gRPC and ROS2 servers can reuse the
    same `QueryService` serialization boundary when those runtime dependencies
    are available.
    """

    def __init__(self, service: QueryService):
        self.service = service

    @classmethod
    def from_sources(
        cls,
        graph: Optional[str] = None,
        asset_cards: Optional[str] = None,
        action_catalog: Optional[str] = None,
        labutopia_config: Optional[str] = None,
        usd: Optional[str] = None,
        robot_assets: Optional[Iterable[str]] = None,
        mock_hals: Optional[Iterable[str]] = None,
    ) -> "RoboUniLabOS":
        unsupported = {
            "graph": graph,
            "asset_cards": asset_cards,
            "labutopia_config": labutopia_config,
            "usd": usd,
            "robot_assets": list(robot_assets or []),
        }
        enabled_unsupported = {key: value for key, value in unsupported.items() if value}
        if enabled_unsupported:
            raise NotImplementedError(
                "This integration includes the Phase 13 core query API only; "
                f"unsupported optional sources: {', '.join(sorted(enabled_unsupported))}"
            )
        sources = []
        if action_catalog:
            sources.append(ActionCatalogSource.from_file(action_catalog))
        engine = QueryEngine(sources=sources)
        for robot_id in mock_hals or []:
            engine.hal_registry.register(robot_id, MockHAL(robot_id=robot_id))
        return cls(QueryService(engine))

    def query_pose(self, target: str, frame: Optional[str] = None) -> Dict[str, Any]:
        return self.service.query_pose(target, frame=frame)

    def query_state(self, target: str) -> Dict[str, Any]:
        return self.service.query_state(target)

    def query_affordance(self, target: str, kind: Optional[str] = None) -> Dict[str, Any]:
        return self.service.query_affordance(target, kind=kind)

    def query_action_schema(self, action: str) -> Dict[str, Any]:
        return self.service.query_action_schema(action)

    def query_safety_zones(self) -> Dict[str, Any]:
        return self.service.query_safety_zones()

    def query_verification(
        self,
        task_id: str,
        context: Optional[Dict[str, Any]] = None,
        action: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.service.query_verification(task_id, context=context, action=action)
