"""Validation and merge helpers for enforced registry init params."""

import copy
from typing import Any, Callable, Dict, Optional


def merge_init_param_enforce(config: Any, init_enforce: Any) -> Dict[str, Any]:
    """Merge runtime config with enforced registry params, letting registry win.

    Runtime config may still provide fields not owned by the device model, but
    every field present in ``init_param_enforce`` is applied last and therefore
    cannot be overridden by instance-level config.
    """
    base = copy.deepcopy(config) if isinstance(config, dict) else {}
    if not isinstance(init_enforce, dict):
        return base
    return _merge_dict_with_enforce(base, init_enforce)


def _merge_dict_with_enforce(config: Dict[str, Any], init_enforce: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(config)
    for key, enforced_value in init_enforce.items():
        current_value = merged.get(key)
        if isinstance(current_value, dict) and isinstance(enforced_value, dict):
            merged[key] = _merge_dict_with_enforce(current_value, enforced_value)
        else:
            merged[key] = copy.deepcopy(enforced_value)
    return merged


def validate_init_param_enforce(
    device_id: str,
    init_schema: Optional[Dict[str, Any]],
    init_enforce: Any,
    error_factory: Callable[[str], Exception] = ValueError,
) -> None:
    """Validate the JSON enforced config paired with ``init_param_schema``.

    ``init_param_enforce`` is a plain JSON config object. It must not reintroduce
    the old ``class.init`` object factory DSL; drivers should construct rich
    objects from JSON-friendly type strings and params inside ``__init__``.
    """
    if not isinstance(init_schema, dict):
        return

    config_schema = init_schema.get("config")
    if not isinstance(config_schema, dict):
        return

    required = config_schema.get("required")
    required_fields = [str(field) for field in required] if isinstance(required, list) else []
    properties = config_schema.get("properties")
    has_config_contract = bool(required_fields) or (
        isinstance(properties, dict) and bool(properties)
    )
    if not has_config_contract:
        return

    if not isinstance(init_enforce, dict):
        raise error_factory(
            f"{device_id}: init_param_schema.config 已声明参数，必须提供对象形式的 init_param_enforce"
        )

    missing = [field for field in required_fields if field not in init_enforce]
    if missing:
        raise error_factory(
            f"{device_id}: init_param_enforce 缺少 required 字段: {', '.join(missing)}"
        )

    _reject_legacy_init_enforce(
        init_enforce,
        f"{device_id}.init_param_enforce",
        error_factory,
    )


def _reject_legacy_init_enforce(
    value: Any,
    path: str,
    error_factory: Callable[[str], Exception],
) -> None:
    if isinstance(value, str):
        if "${" in value and "}" in value:
            raise error_factory(f"{path}: init_param_enforce 不支持 ${{...}} 模板，动态值应来自实例 config")
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_legacy_init_enforce(item, f"{path}[{index}]", error_factory)
        return

    if not isinstance(value, dict):
        return

    keys = set(value)
    if "factory" in keys or "args" in keys or "kwargs" in keys or keys == {"value"}:
        raise error_factory(f"{path}: init_param_enforce 不支持 class.init 的 factory/args/kwargs/value DSL")

    for key, item in value.items():
        _reject_legacy_init_enforce(item, f"{path}.{key}", error_factory)
