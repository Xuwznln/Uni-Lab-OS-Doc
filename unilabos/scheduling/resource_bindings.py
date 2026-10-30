"""UniLabOS 资源绑定注册语义；共享所有权与动作的排他使用分别表达。"""

from __future__ import annotations

from typing import Any

from unilabos.scheduling.protocol import require

RESOURCE_BINDINGS_CAPABILITY = "unilab.resource_bindings.v1"


def active_resources(option: dict[str, Any]) -> set[str]:
    """缺少注册元数据的基础协议候选保守地把全部资源视为活动使用。"""
    values = option["binding_facts"].get("active_resource_ids", option["resource_ids"])
    require(
        isinstance(values, list) and all(isinstance(value, str) for value in values),
        "SNAPSHOT_INVALID", "候选活动资源必须是字符串列表",
    )
    require(
        len(set(values)) == len(values) and set(values) <= set(option["resource_ids"]),
        "SNAPSHOT_INVALID", "候选活动资源重复或不属于完整绑定",
    )
    return set(values)


def bindings_conflict(
    left: dict[str, Any], right: dict[str, Any],
    intervals: list[dict[str, Any]], conflicts: dict[str, set[str]],
) -> bool:
    """同一显式区间内的纯所有权可共享；活动使用和区间外竞争仍互斥。"""
    left_active, right_active = active_resources(left), active_resources(right)
    shared = {
        resource
        for interval in intervals
        if left["node"] in interval["members"] and right["node"] in interval["members"]
        for resource in interval["resource_ids"]
    }
    for first in left["resource_ids"]:
        for second in right["resource_ids"]:
            if first != second and second not in conflicts.get(first, set()):
                continue
            if first == second and first in shared and not (first in left_active and second in right_active):
                continue
            return True
    return False
