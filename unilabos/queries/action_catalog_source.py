from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from unilabos.queries.models import ActionSchema


class ActionCatalogSource:
    """Query source backed by a generated Robo-UniLabOS action catalog."""

    name = "robo_unilabos_action_catalog"

    def __init__(self, entries: Iterable[Dict[str, Any]], source_path: Optional[str] = None):
        self.source_path = source_path
        self._schemas_by_id: Dict[str, ActionSchema] = {}
        self._schemas_by_action: Dict[str, ActionSchema] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            schema_data = entry.get("schema")
            if not isinstance(schema_data, dict):
                continue
            schema = ActionSchema.from_mapping(schema_data)
            entry_id = str(entry.get("id") or entry.get("task_name") or "")
            if entry_id:
                self._schemas_by_id[entry_id] = schema
            self._schemas_by_action.setdefault(schema.action, schema)

    @classmethod
    def from_file(cls, catalog_path: str | Path) -> "ActionCatalogSource":
        path = Path(catalog_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("actions", [])
        if not isinstance(entries, list):
            raise ValueError("Robo-UniLabOS action catalog must contain a list field named 'actions'")
        return cls(entries, source_path=str(path))

    def query_pose(self, target: str, frame: Optional[str] = None):
        return None

    def query_state(self, target: str):
        return None

    def query_affordance(self, target: str, kind: Optional[str] = None) -> List[Any]:
        return []

    def query_action_schema(self, action: str) -> Optional[ActionSchema]:
        return self._schemas_by_id.get(action) or self._schemas_by_action.get(action)

    def query_safety_zones(self) -> List[Any]:
        return []
