"""独立于引擎的关系校验；通过校验的决策仍须经过平台实时准入。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable, Hashable, Iterable

from unilabos.scheduling.protocol import (
    DEFAULT_LIMITS,
    EngineError,
    counter,
    require,
    state_bytes,
    validate_message,
    validate_value,
)
from unilabos.scheduling.resource_bindings import active_resources

NodeKey = tuple[str, str]


def node_key(ref: dict[str, str]) -> NodeKey:
    return ref["task_id"], ref["node_id"]


def automatic_allowed(task: dict[str, Any]) -> bool:
    return (
        task["control"] == "active"
        and task["execution_mode"] == "normal"
        and task["normal_progression_allowed"]
    )


def task_admitted(task: dict[str, Any]) -> bool:
    return (
        task["status"] in {"pending", "running"}
        and task["admission"] == "admitted"
        and task["control"] in {"active", "paused"}
    )


def node_allowed(task: dict[str, Any], node: dict[str, Any]) -> bool:
    """单步只覆盖普通暂停门禁，不能覆盖取消、恢复和域级屏障。"""
    return task_admitted(task) and (
        automatic_allowed(task)
        or (
            task["execution_mode"] in {"step", "single_node"}
            and task["control"] in {"active", "paused"}
            and node["step_authorized"]
        )
    )


def task_capacity_available(task: dict[str, Any], pools: dict[str, dict[str, Any]]) -> bool:
    """快照中的 Task 激活额度；已持有额度的 Task 续行不重复计费。"""
    return all(
        pool["limit"] is None or pool["used"] < pool["limit"]
        or any(holder["task_id"] == task["task_id"] for holder in pool["holders"])
        for pool in (pools[identity] for identity in task["capacity_pool_ids"])
    )


def _check(condition: bool, message: str) -> None:
    require(condition, "SNAPSHOT_INVALID", message)


def _unique(values: Iterable[Hashable], message: str) -> None:
    items = list(values)
    _check(len(items) == len(set(items)), message)


def _index(items: list[dict[str, Any]], key: Callable) -> dict:
    result = {key(item): item for item in items}
    _check(len(items) == len(result), "当前身份重复")
    return result


def _acyclic(parents: dict[Hashable, Iterable[Hashable]], message: str) -> None:
    """迭代拓扑校验，长 DAG 不受 Python 递归深度限制。"""
    incoming = {key: set(value) for key, value in parents.items()}
    children: dict[Hashable, set] = defaultdict(set)
    for key, values in incoming.items():
        _check(values <= incoming.keys(), "依赖引用不存在")
        for parent in values:
            children[parent].add(key)
    # 使用独立计数避免修改调用方提供的集合。
    counts = {key: len(values) for key, values in incoming.items()}
    ready = deque(key for key, value in counts.items() if value == 0)
    completed = 0
    while ready:
        parent = ready.popleft()
        completed += 1
        for child in children[parent]:
            counts[child] -= 1
            if counts[child] == 0:
                ready.append(child)
    _check(completed == len(counts), message)


@dataclass(frozen=True)
class SnapshotIndex:
    tasks: dict[str, dict[str, Any]]
    nodes: dict[NodeKey, dict[str, Any]]
    regions: dict[str, dict[str, Any]]
    resources: dict[str, dict[str, Any]]
    options: dict[str, dict[str, Any]]
    allocations: dict[str, dict[str, Any]]
    intervals: dict[str, dict[str, Any]]
    inflight: dict[str, dict[str, Any]]
    pools: dict[str, dict[str, Any]]


def _snapshot_counters(body: dict[str, Any]) -> None:
    snapshot = body["snapshot"]
    values = [
        snapshot["revision"], snapshot["feedback_watermark"],
        body["checkpoint"]["revision"], body["checkpoint"]["feedback_cursor"],
        body["feedback"]["after_seq"], body["feedback"]["through_seq"],
    ]
    values.extend(task["queue_seq"] for task in snapshot["task_view"]["tasks"])
    values.extend(node["parameters_revision"] for node in snapshot["task_view"]["nodes"])
    values.extend(option["parameters_revision"] for option in snapshot["resource_view"]["options"])
    values.extend(flight["parameters_revision"] for flight in snapshot["inflight"])
    values.extend(event["seq"] for event in body["feedback"]["events"])
    try:
        for value in values:
            counter(value)
    except EngineError as error:
        raise EngineError("SNAPSHOT_INVALID", "快照计数器越界") from error


def validate_snapshot(
    request: dict[str, Any], *, limits: dict[str, int] | None = None, check_schema: bool = True,
) -> SnapshotIndex:
    if check_schema:
        validate_message(request, "DecideRequest")
    body = request["body"]
    snapshot = body["snapshot"]
    _snapshot_counters(body)
    tv, rv = snapshot["task_view"], snapshot["resource_view"]
    limits = limits or DEFAULT_LIMITS
    for name, items in (
        ("max_tasks", tv["tasks"]), ("max_nodes", tv["nodes"]),
        ("max_options", rv["options"]), ("max_feedback_events", body["feedback"]["events"]),
    ):
        require(len(items) <= limits[name], "LIMIT_EXCEEDED", f"超过 {name} 上限")
    facts = SnapshotIndex(
        _index(tv["tasks"], lambda item: item["task_id"]),
        _index(tv["nodes"], lambda item: node_key(item["ref"])),
        _index(tv["control_regions"], lambda item: item["region_id"]),
        _index(rv["resources"], lambda item: item["resource_id"]),
        _index(rv["options"], lambda item: item["option_id"]),
        _index(rv["allocations"], lambda item: item["allocation_id"]),
        _index(rv["intervals"], lambda item: item["interval_id"]),
        _index(snapshot["inflight"], lambda item: item["attempt_id"]),
        _index(snapshot["capacity_view"]["pools"], lambda item: item["pool_id"]),
    )
    tasks, nodes = facts.tasks, facts.nodes
    _unique((task["queue_seq"] for task in tasks.values()), "队列序号重复")
    _check(snapshot["lifecycle"] == "running" or snapshot["execution_gate"] == "held", "非运行阶段必须关闭域门禁")
    _check(body["compute_budget_ms"] <= request["timeout_ms"], "计算预算超过调用预算")
    head = snapshot["head"]
    if head is not None:
        _unique(head["task_scope"], "head Task 范围重复")
        _check(set(head["task_scope"]) <= tasks.keys(), "head Task 引用不存在")
        if head["status"] == "wait":
            _check(not head["authorization_valid"] and head["authorization_expires_at_ms"] is None, "wait 不能保留授权")
        if head["authorization_valid"]:
            _check(
                head["run_epoch"] == body["run_epoch"]
                and head["status"] == "advance"
                and snapshot["lifecycle"] == "running"
                and snapshot["execution_gate"] == "open"
                and head["authorization_expires_at_ms"] is not None
                and head["authorization_expires_at_ms"] > snapshot["captured_at_ms"],
                "head 授权已失效",
            )
    for key, node in nodes.items():
        _check(key[0] in tasks, "节点所属 Task 不存在")
        predecessors = [node_key(ref) for ref in node["predecessors"]]
        _unique(predecessors, "节点结构前驱重复")
        _check(all(parent in nodes and parent[0] == key[0] for parent in predecessors), "节点前驱必须属于同一 Task")
        _check((node["kind"] == "manual") == (node["manual_mode"] != "none"), "人工节点类型与模式不一致")
        _check(node["control_region_id"] is None or node["control_region_id"] in facts.regions, "节点区域引用不存在")
    _acyclic({key: [node_key(ref) for ref in node["predecessors"]] for key, node in nodes.items()}, "节点依赖有环")
    _validate_regions(facts)
    _validate_resources(facts, rv)
    _unique((node_key(item["node"]) for item in facts.inflight.values()), "同一节点有多个当前 attempt")
    for flight in facts.inflight.values():
        key = node_key(flight["node"])
        _check(key in nodes, "在途节点不存在")
        node, task = nodes[key], tasks[key[0]]
        _check(
            node["status"] in {"running", "uncertain"}
            and flight["attempt_no"] == node["attempt_no"]
            and flight["job_id"] == node["job_id"]
            and flight["parameters_revision"] == node["parameters_revision"],
            "在途身份或参数修订与节点不一致",
        )
        _check(set(flight["resource_ids"]) <= facts.resources.keys(), "在途资源不存在")
        _unique(flight["command_ids"], "在途命令身份重复")
        if flight["start_state"] == "NOT_STARTED":
            _check(flight["started_at_ms"] is None, "未开始对象不能有真实开始时间")
        if flight["continuation_allowed"]:
            _check(
                flight["start_state"] == "NOT_STARTED"
                and snapshot["lifecycle"] == "running"
                and snapshot["execution_gate"] == "open"
                and task_admitted(task)
                and (automatic_allowed(task) or task["execution_mode"] in {"step", "single_node"}),
                "续行许可绕过开始或控制屏障",
            )
            if node["kind"] == "manual":
                _check(flight["manual_phase"] == "approved", "人工续行缺少批准")
    for task in tasks.values():
        if task["terminal_settled"]:
            identity = task["task_id"]
            _check(
                task["status"] in {"succeeded", "failed", "canceled"}
                and not any(item["node"]["task_id"] == identity for item in facts.inflight.values())
                and not any(item["owner_task_id"] == identity for item in facts.allocations.values()),
                "终态结算不能覆盖仍在途或持有资源的 Task",
            )
    _validate_capacity(facts)
    feedback = body["feedback"]
    after, through, watermark = map(int, (
        feedback["after_seq"], feedback["through_seq"], snapshot["feedback_watermark"],
    ))
    require(
        after == int(body["checkpoint"]["feedback_cursor"])
        and after <= through <= watermark
        and feedback["has_more"] == (through < watermark)
        and through - after == len(feedback["events"])
        and all(int(event["seq"]) == after + index + 1 for index, event in enumerate(feedback["events"])),
        "FEEDBACK_GAP", "反馈批次必须连续并与检查点及水位一致",
    )
    _unique((event["event_id"] for event in feedback["events"]), "反馈事件身份重复")
    return facts


def _validate_regions(facts: SnapshotIndex) -> None:
    nodes, regions = facts.nodes, facts.regions
    _unique((node_key(region["controller"]) for region in regions.values()), "区域 controller 重复")
    _acyclic(
        {key: [region["parent_region_id"]] if region["parent_region_id"] else [] for key, region in regions.items()},
        "控制区域包含有环",
    )
    for identity, region in regions.items():
        controller = node_key(region["controller"])
        _check(controller in nodes and controller[0] == region["task_id"], "区域 controller 无效")
        _check(
            nodes[controller]["kind"] == region["kind"]
            and nodes[controller]["control_region_id"] == region["parent_region_id"],
            "controller 必须属于父区域且类型一致",
        )
        members = {node_key(ref) for ref in region["members"]}
        _check(
            len(members) == len(region["members"])
            and members == {key for key, node in nodes.items() if node["control_region_id"] == identity}
            and all(key[0] == region["task_id"] for key in members),
            "区域 members 必须恰好包含直属节点",
        )
        entries = {key for key in members if not ({node_key(ref) for ref in nodes[key]["predecessors"]} & members)}
        non_exits = {node_key(ref) for key in members for ref in nodes[key]["predecessors"]} & members
        _check(
            {node_key(ref) for ref in region["entry_nodes"]} == entries
            and {node_key(ref) for ref in region["exit_nodes"]} == members - non_exits,
            "区域入口出口与结构 DAG 不一致",
        )
        for name in ("entry_nodes", "exit_nodes", "input_nodes"):
            _unique((node_key(ref) for ref in region[name]), "区域节点列表重复")
        _check(
            all(node_key(ref) in nodes and ref["task_id"] == region["task_id"] for ref in region["input_nodes"]),
            "区域输入引用无效",
        )
        if region["kind"] == "condition":
            _check(region["iteration_no"] is None, "条件区域不能有循环轮次")
        elif region["phase"] == "body_active":
            _check(region["iteration_no"] is not None, "活动循环体缺少轮次")
        if region["phase"] == "resolved":
            _check(not region["unmaterialized_tail"], "已完成区域不能有未知尾部")
        parent = region["parent_region_id"]
        if parent:
            _check(regions[parent]["task_id"] == region["task_id"], "父区域必须属于同一 Task")
            if region["unmaterialized_tail"]:
                _check(regions[parent]["unmaterialized_tail"], "未知尾部未传播到父区域")


def _validate_resources(facts: SnapshotIndex, view: dict[str, Any]) -> None:
    resources, nodes = facts.resources, facts.nodes
    for option in facts.options.values():
        key = node_key(option["node"])
        _check(key in nodes, "候选节点不存在")
        _check(
            option["attempt_no"] == nodes[key]["attempt_no"]
            and option["parameters_revision"] == nodes[key]["parameters_revision"],
            "候选的 attempt 或参数修订不一致",
        )
        _check(set(option["resource_ids"]) <= resources.keys(), "候选资源不存在")
        _unique(option["resource_ids"], "候选资源重复")
        active = active_resources(option)
        reserved = set(option["resource_ids"]) - active
        covered = {
            resource for interval in facts.intervals.values()
            if option["node"] in interval["members"] for resource in interval["resource_ids"]
        }
        _check(reserved <= covered, "候选纯所有权资源缺少显式区间成员关系")
    conflicts = []
    for edge in view["conflicts"]:
        _check(edge["left"] in resources and edge["right"] in resources and edge["left"] != edge["right"], "资源冲突引用无效")
        conflicts.append(tuple(sorted((edge["left"], edge["right"]))))
    _unique(conflicts, "资源冲突关系重复")
    for allocation in facts.allocations.values():
        _check(set(allocation["resource_ids"]) <= resources.keys(), "占用资源不存在")
        _unique(allocation["resource_ids"], "占用资源重复")
        owner = allocation["owner_task_id"]
        _check(owner is None or owner in facts.tasks, "占用所属 Task 不存在")
        for name in ("owner_node", "release_boundary"):
            ref = allocation[name]
            _check(ref is None or (node_key(ref) in nodes and ref["task_id"] == owner), "占用节点引用无效")
    _acyclic({key: value["contained_in"] for key, value in facts.intervals.items()}, "资源区间包含有环")
    for interval in facts.intervals.values():
        owner = interval["owner_task_id"]
        _check(owner in facts.tasks and set(interval["resource_ids"]) <= resources.keys(), "区间所属 Task 或资源不存在")
        _unique(interval["contained_in"], "区间父引用重复")
        _unique((node_key(ref) for ref in interval["members"]), "区间成员重复")
        references = interval["members"] + [interval[name] for name in ("acquire_node", "release_node") if interval[name]]
        _check(all(node_key(ref) in nodes and ref["task_id"] == owner for ref in references), "区间节点引用无效")
        for identity in interval["held_allocation_ids"]:
            _check(
                identity in facts.allocations
                and facts.allocations[identity]["owner_task_id"] == owner
                and set(facts.allocations[identity]["resource_ids"]) & set(interval["resource_ids"]) != set(),
                "区间实际占用引用无效",
            )
        for identity in interval["contained_in"]:
            _check(facts.intervals[identity]["owner_task_id"] == owner, "包含区间跨 Task")
    for waiter in view["waiters"]:
        key = node_key(waiter["node"])
        _check(
            key in nodes and waiter["attempt_no"] == nodes[key]["attempt_no"]
            and set(waiter["resource_ids"]) <= resources.keys(),
            "资源等待者引用无效",
        )


def _validate_capacity(facts: SnapshotIndex) -> None:
    pools, tasks, nodes, flights = facts.pools, facts.tasks, facts.nodes, facts.inflight
    for pool in pools.values():
        _check((pool["scope"] == "domain") == (pool["workflow_id"] is None), "容量池作用域不一致")
        _check(pool["used"] == len(pool["holders"]) + pool["external_used"], "容量使用计数不一致")
        holders = []
        for holder in pool["holders"]:
            identity = holder["task_id"]
            _check(identity in tasks, "容量持有 Task 不存在")
            if pool["scope"] == "workflow":
                _check(tasks[identity]["workflow_id"] == pool["workflow_id"], "容量持有者不属于该工作流定义")
            attempt = holder["attempt_id"]
            if pool["kind"] == "active_tasks":
                _check(attempt is None, "Task 容量不能按 attempt 计数")
                holders.append(identity)
            else:
                _check(attempt in flights and flights[attempt]["node"]["task_id"] == identity, "Job 容量缺少对应在途对象")
                holders.append(attempt)
        _unique(holders, "容量持有者重复")

    def applicable(task: dict[str, Any], kind: str) -> set[str]:
        return {
            key for key, pool in pools.items()
            if pool["kind"] == kind and (pool["scope"] == "domain" or pool["workflow_id"] == task["workflow_id"])
        }

    for task in tasks.values():
        _unique(task["capacity_pool_ids"], "Task 容量池引用重复")
        _check(set(task["capacity_pool_ids"]) == applicable(task, "active_tasks"), "Task 未列出全部适用容量池")
    for key, node in nodes.items():
        _unique(node["capacity_pool_ids"], "节点容量池引用重复")
        _check(set(node["capacity_pool_ids"]) == applicable(tasks[key[0]], "inflight_jobs"), "节点未列出全部适用容量池")
    for attempt, flight in flights.items():
        for identity in nodes[node_key(flight["node"])]["capacity_pool_ids"]:
            _check(any(holder["attempt_id"] == attempt for holder in pools[identity]["holders"]), "在途对象漏计容量")


def validate_decision(
    request: dict[str, Any],
    response: dict[str, Any],
    *,
    capabilities: Iterable[str] = ("core.v1", "forecast.v1"),
    state_formats: set[str] | None = None,
    limits: dict[str, int] | None = None,
    check_schema: bool = True,
) -> None:
    """候选接受前整批校验；资源竞争的最终处理仍属于平台准入事务。"""
    facts = validate_snapshot(request, limits=limits, check_schema=check_schema)
    if check_schema:
        validate_value(response, "DecideResponse", code="INVALID_DECISION")
    require(response["ok"], "INVALID_DECISION", "预期成功的 decide 响应")
    body, result = request["body"], response["result"]
    snapshot = body["snapshot"]

    def check(condition: bool, message: str) -> None:
        require(condition, "INVALID_DECISION", message)

    check(response["request_id"] == request["request_id"] == result["decision_id"], "决策和请求身份不匹配")
    check(all(result[key] == body[key] for key in ("instance_id", "domain_id", "run_epoch")), "引擎实例或运行世代不匹配")
    check(
        result["base_snapshot_revision"] == snapshot["revision"]
        and result["base_checkpoint_revision"] == body["checkpoint"]["revision"],
        "决策引用了不同的事实或检查点",
    )
    check(result["consumed_feedback_seq"] == body["feedback"]["through_seq"], "必须完整确认当前反馈批次")
    check(result["status"] != "advance" or (snapshot["lifecycle"] == "running" and snapshot["execution_gate"] == "open"), "域级屏障关闭时不能推进")
    if body["feedback"]["has_more"]:
        check(result["status"] == "sync" and result["forecast"] is None, "反馈追赶期间只允许同步")
    if result["status"] == "sync":
        check(result["task_scope"] is None and not result["dispatch_now"] and result["forecast"] is None, "sync 不能改变调度范围")
    else:
        scope = result["task_scope"]
        check(scope is not None and len(scope) == len(set(scope)) and set(scope) <= facts.tasks.keys(), "决策 Task 范围无效")
    if result["status"] != "advance":
        check(not result["dispatch_now"] and bool(result["reasons"]), "等待或同步必须说明原因且不能派发")

    seen_nodes, seen_intents, activated = set(), set(), set()
    used = {key: pool["used"] for key, pool in facts.pools.items()}
    for intent in result["dispatch_now"]:
        key = node_key(intent["node"])
        check(key in facts.nodes and key not in seen_nodes and intent["intent_id"] not in seen_intents, "执行意图身份无效或重复")
        seen_nodes.add(key)
        seen_intents.add(intent["intent_id"])
        node, task = facts.nodes[key], facts.tasks[key[0]]
        check(
            key[0] in result["task_scope"] and task_admitted(task)
            and intent["attempt_no"] == node["attempt_no"]
            and intent["parameters_revision"] == node["parameters_revision"],
            "执行意图不属于已准入的当前节点",
        )
        if intent["kind"] == "continue_attempt":
            flight = facts.inflight.get(intent["attempt_id"])
            check(
                flight is not None and flight["node"] == intent["node"]
                and flight["attempt_no"] == intent["attempt_no"]
                and flight["parameters_revision"] == intent["parameters_revision"]
                and flight["start_state"] == "NOT_STARTED" and flight["continuation_allowed"]
                and intent["option_id"] is None and node["status"] == "running",
                "续行必须使用有许可的原 attempt",
            )
            continue
        check(
            node_allowed(task, node) and node["status"] == "pending"
            and node["ready"] and node["parameters_final"] and node["job_id"] is not None,
            "新执行意图不满足节点控制或参数条件",
        )
        option = facts.options.get(intent["option_id"])
        check(
            option is not None and option["eligible_now"] and option["node"] == intent["node"]
            and option["attempt_no"] == intent["attempt_no"]
            and option["parameters_revision"] == intent["parameters_revision"],
            "执行意图未引用当前合法候选",
        )
        if intent["kind"] == "admit_manual":
            check(node["kind"] == "manual" and node["manual_mode"] in {"confirm_then_execute", "material_io"}, "人工准入节点类型无效")
        else:
            check(node["kind"] == "device" or (
                node["kind"] == "manual" and node["manual_mode"] == "intervention" and node["manual_approved"]
            ), "直接开始的节点类型或批准无效")
        pool_ids = list(node["capacity_pool_ids"])
        if key[0] not in activated:
            activated.add(key[0])
            pool_ids.extend(
                identity for identity in task["capacity_pool_ids"]
                if not any(holder["task_id"] == key[0] for holder in facts.pools[identity]["holders"])
            )
        for identity in pool_ids:
            used[identity] += 1
            limit = facts.pools[identity]["limit"]
            check(limit is None or used[identity] <= limit, "整批意图在输入快照上已超容量")
    state = result["next_state"]
    check(body["checkpoint"]["state"] is None or state is not None, "不能静默丢弃已提交模型")
    if state is not None:
        try:
            state_bytes(
                state, formats=state_formats if state_formats is not None else {state["format"]},
                max_bytes=(limits or DEFAULT_LIMITS)["max_state_bytes"],
            )
        except EngineError as error:
            raise EngineError("INVALID_DECISION", "候选检查点无效") from error
    forecast = result["forecast"]
    if forecast is not None:
        check("forecast.v1" in capabilities, "未声明预测能力")
        check(set(forecast["partial_task_ids"]) <= facts.tasks.keys(), "预测 Task 引用不存在")
        for entry in forecast["nodes"]:
            key = node_key(entry["node"])
            check(key in facts.nodes and entry["end_offset_ms"] >= entry["start_offset_ms"], "预测节点或时间无效")
            option = facts.options.get(entry["option_id"])
            check(entry["option_id"] is None or (option is not None and option["node"] == entry["node"]), "预测候选引用无效")
