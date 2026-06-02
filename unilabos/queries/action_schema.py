from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from unilabos.queries.models import ActionSchema


REQUIRED_SCHEMA_FIELDS = {
    "schema_version": str,
    "action": str,
    "args": dict,
    "preconditions": list,
    "postconditions": list,
    "policy_preference": list,
    "timeout_s": (int, float),
    "metadata": dict,
}


def validate_action_schema_mapping(data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for field, expected_type in REQUIRED_SCHEMA_FIELDS.items():
        if field not in data:
            errors.append(f"missing field: {field}")
            continue
        if not isinstance(data[field], expected_type):
            errors.append(f"{field} must be {expected_type}")
    if "action" in data and (not isinstance(data["action"], str) or not data["action"].strip()):
        errors.append("action must be a non-empty string")
    for field in ("preconditions", "postconditions"):
        if isinstance(data.get(field), list):
            for index, item in enumerate(data[field]):
                if not isinstance(item, dict):
                    errors.append(f"{field}[{index}] must be an object")
                elif "type" not in item:
                    errors.append(f"{field}[{index}] missing type")
    if isinstance(data.get("policy_preference"), list):
        for index, item in enumerate(data["policy_preference"]):
            if not isinstance(item, str):
                errors.append(f"policy_preference[{index}] must be a string")
    if isinstance(data.get("timeout_s"), (int, float)) and float(data["timeout_s"]) <= 0:
        errors.append("timeout_s must be positive")
    return errors


class ActionSchemaRegistry:
    def __init__(self):
        self._schemas: Dict[str, ActionSchema] = {}

    @classmethod
    def with_builtin_schemas(cls) -> "ActionSchemaRegistry":
        registry = cls()
        try:
            schema_root = resources.files("unilabos").joinpath("action_schemas")
            for resource in schema_root.iterdir():
                if resource.name.endswith(".json"):
                    registry.register_mapping(json.loads(resource.read_text(encoding="utf-8")))
        except Exception:
            # Editable checkouts and partial installs may not expose package resources.
            fallback = Path(__file__).parents[1] / "action_schemas"
            if fallback.exists():
                registry.load_directory(fallback)
        return registry

    def register(self, schema: ActionSchema) -> None:
        self._schemas[schema.action] = schema

    def register_mapping(self, data: dict) -> None:
        errors = validate_action_schema_mapping(data)
        if errors:
            raise ValueError(f"Invalid action schema {data.get('action', '<unknown>')}: {'; '.join(errors)}")
        self.register(ActionSchema.from_mapping(data))

    def load_directory(self, directory: str | Path) -> None:
        for path in sorted(Path(directory).glob("*.json")):
            self.register_mapping(json.loads(path.read_text(encoding="utf-8")))

    def get(self, action: str) -> Optional[ActionSchema]:
        return self._schemas.get(action)

    def names(self) -> Iterable[str]:
        return sorted(self._schemas)

    def validate_all(self) -> Dict[str, List[str]]:
        return {name: validate_action_schema_mapping(schema.to_dict()) for name, schema in self._schemas.items()}


def query_action_schema(action: str, registry: Optional[ActionSchemaRegistry] = None) -> Optional[ActionSchema]:
    registry = registry or ActionSchemaRegistry.with_builtin_schemas()
    return registry.get(action)
