"""Construct device instances from registry ``class.init`` specs (Plan 09 Task 3).

Lets multiple registry entries share one Python class but pass different init
parameters (e.g. different ``backend`` factory). Value rules:
- scalars pass through
- ``${config.x}`` / ``${node.id}`` / ``${node.name}`` inject from node/config
- ``factory: module:Callable`` builds the value via that callable + its args/kwargs
- ``value: ...`` passes an explicit constant (disambiguates from factory)
"""

from __future__ import annotations

import importlib
import re
from typing import Any, Callable

_PLACEHOLDER_PATTERN = re.compile(r"^\$\{([^}]+)\}$")


class RegistryInitializerError(ValueError):
    pass


def build_instance_from_registry_entry(entry: dict[str, Any], node: dict[str, Any], config: dict[str, Any]) -> Any:
    class_config = entry.get("class", {})
    class_ref = class_config.get("module")
    if not isinstance(class_ref, str) or not class_ref:
        raise RegistryInitializerError("Registry entry class.module is required")

    target = import_ref(class_ref)
    init_config = class_config.get("init", {}) or {}
    args = [_resolve_value(value, node=node, config=config) for value in init_config.get("args", [])]
    kwargs = {
        key: _resolve_value(value, node=node, config=config)
        for key, value in init_config.get("kwargs", {}).items()
    }
    return target(*args, **kwargs)


def resolve_init_kwargs(entry: dict[str, Any], node: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``class.init`` into concrete args/kwargs WITHOUT instantiating the
    device class itself (Plan 09 Task 6).

    Used by the ROS device construction path: the resolved kwargs (with factory
    objects built and ${config.*}/${node.*} injected) are merged into driver_params
    so the existing ROS2DeviceNode wrapper / creator still builds the device.
    """
    init_config = (entry.get("class", {}) or {}).get("init", {}) or {}
    args = [_resolve_value(value, node=node, config=config) for value in init_config.get("args", [])]
    kwargs = {
        key: _resolve_value(value, node=node, config=config)
        for key, value in init_config.get("kwargs", {}).items()
    }
    return {"args": args, "kwargs": kwargs}


def import_ref(ref: str) -> Callable[..., Any] | type:
    if ":" not in ref:
        raise RegistryInitializerError(f"Import ref must use 'module:attr' format: {ref}")
    module_name, attr_name = ref.split(":", 1)
    module = importlib.import_module(module_name)
    current: Any = module
    for part in attr_name.split("."):
        current = getattr(current, part)
    return current


def _resolve_value(value: Any, node: dict[str, Any], config: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _resolve_string(value, node=node, config=config)

    if isinstance(value, list):
        return [_resolve_value(item, node=node, config=config) for item in value]

    if isinstance(value, dict):
        if "value" in value and set(value.keys()) == {"value"}:
            return value["value"]
        if "factory" in value:
            factory = import_ref(value["factory"])
            args = [_resolve_value(item, node=node, config=config) for item in value.get("args", [])]
            kwargs = {
                key: _resolve_value(item, node=node, config=config)
                for key, item in value.get("kwargs", {}).items()
            }
            return factory(*args, **kwargs)
        return {key: _resolve_value(item, node=node, config=config) for key, item in value.items()}

    return value


def _resolve_string(value: str, node: dict[str, Any], config: dict[str, Any]) -> Any:
    match = _PLACEHOLDER_PATTERN.match(value)
    if not match:
        return value

    expression = match.group(1)
    if expression.startswith("config."):
        return _select_path(config, expression.removeprefix("config."))
    if expression.startswith("node."):
        return _select_path(node, expression.removeprefix("node."))
    raise RegistryInitializerError(f"Unsupported initializer placeholder: {value}")


def _select_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise RegistryInitializerError(f"Missing initializer value: {path}")
        current = current[part]
    return current
