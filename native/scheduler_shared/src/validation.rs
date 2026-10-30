use crate::protocol::*;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, VecDeque};

pub type NodeKey = (String, String);

/// 在返回 ABI 前独立检查整批意图；错误候选不能作为成功响应交给宿主。
pub fn validate_decision(
    request: &Value,
    result: &Value,
    facts: &Facts,
    capabilities: &[&str],
) -> Result<()> {
    let check = |ok, message| require(ok, "INVALID_DECISION", message);
    let body = &request["body"];
    let snapshot = &body["snapshot"];
    check(
        result["decision_id"] == request["request_id"]
            && ["instance_id", "domain_id", "run_epoch"]
                .iter()
                .all(|k| result[k] == body[k])
            && result["base_snapshot_revision"] == snapshot["revision"]
            && result["base_checkpoint_revision"] == body["checkpoint"]["revision"]
            && result["consumed_feedback_seq"] == body["feedback"]["through_seq"],
        "决策引用与输入不匹配",
    )?;
    check(
        result["status"] != "advance"
            || (snapshot["lifecycle"] == "running" && snapshot["execution_gate"] == "open"),
        "屏障关闭时不能推进",
    )?;
    if b(&body["feedback"]["has_more"]) {
        check(
            result["status"] == "sync" && result["forecast"].is_null(),
            "反馈追赶期间只允许同步",
        )?;
    }
    let scope = strings(&result["task_scope"]);
    if result["status"] == "sync" {
        check(
            result["task_scope"].is_null()
                && arr(&result["dispatch_now"]).is_empty()
                && result["forecast"].is_null(),
            "同步不能改变调度范围",
        )?;
    } else {
        check(
            result["task_scope"].is_array()
                && scope.len() == arr(&result["task_scope"]).len()
                && scope.iter().all(|k| facts.tasks.contains_key(k)),
            "决策任务范围无效",
        )?;
    }
    if result["status"] != "advance" {
        check(
            arr(&result["dispatch_now"]).is_empty() && !arr(&result["reasons"]).is_empty(),
            "等待必须说明原因且不能派发",
        )?;
    }
    let (mut nodes, mut intents, mut activated) =
        (BTreeSet::new(), BTreeSet::new(), BTreeSet::new());
    let mut used: BTreeMap<_, _> = facts
        .pools
        .iter()
        .map(|(k, p)| (k.clone(), u(&p["used"])))
        .collect();
    for intent in arr(&result["dispatch_now"]) {
        let key = node_key(&intent["node"]);
        check(
            facts.nodes.contains_key(&key)
                && nodes.insert(key.clone())
                && intents.insert(s(&intent["intent_id"])),
            "执行意图身份无效或重复",
        )?;
        let node = &facts.nodes[&key];
        let task = &facts.tasks[&key.0];
        check(
            scope.contains(&key.0)
                && task_admitted(task)
                && n(&intent["attempt_no"]) == n(&node["attempt_no"])
                && intent["parameters_revision"] == node["parameters_revision"],
            "执行意图不属于已准入的当前节点",
        )?;
        if intent["kind"] == "continue_attempt" {
            let flight = facts.inflight.get(s(&intent["attempt_id"]));
            check(
                flight.is_some_and(|f| {
                    f["node"] == intent["node"]
                        && n(&f["attempt_no"]) == n(&intent["attempt_no"])
                        && f["parameters_revision"] == intent["parameters_revision"]
                        && f["start_state"] == "NOT_STARTED"
                        && b(&f["continuation_allowed"])
                }) && intent["option_id"].is_null()
                    && node["status"] == "running",
                "续行必须使用有许可的原 attempt",
            )?;
            continue;
        }
        check(
            node_allowed(task, node)
                && node["status"] == "pending"
                && b(&node["ready"])
                && b(&node["parameters_final"])
                && !node["job_id"].is_null(),
            "新执行意图不满足节点控制或参数条件",
        )?;
        check(
            facts.options.get(s(&intent["option_id"])).is_some_and(|o| {
                b(&o["eligible_now"])
                    && o["node"] == intent["node"]
                    && n(&o["attempt_no"]) == n(&intent["attempt_no"])
                    && o["parameters_revision"] == intent["parameters_revision"]
            }),
            "执行意图未引用当前合法候选",
        )?;
        check(
            if intent["kind"] == "admit_manual" {
                node["kind"] == "manual"
                    && ["confirm_then_execute", "material_io"].contains(&s(&node["manual_mode"]))
            } else {
                node["kind"] == "device"
                    || (node["kind"] == "manual"
                        && node["manual_mode"] == "intervention"
                        && b(&node["manual_approved"]))
            },
            "执行意图类型或人工批准无效",
        )?;
        let mut pools = strings(&node["capacity_pool_ids"]);
        if activated.insert(key.0.clone()) {
            pools.extend(
                strings(&task["capacity_pool_ids"])
                    .into_iter()
                    .filter(|id| {
                        !arr(&facts.pools[id]["holders"])
                            .iter()
                            .any(|h| s(&h["task_id"]) == key.0)
                    }),
            );
        }
        for id in pools {
            *used.get_mut(&id).unwrap() += 1;
            check(
                facts.pools[&id]["limit"].is_null() || used[&id] <= u(&facts.pools[&id]["limit"]),
                "整批意图超过输入容量",
            )?;
        }
    }
    check(
        body["checkpoint"]["state"].is_null() || !result["next_state"].is_null(),
        "不能静默丢弃已提交模型",
    )?;
    let forecast = &result["forecast"];
    if !forecast.is_null() {
        check(capabilities.contains(&"forecast.v1"), "未声明预测能力")?;
        check(
            strings(&forecast["partial_task_ids"])
                .iter()
                .all(|k| facts.tasks.contains_key(k)),
            "预测任务不存在",
        )?;
        for entry in arr(&forecast["nodes"]) {
            check(
                facts.nodes.contains_key(&node_key(&entry["node"]))
                    && n(&entry["end_offset_ms"]) >= n(&entry["start_offset_ms"]),
                "预测节点或时间无效",
            )?;
            check(
                entry["option_id"].is_null()
                    || facts
                        .options
                        .get(s(&entry["option_id"]))
                        .is_some_and(|o| o["node"] == entry["node"]),
                "预测候选引用无效",
            )?;
        }
    }
    Ok(())
}
pub fn node_key(v: &Value) -> NodeKey {
    (s(&v["task_id"]).into(), s(&v["node_id"]).into())
}
pub fn node_keys(v: &Value) -> BTreeSet<NodeKey> {
    arr(v).iter().map(node_key).collect()
}
pub fn automatic_allowed(t: &Value) -> bool {
    t["control"] == "active"
        && t["execution_mode"] == "normal"
        && b(&t["normal_progression_allowed"])
}
pub fn task_admitted(t: &Value) -> bool {
    ["pending", "running"].contains(&s(&t["status"]))
        && t["admission"] == "admitted"
        && ["active", "paused"].contains(&s(&t["control"]))
}
pub fn node_allowed(t: &Value, n: &Value) -> bool {
    task_admitted(t)
        && (automatic_allowed(t)
            || (["step", "single_node"].contains(&s(&t["execution_mode"]))
                && ["active", "paused"].contains(&s(&t["control"]))
                && b(&n["step_authorized"])))
}
pub fn task_capacity_available(t: &Value, pools: &BTreeMap<String, Value>) -> bool {
    arr(&t["capacity_pool_ids"]).iter().all(|id| {
        let p = &pools[s(id)];
        p["limit"].is_null()
            || u(&p["used"]) < u(&p["limit"])
            || arr(&p["holders"])
                .iter()
                .any(|h| h["task_id"] == t["task_id"])
    })
}
pub fn preparation_error(n: &Value) -> bool {
    arr(&n["blockers"])
        .iter()
        .any(|r| s(&r["code"]).starts_with("vendor.unilab.preparation."))
}
fn check(ok: bool, msg: &str) -> Result<()> {
    require(ok, "SNAPSHOT_INVALID", msg)
}
fn unique<T: Ord>(values: impl IntoIterator<Item = T>) -> Result<()> {
    let mut seen = BTreeSet::new();
    for v in values {
        check(seen.insert(v), "当前身份或引用重复")?;
    }
    Ok(())
}
fn index(v: &Value, key: &str) -> Result<BTreeMap<String, Value>> {
    let mut out = BTreeMap::new();
    for item in arr(v) {
        check(
            out.insert(s(&item[key]).into(), item.clone()).is_none(),
            "当前身份重复",
        )?;
    }
    Ok(out)
}
fn acyclic<T: Ord + Clone>(parents: BTreeMap<T, BTreeSet<T>>) -> Result<()> {
    let mut counts: BTreeMap<T, usize> =
        parents.iter().map(|(k, v)| (k.clone(), v.len())).collect();
    let mut children: BTreeMap<T, Vec<T>> = BTreeMap::new();
    for (k, values) in &parents {
        for parent in values {
            check(parents.contains_key(parent), "依赖引用不存在")?;
            children.entry(parent.clone()).or_default().push(k.clone());
        }
    }
    let mut ready: VecDeque<T> = counts
        .iter()
        .filter(|(_, v)| **v == 0)
        .map(|(k, _)| k.clone())
        .collect();
    let mut done = 0;
    while let Some(key) = ready.pop_front() {
        done += 1;
        if let Some(values) = children.get(&key) {
            for child in values {
                let count = counts.get_mut(child).unwrap();
                *count -= 1;
                if *count == 0 {
                    ready.push_back(child.clone());
                }
            }
        }
    }
    check(done == parents.len(), "依赖或包含关系有环")
}
#[derive(Clone, Default)]
pub struct Facts {
    pub tasks: BTreeMap<String, Value>,
    pub nodes: BTreeMap<NodeKey, Value>,
    pub regions: BTreeMap<String, Value>,
    pub resources: BTreeMap<String, Value>,
    pub options: BTreeMap<String, Value>,
    pub allocations: BTreeMap<String, Value>,
    pub intervals: BTreeMap<String, Value>,
    pub inflight: BTreeMap<String, Value>,
    pub pools: BTreeMap<String, Value>,
}
pub fn active_resources(option: &Value) -> Result<BTreeSet<String>> {
    let values = option["binding_facts"]
        .get("active_resource_ids")
        .unwrap_or(&option["resource_ids"]);
    check(
        values.is_array() && arr(values).iter().all(Value::is_string),
        "候选活动资源必须是字符串列表",
    )?;
    let active = strings(values);
    check(
        active.len() == arr(values).len() && active.is_subset(&strings(&option["resource_ids"])),
        "候选活动资源重复或不属于完整绑定",
    )?;
    Ok(active)
}
pub fn conflict_graph(view: &Value) -> BTreeMap<String, BTreeSet<String>> {
    let mut out: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for edge in arr(&view["conflicts"]) {
        out.entry(s(&edge["left"]).into())
            .or_default()
            .insert(s(&edge["right"]).into());
        out.entry(s(&edge["right"]).into())
            .or_default()
            .insert(s(&edge["left"]).into());
    }
    out
}
pub fn bindings_conflict(
    left: &Value,
    right: &Value,
    intervals: &BTreeMap<String, Value>,
    conflicts: &BTreeMap<String, BTreeSet<String>>,
) -> bool {
    let a = active_resources(left).unwrap_or_default();
    let b = active_resources(right).unwrap_or_default();
    let shared: BTreeSet<String> = intervals
        .values()
        .filter(|i| {
            arr(&i["members"]).contains(&left["node"])
                && arr(&i["members"]).contains(&right["node"])
        })
        .flat_map(|i| strings(&i["resource_ids"]))
        .collect();
    for x in arr(&left["resource_ids"]) {
        for y in arr(&right["resource_ids"]) {
            let (x, y) = (s(x), s(y));
            if x != y && !conflicts.get(x).is_some_and(|v| v.contains(y)) {
                continue;
            }
            if x == y && shared.contains(x) && !(a.contains(x) && b.contains(y)) {
                continue;
            }
            return true;
        }
    }
    false
}
pub fn validate_snapshot(request: &Value, limits: &Value) -> Result<Facts> {
    let body = &request["body"];
    let snap = &body["snapshot"];
    let tv = &snap["task_view"];
    let rv = &snap["resource_view"];
    for (name, values) in [
        ("max_tasks", &tv["tasks"]),
        ("max_nodes", &tv["nodes"]),
        ("max_options", &rv["options"]),
        ("max_feedback_events", &body["feedback"]["events"]),
    ] {
        require(
            arr(values).len() <= u(&limits[name]) as usize,
            "LIMIT_EXCEEDED",
            "快照超过协商配额",
        )?;
    }
    let mut nodes = BTreeMap::new();
    for node in arr(&tv["nodes"]) {
        check(
            nodes.insert(node_key(&node["ref"]), node.clone()).is_none(),
            "当前节点身份重复",
        )?;
    }
    let f = Facts {
        tasks: index(&tv["tasks"], "task_id")?,
        nodes,
        regions: index(&tv["control_regions"], "region_id")?,
        resources: index(&rv["resources"], "resource_id")?,
        options: index(&rv["options"], "option_id")?,
        allocations: index(&rv["allocations"], "allocation_id")?,
        intervals: index(&rv["intervals"], "interval_id")?,
        inflight: index(&snap["inflight"], "attempt_id")?,
        pools: index(&snap["capacity_view"]["pools"], "pool_id")?,
    };
    let mut counters = vec![
        &snap["revision"],
        &snap["feedback_watermark"],
        &body["checkpoint"]["revision"],
        &body["checkpoint"]["feedback_cursor"],
        &body["feedback"]["after_seq"],
        &body["feedback"]["through_seq"],
    ];
    counters.extend(f.tasks.values().map(|v| &v["queue_seq"]));
    counters.extend(f.nodes.values().map(|v| &v["parameters_revision"]));
    counters.extend(f.options.values().map(|v| &v["parameters_revision"]));
    counters.extend(f.inflight.values().map(|v| &v["parameters_revision"]));
    counters.extend(arr(&body["feedback"]["events"]).iter().map(|v| &v["seq"]));
    for c in counters {
        counter(c).map_err(|_| Error::new("SNAPSHOT_INVALID", "快照计数器越界"))?;
    }
    unique(f.tasks.values().map(|v| s(&v["queue_seq"])))?;
    check(
        snap["lifecycle"] == "running" || snap["execution_gate"] == "held",
        "非运行阶段必须关闭域门禁",
    )?;
    check(
        u(&body["compute_budget_ms"]) <= u(&request["timeout_ms"]),
        "计算预算超过调用预算",
    )?;
    let head = &snap["head"];
    if !head.is_null() {
        unique(arr(&head["task_scope"]).iter().map(s))?;
        check(
            arr(&head["task_scope"])
                .iter()
                .all(|id| f.tasks.contains_key(s(id))),
            "head Task 引用不存在",
        )?;
        if head["status"] == "wait" {
            check(
                !b(&head["authorization_valid"]) && head["authorization_expires_at_ms"].is_null(),
                "wait 不能保留授权",
            )?;
        }
        if b(&head["authorization_valid"]) {
            check(
                head["run_epoch"] == body["run_epoch"]
                    && head["status"] == "advance"
                    && snap["lifecycle"] == "running"
                    && snap["execution_gate"] == "open"
                    && !head["authorization_expires_at_ms"].is_null()
                    && u(&head["authorization_expires_at_ms"]) > u(&snap["captured_at_ms"]),
                "head 授权已失效",
            )?;
        }
    }
    for (key, node) in &f.nodes {
        check(f.tasks.contains_key(&key.0), "节点所属 Task 不存在")?;
        unique(arr(&node["predecessors"]).iter().map(node_key))?;
        check(
            arr(&node["predecessors"]).iter().all(|p| {
                let p = node_key(p);
                p.0 == key.0 && f.nodes.contains_key(&p)
            }),
            "节点前驱必须属于同一 Task",
        )?;
        check(
            (node["kind"] == "manual") == (node["manual_mode"] != "none"),
            "人工节点类型与模式不一致",
        )?;
        check(
            node["control_region_id"].is_null()
                || f.regions.contains_key(s(&node["control_region_id"])),
            "节点区域引用不存在",
        )?;
    }
    acyclic(
        f.nodes
            .iter()
            .map(|(k, n)| (k.clone(), node_keys(&n["predecessors"])))
            .collect(),
    )?;
    validate_regions(&f)?;
    validate_resources(&f, rv)?;
    unique(f.inflight.values().map(|v| node_key(&v["node"])))?;
    for flight in f.inflight.values() {
        let key = node_key(&flight["node"]);
        check(f.nodes.contains_key(&key), "在途节点不存在")?;
        let node = &f.nodes[&key];
        let task = &f.tasks[&key.0];
        check(
            ["running", "uncertain"].contains(&s(&node["status"]))
                && ["attempt_no", "job_id", "parameters_revision"]
                    .iter()
                    .all(|k| same_scalar(&flight[k], &node[k])),
            "在途身份与节点不一致",
        )?;
        check(
            arr(&flight["resource_ids"])
                .iter()
                .all(|v| f.resources.contains_key(s(v))),
            "在途资源不存在",
        )?;
        unique(arr(&flight["command_ids"]).iter().map(s))?;
        if flight["start_state"] == "NOT_STARTED" {
            check(
                flight["started_at_ms"].is_null(),
                "未开始对象不能有真实开始时间",
            )?;
        }
        if b(&flight["continuation_allowed"]) {
            check(
                flight["start_state"] == "NOT_STARTED"
                    && snap["lifecycle"] == "running"
                    && snap["execution_gate"] == "open"
                    && task_admitted(task)
                    && (automatic_allowed(task)
                        || ["step", "single_node"].contains(&s(&task["execution_mode"]))),
                "续行许可绕过开始或控制屏障",
            )?;
            if node["kind"] == "manual" {
                check(flight["manual_phase"] == "approved", "人工续行缺少批准")?;
            }
        }
    }
    for task in f.tasks.values() {
        if b(&task["terminal_settled"]) {
            check(
                ["succeeded", "failed", "canceled"].contains(&s(&task["status"]))
                    && !f
                        .inflight
                        .values()
                        .any(|v| v["node"]["task_id"] == task["task_id"])
                    && !f
                        .allocations
                        .values()
                        .any(|v| v["owner_task_id"] == task["task_id"]),
                "终态结算覆盖仍在途或持有资源的 Task",
            )?;
        }
    }
    validate_capacity(&f)?;
    let feedback = &body["feedback"];
    let after = counter(&feedback["after_seq"])?;
    let through = counter(&feedback["through_seq"])?;
    let watermark = counter(&snap["feedback_watermark"])?;
    require(
        after == counter(&body["checkpoint"]["feedback_cursor"])?
            && after <= through
            && through <= watermark
            && b(&feedback["has_more"]) == (through < watermark)
            && through - after == arr(&feedback["events"]).len() as u64
            && arr(&feedback["events"])
                .iter()
                .enumerate()
                .all(|(i, v)| counter(&v["seq"]).ok() == after.checked_add(i as u64 + 1)),
        "FEEDBACK_GAP",
        "反馈批次必须连续并与检查点及水位一致",
    )?;
    unique(arr(&feedback["events"]).iter().map(|v| s(&v["event_id"])))?;
    Ok(f)
}
fn validate_regions(f: &Facts) -> Result<()> {
    unique(f.regions.values().map(|r| node_key(&r["controller"])))?;
    acyclic(
        f.regions
            .iter()
            .map(|(k, v)| {
                (
                    k.clone(),
                    if v["parent_region_id"].is_null() {
                        BTreeSet::new()
                    } else {
                        BTreeSet::from([s(&v["parent_region_id"]).into()])
                    },
                )
            })
            .collect(),
    )?;
    for (id, r) in &f.regions {
        let key = node_key(&r["controller"]);
        check(
            f.nodes.contains_key(&key) && key.0 == s(&r["task_id"]),
            "区域 controller 无效",
        )?;
        check(
            f.nodes[&key]["kind"] == r["kind"]
                && f.nodes[&key]["control_region_id"] == r["parent_region_id"],
            "controller 必须属于父区域且类型一致",
        )?;
        let members = node_keys(&r["members"]);
        let direct: BTreeSet<_> = f
            .nodes
            .iter()
            .filter(|(_, n)| s(&n["control_region_id"]) == id)
            .map(|(k, _)| k.clone())
            .collect();
        check(
            members.len() == arr(&r["members"]).len()
                && members == direct
                && members.iter().all(|k| k.0 == s(&r["task_id"])),
            "区域 members 必须恰好包含直属节点",
        )?;
        let entries: BTreeSet<_> = members
            .iter()
            .filter(|k| node_keys(&f.nodes[*k]["predecessors"]).is_disjoint(&members))
            .cloned()
            .collect();
        let non_exits: BTreeSet<_> = members
            .iter()
            .flat_map(|k| node_keys(&f.nodes[k]["predecessors"]))
            .filter(|k| members.contains(k))
            .collect();
        check(
            node_keys(&r["entry_nodes"]) == entries
                && node_keys(&r["exit_nodes"]) == members.difference(&non_exits).cloned().collect(),
            "区域入口出口与结构 DAG 不一致",
        )?;
        for name in ["entry_nodes", "exit_nodes", "input_nodes"] {
            unique(arr(&r[name]).iter().map(node_key))?;
        }
        check(
            arr(&r["input_nodes"])
                .iter()
                .all(|v| f.nodes.contains_key(&node_key(v)) && v["task_id"] == r["task_id"]),
            "区域输入引用无效",
        )?;
        if r["kind"] == "condition" {
            check(r["iteration_no"].is_null(), "条件区域不能有循环轮次")?;
        } else if r["phase"] == "body_active" {
            check(!r["iteration_no"].is_null(), "活动循环体缺少轮次")?;
        }
        if r["phase"] == "resolved" {
            check(!b(&r["unmaterialized_tail"]), "已完成区域不能有未知尾部")?;
        }
        if !r["parent_region_id"].is_null() {
            let parent = &f.regions[s(&r["parent_region_id"])];
            check(parent["task_id"] == r["task_id"], "父区域必须属于同一 Task")?;
            if b(&r["unmaterialized_tail"]) {
                check(b(&parent["unmaterialized_tail"]), "未知尾部未传播到父区域")?;
            }
        }
    }
    Ok(())
}
fn validate_resources(f: &Facts, view: &Value) -> Result<()> {
    for option in f.options.values() {
        let key = node_key(&option["node"]);
        check(f.nodes.contains_key(&key), "候选节点不存在")?;
        check(
            ["attempt_no", "parameters_revision"]
                .iter()
                .all(|k| same_scalar(&option[k], &f.nodes[&key][k])),
            "候选的 attempt 或参数修订不一致",
        )?;
        check(
            arr(&option["resource_ids"])
                .iter()
                .all(|v| f.resources.contains_key(s(v))),
            "候选资源不存在",
        )?;
        unique(arr(&option["resource_ids"]).iter().map(s))?;
        let active = active_resources(option)?;
        let resources = strings(&option["resource_ids"]);
        let reserved: BTreeSet<_> = resources.difference(&active).cloned().collect();
        let covered: BTreeSet<String> = f
            .intervals
            .values()
            .filter(|i| arr(&i["members"]).contains(&option["node"]))
            .flat_map(|i| strings(&i["resource_ids"]))
            .collect();
        check(
            reserved.is_subset(&covered),
            "候选纯所有权资源缺少显式区间成员关系",
        )?;
    }
    let mut conflicts = BTreeSet::new();
    for edge in arr(&view["conflicts"]) {
        let (a, b) = (s(&edge["left"]), s(&edge["right"]));
        check(
            a != b && f.resources.contains_key(a) && f.resources.contains_key(b),
            "资源冲突引用无效",
        )?;
        check(
            conflicts.insert(if a < b { (a, b) } else { (b, a) }),
            "资源冲突关系重复",
        )?;
    }
    for a in f.allocations.values() {
        check(
            arr(&a["resource_ids"])
                .iter()
                .all(|v| f.resources.contains_key(s(v))),
            "占用资源不存在",
        )?;
        unique(arr(&a["resource_ids"]).iter().map(s))?;
        check(
            a["owner_task_id"].is_null() || f.tasks.contains_key(s(&a["owner_task_id"])),
            "占用所属 Task 不存在",
        )?;
        for k in ["owner_node", "release_boundary"] {
            check(
                a[k].is_null()
                    || (f.nodes.contains_key(&node_key(&a[k]))
                        && a[k]["task_id"] == a["owner_task_id"]),
                "占用节点引用无效",
            )?;
        }
    }
    acyclic(
        f.intervals
            .iter()
            .map(|(k, v)| (k.clone(), strings(&v["contained_in"])))
            .collect(),
    )?;
    for interval in f.intervals.values() {
        let owner = &interval["owner_task_id"];
        check(
            f.tasks.contains_key(s(owner))
                && arr(&interval["resource_ids"])
                    .iter()
                    .all(|v| f.resources.contains_key(s(v))),
            "区间所属 Task 或资源不存在",
        )?;
        unique(arr(&interval["contained_in"]).iter().map(s))?;
        unique(arr(&interval["members"]).iter().map(node_key))?;
        for v in arr(&interval["members"]).iter().chain(
            [&interval["acquire_node"], &interval["release_node"]]
                .into_iter()
                .filter(|v| !v.is_null()),
        ) {
            check(
                f.nodes.contains_key(&node_key(v)) && v["task_id"] == *owner,
                "区间节点引用无效",
            )?;
        }
        for id in arr(&interval["held_allocation_ids"]) {
            check(
                f.allocations.get(s(id)).is_some_and(|a| {
                    a["owner_task_id"] == *owner
                        && !strings(&a["resource_ids"])
                            .is_disjoint(&strings(&interval["resource_ids"]))
                }),
                "区间实际占用引用无效",
            )?;
        }
        for id in arr(&interval["contained_in"]) {
            check(
                f.intervals[s(id)]["owner_task_id"] == *owner,
                "包含区间跨 Task",
            )?;
        }
    }
    for waiter in arr(&view["waiters"]) {
        let key = node_key(&waiter["node"]);
        check(
            f.nodes
                .get(&key)
                .is_some_and(|n| same_scalar(&n["attempt_no"], &waiter["attempt_no"]))
                && arr(&waiter["resource_ids"])
                    .iter()
                    .all(|v| f.resources.contains_key(s(v))),
            "资源等待者引用无效",
        )?;
    }
    Ok(())
}
fn validate_capacity(f: &Facts) -> Result<()> {
    for pool in f.pools.values() {
        check(
            (pool["scope"] == "domain") == pool["workflow_id"].is_null(),
            "容量池作用域不一致",
        )?;
        check(
            u(&pool["used"]) == arr(&pool["holders"]).len() as u64 + u(&pool["external_used"]),
            "容量使用计数不一致",
        )?;
        let mut holders = BTreeSet::new();
        for h in arr(&pool["holders"]) {
            let id = s(&h["task_id"]);
            check(f.tasks.contains_key(id), "容量持有 Task 不存在")?;
            if pool["scope"] == "workflow" {
                check(
                    f.tasks[id]["workflow_id"] == pool["workflow_id"],
                    "容量持有者不属于该工作流定义",
                )?;
            }
            let identity = if pool["kind"] == "active_tasks" {
                check(h["attempt_id"].is_null(), "Task 容量不能按 attempt 计数")?;
                id
            } else {
                check(
                    f.inflight
                        .get(s(&h["attempt_id"]))
                        .is_some_and(|v| v["node"]["task_id"] == h["task_id"]),
                    "Job 容量缺少对应在途对象",
                )?;
                s(&h["attempt_id"])
            };
            check(holders.insert(identity), "容量持有者重复")?;
        }
    }
    let applicable = |task: &Value, kind: &str| -> BTreeSet<String> {
        f.pools
            .iter()
            .filter(|(_, p)| {
                p["kind"] == kind
                    && (p["scope"] == "domain" || p["workflow_id"] == task["workflow_id"])
            })
            .map(|(k, _)| k.clone())
            .collect()
    };
    for task in f.tasks.values() {
        unique(arr(&task["capacity_pool_ids"]).iter().map(s))?;
        check(
            strings(&task["capacity_pool_ids"]) == applicable(task, "active_tasks"),
            "Task 未列出全部适用容量池",
        )?;
    }
    for (key, node) in &f.nodes {
        unique(arr(&node["capacity_pool_ids"]).iter().map(s))?;
        check(
            strings(&node["capacity_pool_ids"]) == applicable(&f.tasks[&key.0], "inflight_jobs"),
            "节点未列出全部适用容量池",
        )?;
    }
    for (id, flight) in &f.inflight {
        for pool in arr(&f.nodes[&node_key(&flight["node"])]["capacity_pool_ids"]) {
            check(
                arr(&f.pools[s(pool)]["holders"])
                    .iter()
                    .any(|h| s(&h["attempt_id"]) == id),
                "在途对象漏计容量",
            )?;
        }
    }
    Ok(())
}
