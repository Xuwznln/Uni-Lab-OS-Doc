"""Contracts for the renameable HostNode runtime identity."""

from types import SimpleNamespace

import pytest

from unilabos.config.config import BasicConfig, HOST_NODE_REGISTRY_NAME
from unilabos.resources.graphio import canonicalize_nodes_data
from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode
from unilabos.ros.nodes.presets.host_node import HostNode
from unilabos.workflow.common import build_protocol_graph


def _device(device_id: str, *, klass: str = "mock_device") -> dict:
    return {
        "id": device_id,
        "name": device_id,
        "type": "device",
        "class": klass,
        "config": {},
        "data": {},
        "children": [],
    }


def test_graph_removes_renamed_host_root_by_stable_class() -> None:
    tree_set = canonicalize_nodes_data(
        [_device("west_lab", klass=HOST_NODE_REGISTRY_NAME), _device("pump_1")]
    )

    assert [node.res_content.id for node in tree_set.root_nodes] == ["pump_1"]


def test_graph_removes_legacy_classless_host_root_by_configured_name(
    monkeypatch,
) -> None:
    monkeypatch.setattr(BasicConfig, "host_node_name", "west_lab")
    legacy_host = _device("west_lab")
    legacy_host.pop("class")

    tree_set = canonicalize_nodes_data([legacy_host, _device("pump_1")])

    assert [node.res_content.id for node in tree_set.root_nodes] == ["pump_1"]


def test_graph_rejects_device_id_collision_with_host_runtime_name(
    monkeypatch,
) -> None:
    monkeypatch.setattr(BasicConfig, "host_node_name", "west_lab")

    with pytest.raises(ValueError, match="conflicts with the HostNode"):
        canonicalize_nodes_data([_device("west_lab", klass="pump")])


def test_host_role_uses_registry_type_not_runtime_id() -> None:
    renamed_host = object.__new__(BaseROS2DeviceNode)
    renamed_host.registry_name = HOST_NODE_REGISTRY_NAME
    renamed_host.device_id = "west_lab"
    ordinary_device = object.__new__(BaseROS2DeviceNode)
    ordinary_device.registry_name = "pump"
    ordinary_device.device_id = "host_node"

    assert renamed_host.is_host_node is True
    assert ordinary_device.is_host_node is False


def test_generated_workflow_targets_renamed_host() -> None:
    graph = build_protocol_graph(
        {},
        [],
        workstation_name="workstation",
        labware_defs=[{"name": "plate", "slot": "1", "type": "plate_type"}],
        host_node_name="west_lab",
    )
    create_node = next(
        SimpleNamespace(**node)
        for node in graph.nodes.values()
        if node.get("template_name") == "create_resource"
    )

    assert create_node.device_name == "west_lab"
    assert create_node.resource_name == "west_lab"
    assert create_node.footer == "create_resource-west_lab"


def test_host_side_effect_bridge_requires_the_requested_capability() -> None:
    scheduler_bridge = SimpleNamespace(publish_host_ready=lambda: None)
    material_bridge = SimpleNamespace(resource_tree_add=lambda *_args: {})
    host = SimpleNamespace(bridges=[scheduler_bridge, material_bridge])

    assert HostNode._bridge_for(host, "resource_tree_add") is material_bridge
    assert HostNode._bridge_for(host, "resource_registry") is None
