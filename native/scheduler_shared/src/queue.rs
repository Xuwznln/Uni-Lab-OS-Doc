use crate::{protocol::*, runtime::Algorithm, validation::*};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub fn config() -> Value {
    json!({"queue_order":"fifo","paused_task":"hold","failed_task":"advance_after_settlement","canceled_task":"advance_after_settlement","manual_outside_scope":"reject"})
}
pub fn intent(
    request: &Value,
    node: &Value,
    option: Option<&Value>,
    flight: Option<&Value>,
) -> Value {
    let kind = if flight.is_some() {
        "continue_attempt"
    } else if ["confirm_then_execute", "material_io"].contains(&s(&node["manual_mode"])) {
        "admit_manual"
    } else {
        "start"
    };
    let identity = format!(
        "{}:{}:{}:{}",
        s(&node["ref"]["task_id"]),
        s(&node["ref"]["node_id"]),
        u(&node["attempt_no"]),
        kind
    );
    let id = uuid::Uuid::new_v5(
        &uuid::Uuid::parse_str(s(&request["request_id"])).unwrap(),
        identity.as_bytes(),
    );
    json!({"intent_id":id.to_string(),"node":node["ref"],"attempt_no":node["attempt_no"],"parameters_revision":node["parameters_revision"],"kind":kind,"option_id":option.map(|v|&v["option_id"]),"attempt_id":flight.map(|v|&v["attempt_id"])})
}
pub fn choose_dispatch(
    request: &Value,
    facts: &Facts,
    scope: &[String],
    preferred: Option<&[String]>,
) -> Vec<Value> {
    let mut used: BTreeMap<String, u64> = facts
        .pools
        .iter()
        .map(|(k, v)| (k.clone(), u(&v["used"])))
        .collect();
    let (mut activated, mut picked, mut occupied) =
        (BTreeSet::new(), BTreeSet::new(), BTreeSet::new());
    let conflicts = conflict_graph(&request["body"]["snapshot"]["resource_view"]);
    let mut intents = vec![];
    let mut selected: Vec<&Value> = vec![];
    for flight in facts.inflight.values() {
        let key = node_key(&flight["node"]);
        if scope.contains(&key.0) && b(&flight["continuation_allowed"]) {
            intents.push(intent(request, &facts.nodes[&key], None, Some(flight)));
            picked.insert(key);
            occupied.extend(strings(&flight["resource_ids"]));
        }
    }
    let ids: Vec<_> = preferred
        .map(|v| v.to_vec())
        .unwrap_or_else(|| facts.options.keys().cloned().collect());
    for id in ids {
        let option = &facts.options[&id];
        let key = node_key(&option["node"]);
        let node = &facts.nodes[&key];
        let task = &facts.tasks[&key.0];
        if !scope.contains(&key.0)
            || picked.contains(&key)
            || !b(&option["eligible_now"])
            || !node_allowed(task, node)
            || node["status"] != "pending"
            || !b(&node["ready"])
            || !b(&node["parameters_final"])
            || node["job_id"].is_null()
            || !["device", "manual"].contains(&s(&node["kind"]))
            || (node["manual_mode"] == "intervention" && !b(&node["manual_approved"]))
        {
            continue;
        }
        let resources = strings(&option["resource_ids"]);
        if !resources.is_disjoint(&occupied)
            || resources
                .iter()
                .any(|r| conflicts.get(r).is_some_and(|v| !v.is_disjoint(&occupied)))
            || selected
                .iter()
                .any(|v| bindings_conflict(option, v, &facts.intervals, &conflicts))
        {
            continue;
        }
        let mut pools: Vec<String> = arr(&node["capacity_pool_ids"])
            .iter()
            .map(|v| s(v).into())
            .collect();
        if !activated.contains(&key.0) {
            pools.extend(
                arr(&task["capacity_pool_ids"])
                    .iter()
                    .filter(|id| {
                        !arr(&facts.pools[s(id)]["holders"])
                            .iter()
                            .any(|h| h["task_id"] == task["task_id"])
                    })
                    .map(|v| s(v).to_owned()),
            );
        }
        if pools.iter().any(|p| {
            !facts.pools[p]["limit"].is_null() && used[p] + 1 > u(&facts.pools[p]["limit"])
        }) {
            continue;
        }
        for p in pools {
            *used.get_mut(&p).unwrap() += 1;
        }
        activated.insert(key.0.clone());
        picked.insert(key);
        selected.push(option);
        intents.push(intent(request, node, Some(option), None));
    }
    intents
}
fn queue_order(f: &Facts, id: &str) -> (u64, String) {
    (counter(&f.tasks[id]["queue_seq"]).unwrap(), id.to_owned())
}
fn migration_owner(request: &Value, f: &Facts, task_id: &str) -> Option<String> {
    let requested: BTreeSet<String> = f
        .options
        .values()
        .filter(|o| s(&o["node"]["task_id"]) == task_id)
        .flat_map(|o| arr(&o["blockers"]))
        .filter(|b| b["code"] == "RESOURCE_BUSY")
        .flat_map(|b| strings(&b["resource_ids"]))
        .collect();
    let mut blocked = requested.clone();
    for edge in arr(&request["body"]["snapshot"]["resource_view"]["conflicts"]) {
        if requested.contains(s(&edge["left"])) || requested.contains(s(&edge["right"])) {
            blocked.insert(s(&edge["left"]).into());
            blocked.insert(s(&edge["right"]).into());
        }
    }
    let mut owners: BTreeSet<String> = f
        .allocations
        .values()
        .filter(|a| {
            a["kind"] == "continuous"
                && !strings(&a["resource_ids"]).is_disjoint(&blocked)
                && s(&a["owner_task_id"]) != task_id
                && f.tasks
                    .get(s(&a["owner_task_id"]))
                    .is_some_and(|t| t["status"] == "running")
        })
        .map(|a| s(&a["owner_task_id"]).into())
        .collect();
    for id in arr(&f.tasks[task_id]["capacity_pool_ids"]) {
        let pool = &f.pools[s(id)];
        if pool["limit"].is_null()
            || u(&pool["used"]) < u(&pool["limit"])
            || arr(&pool["holders"])
                .iter()
                .any(|h| s(&h["task_id"]) == task_id)
        {
            continue;
        }
        owners.extend(
            arr(&pool["holders"])
                .iter()
                .filter(|h| f.tasks[s(&h["task_id"])]["status"] == "running")
                .map(|h| s(&h["task_id"]).to_owned()),
        );
    }
    owners.into_iter().min_by_key(|id| queue_order(f, id))
}
#[derive(Default)]
pub struct QueueEngine;
impl Algorithm for QueueEngine {
    fn engine_id(&self) -> &str {
        "unilab.queue"
    }
    fn config_schema(&self) -> Value {
        let cfg = config();
        let properties: BTreeMap<String, Value> = cfg
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), json!({"const":v})))
            .collect();
        json!({"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":properties,"required":cfg.as_object().unwrap().keys().collect::<Vec<_>>(),"additionalProperties":false})
    }
    fn decide(
        &mut self,
        request: &Value,
        f: &Facts,
        _config: &Value,
        _state: Value,
    ) -> Result<(Value, Value)> {
        let body = &request["body"];
        let snap = &body["snapshot"];
        let mut d = json!({"status":"wait","task_scope":[],"dispatch_now":[],"wakeup_after_ms":null,"reasons":[],"forecast":null});
        if b(&body["feedback"]["has_more"]) {
            d["status"] = json!("sync");
            d["task_scope"] = Value::Null;
            d["reasons"] = json!([reason("FEEDBACK_CATCHUP", "反馈尚未追平", None)]);
            return Ok((d, Value::Null));
        }
        let remaining: Vec<String> = f
            .tasks
            .iter()
            .filter(|(_, t)| !b(&t["terminal_settled"]))
            .map(|(k, _)| k.clone())
            .collect();
        if remaining.is_empty() {
            d["reasons"] = json!([reason("IDLE", "没有未结算任务", None)]);
            return Ok((d, Value::Null));
        }
        let prev = arr(&snap["head"]["task_scope"]);
        let mut identity = if prev.len() == 1 && remaining.iter().any(|id| id == s(&prev[0])) {
            s(&prev[0]).to_owned()
        } else {
            remaining
                .into_iter()
                .min_by_key(|id| queue_order(f, id))
                .unwrap()
        };
        if let Some(owner) = migration_owner(request, f, &identity) {
            identity = owner;
        }
        d["task_scope"] = json!([identity]);
        if snap["lifecycle"] != "running" || snap["execution_gate"] != "open" {
            d["reasons"] = json!([reason(
                "EXECUTION_HELD",
                "平台执行屏障关闭",
                Some(&identity)
            )]);
            return Ok((d, Value::Null));
        }
        if f.inflight
            .values()
            .any(|v| s(&v["node"]["task_id"]) != identity)
        {
            let mut existing: Vec<String> = f
                .inflight
                .values()
                .map(|v| s(&v["node"]["task_id"]).to_owned())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            existing.sort_by_key(|id| queue_order(f, id));
            d["dispatch_now"] = json!(choose_dispatch(request, f, &existing, Some(&[])));
            d["task_scope"] = json!(existing);
            if !arr(&d["dispatch_now"]).is_empty() {
                d["status"] = json!("advance");
            }
            d["reasons"] = json!([reason(
                "MIGRATION_DRAIN",
                "等待其他 Task 已在途操作收敛",
                Some(&identity)
            )]);
            return Ok((d, Value::Null));
        }
        d["dispatch_now"] = json!(choose_dispatch(
            request,
            f,
            std::slice::from_ref(&identity),
            None
        ));
        let task = &f.tasks[&identity];
        let ready = f.nodes.iter().any(|(k, node)| {
            k.0 == identity
                && b(&node["ready"])
                && b(&node["parameters_final"])
                && (["condition", "loop", "workflow_command", "material_source"]
                    .contains(&s(&node["kind"]))
                    || (preparation_error(node) && task_capacity_available(task, &f.pools)))
                && node_allowed(task, node)
        });
        if !arr(&d["dispatch_now"]).is_empty() || ready {
            d["status"] = json!("advance");
        } else {
            d["reasons"] = json!([reason(
                "TASK_WAIT",
                "队首等待控制、依赖、资源或结算",
                Some(&identity)
            )]);
        }
        Ok((d, Value::Null))
    }
}
