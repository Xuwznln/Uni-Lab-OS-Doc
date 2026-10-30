"""复制当前已物化图和控制区域，不求值条件，也不展开未来循环。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from unilabos.scheduling.protocol import require

_DONE = {"succeeded", "skipped", "failed", "canceled", "timeout"}
_CONTROL = {"condition", "repeat_until"}


class RuntimeGraph:
    """把循环子 DAG 统一映射到 Task 内的实际节点身份。"""

    def __init__(self, task: dict[str, Any], jobs: list[dict[str, Any]], run: Any) -> None:
        self.task_id = task["uuid"]
        self.nodes: dict[str, Any] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.predecessors: dict[str, list[str]] = defaultdict(list)
        self.loops: dict[str, Any] = {}
        self.jobs = {
            str((job.get("control_data") or {}).get("runtime_node_id") or job["workflow_node_uuid"]): job
            for job in jobs if job.get("current_attempt", True)
        }
        self._collect(run)
        self.actual_by_template = {
            self.jobs[identity]["workflow_node_uuid"]: identity
            for identity in self.nodes if identity in self.jobs
        }
        planned = {node["uuid"]: node for node in task["execution_plan"]["nodes"]}
        self.parent: dict[str, str | None] = {}
        for identity in self.nodes:
            template = self.jobs[identity]["workflow_node_uuid"] if identity in self.jobs else identity
            parent = planned.get(template, {}).get("parent_uuid")
            visited = set()
            while parent and planned.get(parent, {}).get("kind") not in _CONTROL:
                require(parent not in visited, "SNAPSHOT_INVALID", "冻结节点的归属链存在环")
                visited.add(parent)
                parent = planned.get(parent, {}).get("parent_uuid")
            actual_parent = self.actual_by_template.get(parent) if parent else None
            require(parent is None or actual_parent is not None, "SNAPSHOT_INVALID", "当前节点缺少真实父控制区域")
            self.parent[identity] = self.region_id(actual_parent) if actual_parent else None

    def _collect(self, run: Any) -> None:
        snapshot = run.snapshot()
        for identity, state in snapshot["nodes"].items():
            require(identity not in self.nodes, "SNAPSHOT_INVALID", "当前图存在重复运行节点")
            self.nodes[identity] = run.node(identity)
            self.states[identity] = state
        for edge in snapshot["edges"]:
            self.predecessors[edge["target"]].append(edge["source"])
        for identity, runtime in run._repeat_runtime.items():
            self.loops[identity] = runtime
            if runtime.round_run is not None:
                self._collect(runtime.round_run)

    def region_id(self, identity: str) -> str:
        return self.task_id + ":" + identity

    def ref(self, identity: str) -> dict[str, str]:
        return {"task_id": self.task_id, "node_id": identity}

    def loop_transition_ready(self, identity: str) -> bool:
        """已进入的循环可以结算轮末或物化续轮，不把 controller 改回 pending。"""
        runtime = self.loops.get(identity)
        return bool(
            runtime is not None and runtime.iteration_index >= 0
            and self.states[identity]["state"] == "dispatched"
            and (runtime.round_run is None or runtime.round_run.state.value in {"success", "failed"})
        )

    def regions(self, projected: list[dict[str, Any]]) -> list[dict[str, Any]]:
        values = {node["ref"]["node_id"]: node for node in projected}
        regions: dict[str, dict[str, Any]] = {}
        for identity, node in self.nodes.items():
            if node.executor_kind not in _CONTROL:
                continue
            region_id = self.region_id(identity)
            members = {key for key, parent in self.parent.items() if parent == region_id}
            inputs: set[str] = set()
            bindings = dict(node.param.get("bindings") or {})
            if node.executor_kind == "repeat_until":
                runtime = self.loops[identity]
                bindings.update(node.param.get("next_carry" if runtime.iteration_index >= 0 else "initial_carry") or {})
            for binding in bindings.values():
                if binding.get("kind") == "node_result":
                    source = binding.get("node_uuid")
                    actual = source if source in self.nodes else self.actual_by_template.get(source)
                    # 尚未物化的模板结果不是当前真实输入节点。
                    if isinstance(actual, str) and actual in values:
                        inputs.add(actual)
            entries = {key for key in members if not (set(self.predecessors[key]) & members)}
            non_exits = {parent for key in members for parent in self.predecessors[key]} & members
            terminal = values[identity]["status"] in _DONE
            if node.executor_kind == "condition":
                activated = values[identity]["status"] in {"succeeded", "running"}
                iteration = None
                tail = not terminal
            else:
                runtime = self.loops[identity]
                activated = runtime.round_run is not None
                iteration = runtime.iteration_index if runtime.iteration_index >= 0 else None
                tail = not terminal
            complete = terminal and all(values[key]["status"] in _DONE for key in members)
            phase = (
                "resolved" if complete else "body_active" if activated
                else "awaiting_result" if any(values[key]["status"] not in _DONE for key in inputs)
                else "pending"
            )
            regions[region_id] = {
                "region_id": region_id, "task_id": self.task_id, "controller": self.ref(identity),
                "parent_region_id": self.parent[identity],
                "kind": "loop" if node.executor_kind == "repeat_until" else "condition",
                "phase": phase, "iteration_no": iteration,
                "members": [self.ref(key) for key in sorted(members)],
                "entry_nodes": [self.ref(key) for key in sorted(entries)],
                "exit_nodes": [self.ref(key) for key in sorted(members - non_exits)],
                "input_nodes": [self.ref(key) for key in sorted(inputs)],
                "unmaterialized_tail": tail,
            }
        # 子区域未完成时，已成功的条件 controller 不能让父区域提前完成。
        for region in regions.values():
            parent = region["parent_region_id"]
            visited = {region["region_id"]}
            while parent is not None:
                require(parent not in visited and parent in regions, "SNAPSHOT_INVALID", "控制区域父链无效")
                visited.add(parent)
                ancestor = regions[parent]
                if region["unmaterialized_tail"]:
                    ancestor["unmaterialized_tail"] = True
                if region["phase"] != "resolved" and ancestor["phase"] == "resolved":
                    ancestor["phase"] = "body_active"
                parent = ancestor["parent_region_id"]
        return list(regions.values())
