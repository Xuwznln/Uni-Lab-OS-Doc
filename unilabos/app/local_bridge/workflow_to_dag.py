"""共享翻译核：两套 UI 的工作流图 → F002 TaskDag。

这是实现 A（云端 panel）与实现 B（SZLab local_ui）唯一共享的业务逻辑：
把各 UI 的 nodes/edges 归一为 F002 契约字段，产出 task_dag 载荷，再交
unilabos.scheduler.dag_model.TaskDag.from_message 解析与校验（含解析期拒环 = F002 I5）。

字段对齐 F002 interface-design.md §1.1：
- node → {node_id, device_id, action, action_type, action_args, sample_material, always_free}
- edge → {source_node_uuid, target_node_uuid}

两套 UI 的原生字段名不同，本模块统一做别名归一，避免两面各写一套翻译：
- 实现 A（云端）: 节点直接带 node_id/device_id/action/action_args；边带 source/target。
- 实现 B（SZLab）: 节点形如 {id, data:{method, deviceId, params}}；边带 source/target。
"""

from __future__ import annotations

from typing import Any

from unilabos.scheduler.dag_model import DagValidationError, TaskDag


def _first_present(d: dict[str, Any], *keys: str) -> Any:
    """按顺序返回第一个存在且非空的键值；都无则返回 None。"""
    for key in keys:
        value = d.get(key)
        if value:
            return value
    return None


def _node_to_f002(raw: dict[str, Any]) -> dict[str, Any]:
    """把一个 UI 节点归一为 F002 节点字段（别名解析）。

    支持两种形状：
    - 扁平（云端）: {node_id/id, device_id, action, action_type, action_args, ...}
    - 嵌套（SZLab）: {id, data:{method, deviceId, params, ...}}
    """
    if not isinstance(raw, dict):
        raise DagValidationError(f"节点必须是对象，收到 {type(raw).__name__}")

    inner = raw.get("data")
    inner = inner if isinstance(inner, dict) else {}

    node_id = _first_present(raw, "node_id", "id")
    device_id = _first_present(raw, "device_id", "deviceId") or _first_present(
        inner, "device_id", "deviceId"
    )
    action = _first_present(raw, "action", "method") or _first_present(
        inner, "action", "method"
    )
    action_type = raw.get("action_type") or inner.get("action_type") or ""
    action_args = (
        _first_present(raw, "action_args", "params")
        or _first_present(inner, "action_args", "params")
        or {}
    )
    sample_material = raw.get("sample_material") or inner.get("sample_material") or {}
    always_free = bool(raw.get("always_free", inner.get("always_free", False)))

    # 缺字段留给 DagNode.from_dict 统一报错，保证错误信息与 F002 一致
    node: dict[str, Any] = {
        "node_id": node_id,
        "device_id": device_id,
        "action": action,
        "action_type": action_type,
        "action_args": dict(action_args) if isinstance(action_args, dict) else {},
        "sample_material": dict(sample_material)
        if isinstance(sample_material, dict)
        else {},
        "always_free": always_free,
    }
    return node


def _edge_to_f002(raw: dict[str, Any]) -> dict[str, Any]:
    """把一个 UI 边归一为 F002 边字段（source/target 或 source_node_uuid/target_node_uuid）。"""
    if not isinstance(raw, dict):
        raise DagValidationError(f"边必须是对象，收到 {type(raw).__name__}")
    source = _first_present(raw, "source_node_uuid", "source")
    target = _first_present(raw, "target_node_uuid", "target")
    return {"source_node_uuid": source, "target_node_uuid": target}


def build_task_dag_payload(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    task_id: str,
    notebook_id: str = "",
    server_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 UI 图归一为 F002 task_dag 载荷（§1.1 的 data 段），不做解析校验。

    仅做字段归一；合法性（缺字段/重复/悬空边/含环）交 TaskDag.from_message 统一判定，
    以保证与 F002 解析行为逐字一致。
    """
    if not task_id:
        raise DagValidationError("缺少 task_id")
    if not isinstance(nodes, list) or not nodes:
        raise DagValidationError("缺少非空 nodes 列表")
    if edges is None:
        edges = []
    if not isinstance(edges, list):
        raise DagValidationError("edges 必须是列表")

    return {
        "task_id": task_id,
        "notebook_id": notebook_id,
        "server_info": dict(server_info or {}),
        "nodes": [_node_to_f002(n) for n in nodes],
        "edges": [_edge_to_f002(e) for e in edges],
    }


def workflow_to_task_dag(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    task_id: str,
    notebook_id: str = "",
    server_info: dict[str, Any] | None = None,
) -> TaskDag:
    """UI 图 → 校验后的 F002 TaskDag（含解析期拒环）。

    是两套 UI 面（workflow_ws / local_api）触发运行时的统一入口。
    """
    payload = build_task_dag_payload(
        nodes,
        edges,
        task_id=task_id,
        notebook_id=notebook_id,
        server_info=server_info,
    )
    return TaskDag.from_message(payload)
