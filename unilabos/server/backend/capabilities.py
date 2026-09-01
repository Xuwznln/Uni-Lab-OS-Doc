"""runtime.v1 endpoint 能力快照组装。

把执行适配器（经 ``adapter_registry`` 暴露）维护的在线设备与
``action_value_mappings`` 投影成 runtime.v1 的
``device_routes`` + ``action_capabilities``。

``GET /api/v1/runtime/endpoints`` 返回该快照，供微前端的设备页、单点动作
参数表单与工作流画布节点目录使用。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from unilabos.server.database.tables.runtime import (
    DeviceActionCapability,
    DeviceRoute,
)
from unilabos.protocol.base import canonical_hash

# descriptor 白名单：registry action config 里对前端有用且 JSON-safe 的字段。
# `type`（动作消息类型）在运行期可能被 resolve 成类对象，单独字符串化为
# action_type，不进 descriptor。
_DESCRIPTOR_FIELDS = (
    "schema",
    "goal_default",
    "handles",
    "placeholder_keys",
    "always_free",
    "feedback_interval",
    "node_type",
    "materials_need_lock",
    "goal",
    "feedback",
    "result",
)


def _json_safe(value: Any) -> Any:
    """把 action config 片段规范成纯 JSON 结构（非常规对象字符串化）。"""

    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def build_endpoint_capabilities(
    adapter: Any, *, observed_at_ms: int
) -> Tuple[List[DeviceRoute], List[DeviceActionCapability]]:
    """从执行适配器构建 (device_routes, action_capabilities)。

    HostNode 的两种 transport 形态均满足以下适配器契约：
    ``devices_names``（device_id → namespace）为在线设备面，
    ``_action_value_mappings``（device_id → action_name → config）为动作面。
    """

    devices_names: Dict[str, str] = dict(getattr(adapter, "devices_names", None) or {})
    mappings: Dict[str, Dict[str, Any]] = dict(
        getattr(adapter, "_action_value_mappings", None) or {}
    )
    routes: List[DeviceRoute] = []
    capabilities: List[DeviceActionCapability] = []
    for device_id in sorted(devices_names):
        routes.append(
            DeviceRoute(
                route_uuid=f"route:{device_id}",
                device_uuid=device_id,
                driver_key=device_id,
                enabled=True,
                selected=True,
                config_hash=canonical_hash({}),
            )
        )
        device_actions = mappings.get(device_id) or {}
        for action_name in sorted(device_actions):
            entry = device_actions[action_name]
            if not isinstance(entry, dict):
                continue
            descriptor = _json_safe(
                {key: entry[key] for key in _DESCRIPTOR_FIELDS if key in entry}
            )
            action_type = entry.get("type")
            capabilities.append(
                DeviceActionCapability(
                    device_uuid=device_id,
                    action_name=str(action_name),
                    action_type=(
                        str(getattr(action_type, "__name__", action_type))
                        if action_type
                        else None
                    ),
                    concurrency_mode=(
                        "unbounded" if entry.get("always_free") else "exclusive"
                    ),
                    state="active",
                    availability="unknown",
                    descriptor=descriptor,
                    descriptor_hash=canonical_hash(descriptor),
                    observed_at_ms=observed_at_ms,
                )
            )
    return routes, capabilities


__all__ = ["build_endpoint_capabilities"]
