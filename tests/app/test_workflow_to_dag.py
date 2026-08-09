"""T01 workflow_to_dag 翻译核 hermetic 单测。

覆盖 AC-1（翻译核 UI 图 → F002 TaskDag，含环拒绝）：
- 扁平（云端）与嵌套（SZLab）两种节点形状都能归一
- 边别名 source/target 与 source_node_uuid/target_node_uuid 都支持
- 合法图产出字段合规、往返一致
- 含环图在解析期即抛 DagValidationError（复用 F002 校验）
不连真实设备、无 time.sleep。
"""

from __future__ import annotations

import pytest

from unilabos.app.local_bridge.workflow_to_dag import (
    build_task_dag_payload,
    workflow_to_task_dag,
)
from unilabos.scheduler.dag_model import DagValidationError, TaskDag


def test_flat_cloud_nodes_translate() -> None:
    """扁平（云端）节点：node_id/device_id/action/action_args 直取。"""
    nodes = [
        {
            "node_id": "n1",
            "device_id": "pump_a",
            "action": "add",
            "action_type": "protocol",
            "action_args": {"volume": 10},
        },
        {"node_id": "n2", "device_id": "stir_a", "action": "stir"},
    ]
    edges = [{"source_node_uuid": "n1", "target_node_uuid": "n2"}]

    dag = workflow_to_task_dag(nodes, edges, task_id="t1", notebook_id="nb1")

    assert isinstance(dag, TaskDag)
    assert set(dag.nodes) == {"n1", "n2"}
    assert dag.nodes["n1"].device_id == "pump_a"
    assert dag.nodes["n1"].action == "add"
    assert dag.nodes["n1"].action_type == "protocol"
    assert dag.nodes["n1"].action_args == {"volume": 10}
    assert dag.edges[0].source_node_uuid == "n1"
    assert dag.edges[0].target_node_uuid == "n2"


def test_nested_szlab_nodes_translate() -> None:
    """嵌套（SZLab）节点：{id, data:{method, deviceId, params}} + 边 {source,target}。"""
    nodes = [
        {"id": "a", "data": {"label": "加液", "method": "add", "deviceId": "pump_a", "params": {"v": 5}}},
        {"id": "b", "data": {"label": "搅拌", "method": "stir", "deviceId": "stir_a", "params": {}}},
    ]
    edges = [{"source": "a", "target": "b"}]

    dag = workflow_to_task_dag(nodes, edges, task_id="t2")

    assert set(dag.nodes) == {"a", "b"}
    assert dag.nodes["a"].device_id == "pump_a"
    assert dag.nodes["a"].action == "add"
    assert dag.nodes["a"].action_args == {"v": 5}
    assert dag.edges[0].source_node_uuid == "a"
    assert dag.edges[0].target_node_uuid == "b"


def test_payload_field_names_are_f002() -> None:
    """产出载荷字段名严格是 F002 契约名（无 UI 别名泄漏）。"""
    nodes = [{"id": "x", "data": {"method": "m", "deviceId": "d", "params": {}}}]
    payload = build_task_dag_payload(nodes, [], task_id="t3")

    assert set(payload) == {"task_id", "notebook_id", "server_info", "nodes", "edges"}
    node = payload["nodes"][0]
    assert set(node) == {
        "node_id",
        "device_id",
        "action",
        "action_type",
        "action_args",
        "sample_material",
        "always_free",
    }


def test_cyclic_graph_rejected_at_parse() -> None:
    """含环图在解析期即抛 DagValidationError（复用 F002 I5）。"""
    nodes = [
        {"node_id": "a", "device_id": "d", "action": "m"},
        {"node_id": "b", "device_id": "d", "action": "m"},
    ]
    edges = [
        {"source_node_uuid": "a", "target_node_uuid": "b"},
        {"source_node_uuid": "b", "target_node_uuid": "a"},
    ]
    with pytest.raises(DagValidationError, match="含环"):
        workflow_to_task_dag(nodes, edges, task_id="t4")


def test_dangling_edge_rejected() -> None:
    """悬空边（引用不存在节点）即拒。"""
    nodes = [{"node_id": "a", "device_id": "d", "action": "m"}]
    edges = [{"source": "a", "target": "ghost"}]
    with pytest.raises(DagValidationError):
        workflow_to_task_dag(nodes, edges, task_id="t5")


def test_missing_device_id_rejected() -> None:
    """节点缺 device_id 由 DagNode.from_dict 统一报错。"""
    nodes = [{"node_id": "a", "action": "m"}]
    with pytest.raises(DagValidationError, match="device_id"):
        workflow_to_task_dag(nodes, [], task_id="t6")


def test_missing_task_id_rejected() -> None:
    """缺 task_id 直接拒。"""
    with pytest.raises(DagValidationError, match="task_id"):
        build_task_dag_payload([{"node_id": "a", "device_id": "d", "action": "m"}], [], task_id="")


def test_empty_nodes_rejected() -> None:
    """空 nodes 列表拒。"""
    with pytest.raises(DagValidationError, match="nodes"):
        build_task_dag_payload([], [], task_id="t7")
