"""第三方示例：独立身份、后进先出策略，仅依赖公开协议和准入候选工具。"""

from typing import Any

from unilabos.scheduling.engine import JsonEngine, reason
from uuid import UUID, uuid5
from unilabos.scheduling.resource_bindings import bindings_conflict
from unilabos.scheduling.validation import node_allowed, node_key, task_capacity_available
from unilabos.scheduling.resource_bindings import RESOURCE_BINDINGS_CAPABILITY
from unilabos.scheduling.validation import SnapshotIndex


def intent_for(
    request_id: str, node: dict[str, Any], *, option: dict[str, Any] | None = None,
    flight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = "continue_attempt" if flight else (
        "admit_manual" if node["manual_mode"] in {"confirm_then_execute", "material_io"} else "start"
    )
    identity = f"{node['ref']['task_id']}:{node['ref']['node_id']}:{node['attempt_no']}:{kind}"
    return {
        "intent_id": str(uuid5(UUID(request_id), identity)),
        "node": dict(node["ref"]), "attempt_no": node["attempt_no"],
        "parameters_revision": node["parameters_revision"], "kind": kind,
        "option_id": option["option_id"] if option else None,
        "attempt_id": flight["attempt_id"] if flight else None,
    }


def choose_dispatch(
    request: dict[str, Any], facts: SnapshotIndex, task_scope: list[str],
    preferred: list[str] | None = None,
) -> list[dict[str, Any]]:
    """依照引擎给定顺序选择可准入候选，并试算整批资源和容量。"""
    used = {key: pool["used"] for key, pool in facts.pools.items()}
    activated, picked, occupied = set(), set(), set()
    conflicts: dict[str, set[str]] = {}
    for edge in request["body"]["snapshot"]["resource_view"]["conflicts"]:
        conflicts.setdefault(edge["left"], set()).add(edge["right"])
        conflicts.setdefault(edge["right"], set()).add(edge["left"])
    intents = []
    selected_options: list[dict[str, Any]] = []
    intervals = list(facts.intervals.values())
    for flight in facts.inflight.values():
        node = facts.nodes[node_key(flight["node"])]
        if flight["node"]["task_id"] in task_scope and flight["continuation_allowed"]:
            intents.append(intent_for(request["request_id"], node, flight=flight))
            picked.add(node_key(node["ref"]))
            occupied.update(flight["resource_ids"])
    option_ids = preferred if preferred is not None else sorted(facts.options)
    for identity in option_ids:
        option = facts.options[identity]
        key = node_key(option["node"])
        node, task = facts.nodes[key], facts.tasks[key[0]]
        if (
            key[0] not in task_scope or key in picked or not option["eligible_now"]
            or not node_allowed(task, node) or node["status"] != "pending"
            or not node["ready"] or not node["parameters_final"] or node["job_id"] is None
            or node["kind"] not in {"device", "manual"}
            or (node["manual_mode"] == "intervention" and not node["manual_approved"])
        ):
            continue
        resources = set(option["resource_ids"])
        if resources & occupied or any(conflicts.get(key, set()) & occupied for key in resources):
            continue
        if any(bindings_conflict(option, selected, intervals, conflicts) for selected in selected_options):
            continue
        pools = list(node["capacity_pool_ids"])
        if key[0] not in activated:
            pools.extend(
                pool_id for pool_id in task["capacity_pool_ids"]
                if not any(holder["task_id"] == key[0] for holder in facts.pools[pool_id]["holders"])
            )
        if any(facts.pools[pool_id]["limit"] is not None and used[pool_id] + 1 > facts.pools[pool_id]["limit"] for pool_id in pools):
            continue
        for pool_id in pools:
            used[pool_id] += 1
        activated.add(key[0])
        picked.add(key)
        selected_options.append(option)
        intents.append(intent_for(request["request_id"], node, option=option))
    return intents



class NewestTaskEngine(JsonEngine):
    engine_id = "example.newest_task"
    build_id = "example-newest-r3"
    capabilities = ("core.v1", RESOURCE_BINDINGS_CAPABILITY)

    def decide_values(
        self, request: dict[str, Any], facts: SnapshotIndex, config: dict[str, Any], state: Any,
    ) -> tuple[dict[str, Any], Any]:
        body = request["body"]
        snapshot = body["snapshot"]
        decision: dict[str, Any] = {
            "status": "wait", "task_scope": [], "dispatch_now": [], "wakeup_after_ms": None,
            "forecast": None, "reasons": [reason("vendor.example.wait", "等待可执行的设备或人工节点")],
        }
        if body["feedback"]["has_more"]:
            decision.update(status="sync", task_scope=None)
            return decision, None
        if snapshot["lifecycle"] != "running" or snapshot["execution_gate"] != "open":
            return decision, None
        remaining = [task for task in facts.tasks.values() if not task["terminal_settled"]]
        active = [task for task in remaining if task["status"] != "pending"]
        if not remaining:
            return decision, None
        selected = max(active or remaining, key=lambda task: int(task["queue_seq"]))
        scope = [selected["task_id"]]
        # 已在途 Task 优先完成；同一 Task 的候选仍遵守资源和容量合同。
        decision["task_scope"] = scope
        decision["dispatch_now"] = choose_dispatch(request, facts, scope)
        if decision["dispatch_now"]:
            decision.update(status="advance", reasons=[])
        return decision, None


def create_engine() -> JsonEngine:
    return NewestTaskEngine()
