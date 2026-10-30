"""构造用于调度回归的公开协议工作负载。"""

from __future__ import annotations

import json
import random
import uuid
from copy import deepcopy

from unilabos.scheduling.protocol import CONTRACT_DIR, decode_message, encode_message

CASES = {
    item["name"]: item["message"]
    for item in json.loads((CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_text(encoding="utf-8"))["cases"]
}
NS = uuid.UUID("12345678-1234-4234-8234-123456789abc")


def gen_workload(n_tasks, chain_len, n_devices, alt=2, seed=0, fan=False):
    rnd = random.Random(seed)
    devices = [f"dev-{i}" for i in range(n_devices)]
    tasks = []
    for t in range(n_tasks):
        nodes = []
        for k in range(chain_len):
            preds = [] if k == 0 else [f"n{k - 1}"]
            if fan and k >= 2:
                preds = [f"n{k - 2}"]
            devs = rnd.sample(devices, min(alt, n_devices))
            nodes.append(dict(node_id=f"n{k}", preds=preds, devices=devs, dur=rnd.randint(1, 20)))
        tasks.append(dict(task_id=f"T{t:03d}", priority=rnd.choice([0, 0, 0, 5]), nodes=nodes))
    return tasks, devices


def snapshot_from_workload(tasks, devices, *, origin_ms=1_800_000_000_000, bad_option=None):
    task_rows, node_rows, options = [], [], []
    for qi, task in enumerate(tasks):
        task_rows.append({
            "task_id": task["task_id"], "queue_seq": str(qi + 1), "created_at_ms": origin_ms,
            "priority": task["priority"], "wait_since_ms": origin_ms, "status": "pending",
            "admission": "admitted", "control": "active", "execution_mode": "normal",
            "normal_progression_allowed": True, "terminal_settled": False,
            "known_graph_complete": True, "blockers": [], "workflow_id": "wf", "capacity_pool_ids": [],
        })
        for node in task["nodes"]:
            key = (task["task_id"], node["node_id"])
            ready = not node["preds"]
            node_rows.append({
                "ref": {"task_id": key[0], "node_id": key[1]}, "job_id": f"job-{key[0]}-{key[1]}",
                "attempt_no": 0, "kind": "device", "status": "pending",
                "predecessors": [{"task_id": key[0], "node_id": pred} for pred in node["preds"]],
                "ready": ready, "parameters_final": True, "parameters_revision": "1",
                "parameters": {"duration_s": node["dur"]}, "action_id": "sim.act", "action_revision": "r1",
                "information_boundary": False, "manual_approved": False, "step_authorized": False,
                "blockers": [], "control_region_id": None, "manual_mode": "none", "capacity_pool_ids": [],
            })
            for device in node["devices"]:
                params = {"duration_s": node["dur"]}
                if bad_option == (key[0], key[1], device):
                    params = {"time": 1792400000}
                options.append({
                    "option_id": f"opt-{key[0]}-{key[1]}-{device}",
                    "node": {"task_id": key[0], "node_id": key[1]},
                    "attempt_no": 0, "device_id": device, "resource_ids": [device],
                    "eligible_now": ready, "parameters_revision": "1",
                    "resolved_parameters": params, "binding_facts": {},
                    "blockers": [], "timing_profile": None,
                })
    return {
        "revision": "1", "captured_at_ms": origin_ms, "feedback_watermark": "0", "complete": True,
        "execution_gate": "open",
        "task_view": {"tasks": task_rows, "nodes": node_rows, "control_regions": []},
        "resource_view": {
            "resources": [
                {"resource_id": device, "kind": "device", "availability": "available", "attributes": {}}
                for device in devices
            ],
            "conflicts": [], "options": options, "allocations": [], "intervals": [], "waiters": [],
        },
        "inflight": [], "head": None, "lifecycle": "running", "capacity_view": {"pools": []},
    }


def decide_request(engine_id, snapshot, *, instance_id, run_epoch, config_id, timeout_ms=30000, budget_ms=1000):
    return {
        "protocol": "unilab.scheduler", "schema_version": "1.0", "operation": "decide",
        "request_id": str(uuid.uuid4()), "timeout_ms": timeout_ms,
        "body": {
            "instance_id": instance_id, "domain_id": "example-station", "run_epoch": run_epoch,
            "compute_budget_ms": budget_ms, "snapshot": snapshot,
            "checkpoint": {
                "revision": "0", "feedback_cursor": "0", "engine_id": engine_id,
                "config_id": config_id, "state": None,
            },
            "feedback": {"after_seq": "0", "through_seq": "0", "has_more": False, "events": []},
            "apply_budget_ms": 5000,
        },
    }


def create_engine(engine, config=None):
    request = deepcopy(CASES["create_request"])
    request["body"].update(
        instance_id=str(uuid.uuid4()), run_epoch=str(uuid.uuid4()),
        config_id=str(uuid.uuid4()), config={} if config is None else config,
    )
    if engine.engine_id == "unilab.cp_sat":
        request["body"]["required_capabilities"] = ["core.v1"]
        request["body"]["checkpoint"] = {
            "revision": "0", "feedback_cursor": "0", "engine_id": engine.engine_id,
            "config_id": request["body"]["config_id"], "state": None,
        }
    response = decode_message(engine.call(encode_message(request), timeout_ms=request["timeout_ms"]))
    assert response["ok"], response
    return request["body"]
