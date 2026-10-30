"""把工作流输出边界收敛到冻结执行计划中的真实来源。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class WorkflowBoundaryBindingError(ValueError):
    """工作流输出绑定无法唯一落到真实执行节点。"""


def flatten_output_bindings(
    *,
    graph_nodes: Sequence[Mapping[str, Any]],
    output_bindings: Mapping[str, Mapping[str, Any]],
    planned_node_uuids: set[str],
) -> dict[str, dict[str, Any]]:
    """把组合调用输出递归改写为执行计划中的叶子节点输出。

    参数：应用图节点、已验证的工作流输出绑定和冻结计划节点身份。返回：不再
    引用虚拟组合调用节点的独立绑定对象。异常：组合元数据缺失、映射成环、
    映射种类不受支持或最终来源不在计划中时抛
    ``WorkflowBoundaryBindingError``；运行时不得猜测来源。
    """

    nodes = {
        str(node.get("uuid") or ""): node
        for node in graph_nodes
        if isinstance(node, Mapping)
    }
    flattened: dict[str, dict[str, Any]] = {}
    for output_name, raw_binding in output_bindings.items():
        binding = dict(raw_binding)
        if binding.get("kind") == "workflow_input":
            flattened[output_name] = binding
            continue
        if binding.get("kind") != "node_output":
            raise WorkflowBoundaryBindingError("工作流输出绑定种类不受支持")
        seen: set[tuple[str, str]] = set()
        while True:
            node_uuid = str(binding.get("workflow_node_uuid") or "")
            handle_uuid = str(binding.get("source_handle_uuid") or "")
            identity = (node_uuid, handle_uuid)
            if not node_uuid or not handle_uuid or identity in seen:
                raise WorkflowBoundaryBindingError("工作流输出绑定身份缺失或成环")
            seen.add(identity)
            if node_uuid in planned_node_uuids:
                flattened[output_name] = binding
                break
            node = nodes.get(node_uuid)
            metadata = node.get("meta_data") if isinstance(node, Mapping) else None
            unilab = metadata.get("unilab") if isinstance(metadata, Mapping) else None
            composite = unilab.get("composite") if isinstance(unilab, Mapping) else None
            mappings = (
                composite.get("source_mappings")
                if isinstance(composite, Mapping)
                else None
            )
            mapped = (
                mappings.get(handle_uuid) if isinstance(mappings, Mapping) else None
            )
            if not isinstance(mapped, Mapping) or mapped.get("kind") != "node_output":
                raise WorkflowBoundaryBindingError(
                    "工作流输出引用的虚拟组合节点没有真实作业来源"
                )
            binding = dict(mapped)
    return flattened


__all__ = ["WorkflowBoundaryBindingError", "flatten_output_bindings"]


def structural_boundary_nodes(
    nodes,
    node_uuid,
    boundary="completion_sources",
    seen=frozenset(),
    *,
    validate_ancestry=True,
):
    """解析结构边界：条件选择不是完成，循环节点才代表整轮执行完成。"""
    if node_uuid in seen or node_uuid not in nodes:
        raise WorkflowBoundaryBindingError("结构边界引用不存在的节点或成环")
    node = nodes[node_uuid]
    composite = node.get("meta_data", {}).get("unilab", {}).get("composite")
    if composite:
        values = composite.get("structural_mappings", {}).get(boundary)
        if not isinstance(values, list) or not values:
            raise WorkflowBoundaryBindingError("child 缺少结构边界")
        resolved = []
        for value in values:
            for leaf in structural_boundary_nodes(
                nodes,
                value.get("workflow_node_uuid"),
                boundary,
                seen | {node_uuid},
                validate_ancestry=validate_ancestry,
            ):
                completion = leaf
                parent = nodes[leaf].get("parent_uuid")
                ancestors = set()
                while parent and parent != node_uuid:
                    if parent in ancestors or parent not in nodes:
                        raise WorkflowBoundaryBindingError("child 边界父级无效")
                    ancestors.add(parent)
                    # 入口先经过条件控制器；否则它会在调用方选中该分支前提前求值。
                    # 完成仍等待分支动作；repeat 的 body 由循环控制器整体代表。
                    if nodes[parent].get("type") == "repeat_until" or (
                        boundary == "entry_targets" and nodes[parent].get("type") == "condition"
                    ):
                        completion = parent
                    parent = nodes[parent].get("parent_uuid")
                if validate_ancestry and parent != node_uuid:
                    raise WorkflowBoundaryBindingError("child 边界节点不属于调用")
                resolved.append(completion)
        return list(dict.fromkeys(resolved))
    if node.get("type") == "condition" and boundary == "completion_sources":
        branches = node.get("param", {}).get("branches")
        if not isinstance(branches, list) or not branches:
            raise WorkflowBoundaryBindingError("条件完成边界缺少分支")
        exits = []
        for branch in branches:
            values = branch.get("exit_node_uuids")
            if not isinstance(values, list) or not values:
                raise WorkflowBoundaryBindingError("条件完成边界缺少分支出口")
            for value in values:
                exits.extend(
                    structural_boundary_nodes(
                        nodes,
                        value,
                        boundary,
                        seen | {node_uuid},
                        validate_ancestry=validate_ancestry,
                    )
                )
        return list(dict.fromkeys(exits))
    return [node_uuid]


def flatten_control_result_bindings(*, graph_nodes, handles, planned_node_uuids):
    """把控制节点的输出、顺序、区域成员与入口/出口解析到实际执行节点。"""
    from copy import deepcopy

    nodes = {str(node["uuid"]): node for node in graph_nodes}

    def resolve(value):
        if isinstance(value, list):
            return [resolve(child) for child in value]
        if not isinstance(value, Mapping):
            return deepcopy(value)
        if value.get("kind") == "literal":
            return deepcopy(dict(value))
        path = value.get("result_path")
        node_uuid = value.get("node_uuid")
        if (
            value.get("kind") == "node_result"
            and node_uuid not in planned_node_uuids
            and isinstance(path, list)
            and path
        ):
            source = nodes.get(str(node_uuid), {})
            template = source.get("workflow_node_template_uuid")
            candidates = [
                h
                for h in handles
                if h.get("workflow_node_template_uuid") == template
                and h.get("io_type") == "source"
                and h.get("data_key") == path[0]
            ]
            if len(candidates) != 1:
                raise WorkflowBoundaryBindingError(
                    "控制输入缺少唯一的 child 输出连接点"
                )
            binding = flatten_output_bindings(
                graph_nodes=graph_nodes,
                planned_node_uuids=planned_node_uuids,
                output_bindings={
                    "value": {
                        "kind": "node_output",
                        "workflow_node_uuid": node_uuid,
                        "source_handle_uuid": candidates[0]["uuid"],
                    }
                },
            )["value"]
            leaf = next(
                (h for h in handles if h.get("uuid") == binding["source_handle_uuid"]),
                None,
            )
            if leaf is None or not leaf.get("data_key"):
                raise WorkflowBoundaryBindingError("控制输入的真实输出连接点不存在")
            return {
                **deepcopy(dict(value)),
                "node_uuid": binding["workflow_node_uuid"],
                "result_path": [leaf["data_key"], *path[1:]],
            }
        return {key: resolve(child) for key, child in value.items()}

    def boundary_nodes(node_uuid, boundary="completion_sources"):
        return structural_boundary_nodes(nodes, node_uuid, boundary)

    def expand_members(values):
        if not all(isinstance(value, str) for value in values) or len(
            set(values)
        ) != len(values):
            raise WorkflowBoundaryBindingError("控制区域成员身份无效或重复")
        result = []
        for value in values:
            if value in planned_node_uuids:
                expanded = [value]
            else:
                invocation = nodes.get(value, {})
                if (
                    not invocation.get("meta_data", {})
                    .get("unilab", {})
                    .get("composite")
                ):
                    raise WorkflowBoundaryBindingError("控制区域成员引用计划外节点")
                expanded = []
                for candidate in graph_nodes:
                    if candidate["uuid"] not in planned_node_uuids:
                        continue
                    parent = candidate.get("parent_uuid")
                    seen = set()
                    while parent and parent != value:
                        if parent in seen or parent not in nodes:
                            raise WorkflowBoundaryBindingError("控制区域成员父级无效")
                        seen.add(parent)
                        parent = nodes[parent].get("parent_uuid")
                    if parent == value:
                        expanded.append(candidate["uuid"])
                if not expanded:
                    raise WorkflowBoundaryBindingError("控制区域的 child 没有执行节点")
            result.extend(item for item in expanded if item not in result)
        return result

    def rewrite_region(region):
        if isinstance(region.get("node_uuids"), list):
            region["node_uuids"] = expand_members(region["node_uuids"])
        for key, boundary in (
            ("entry_node_uuids", "entry_targets"),
            ("exit_node_uuids", "completion_sources"),
            ("successor_node_uuids", "entry_targets"),
        ):
            if isinstance(region.get(key), list):
                region[key] = list(
                    dict.fromkeys(
                        leaf
                        for value in region[key]
                        for leaf in boundary_nodes(value, boundary)
                    )
                )

    result = []
    for node in graph_nodes:
        copied = deepcopy(dict(node))
        if node.get("type") in {"condition", "repeat_until"} and isinstance(
            copied.get("param"), dict
        ):
            params = copied.get("param", {})
            rewrite_region(params)
            branches = params.get("branches")
            if isinstance(branches, list):
                for branch in branches:
                    if isinstance(branch, dict):
                        rewrite_region(branch)
            predecessors = copied.get("param", {}).get("predecessor_node_uuids")
            if isinstance(predecessors, list):
                copied["param"]["predecessor_node_uuids"] = list(
                    dict.fromkeys(
                        leaf
                        for predecessor in predecessors
                        for leaf in boundary_nodes(predecessor)
                    )
                )
            for key in ("initial_carry", "next_carry", "bindings"):
                if key in copied.get("param", {}):
                    copied["param"][key] = resolve(copied["param"][key])
        result.append(copied)
    return result
