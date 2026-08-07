"""HostNode-compatible material query over the Edge inventory microbackend."""

from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from unilabos.app.scheduler.inventory.api import create_app
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.resources.resource_tracker import ResourceDict, ResourceTreeSet


def _service() -> InventoryService:
    service = InventoryService(InventoryStore(":memory:"))
    service.upsert_template(
        "tpl-rack",
        name="Rack template",
        category="container",
        spec={
            "storage_class": "ambient",
            "resource": {
                "id": "rack-logical-id",
                "name": "rack-a",
                "type": "container",
                "class": "",
                "config": {"size_x": 120, "size_y": 80, "size_z": 20},
                "sites": [
                    {
                        "uuid": "site-a1",
                        "index": 0,
                        "label": "A1",
                        "occupied_by": "tube-a1",
                        "position": {"x": 1, "y": 2, "z": 3},
                        "size": {"width": 10, "height": 10, "depth": 20},
                    }
                ],
                "data": {"template_state": True},
                "extra": {"fixture": "rack"},
            },
        },
    )
    service.upsert_template(
        "tpl-tube",
        name="Tube template",
        category="container",
        spec={
            "resource_dict": {
                "id": "tube-logical-id",
                "name": "tube-a1",
                "type": "container",
                "class": "",
                "config": {},
                "data": {"max_volume": 2.0},
                "extra": {},
            }
        },
    )
    service.register_instance(
        template_id="tpl-rack",
        edge_uuid="edge-rack",
        legacy_cloud_id="cloud-rack",
        barcode="RACK-001",
    )
    service.register_instance(
        template_id="tpl-tube",
        edge_uuid="edge-tube",
        barcode="TUBE-001",
        parent_uuid="edge-rack",
        slot_id="A1",
    )
    service.update_content(
        "edge-tube",
        {
            "data": {"temperature_c": 4},
            "liquids": [["water", 1.5]],
            "liquid_history": [["water", 1.5]],
            "unknown_counter": 2,
            "substance": "water",
        },
    )
    return service


def test_legacy_query_returns_flat_resource_dict_tree() -> None:
    service = _service()
    client = TestClient(create_app(service))

    response = client.post(
        "/api/v1/edge/material/query",
        json={"uuids": ["edge-rack"], "with_children": True},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    nodes = response.json()["data"]["nodes"]
    assert [node["uuid"] for node in nodes] == ["edge-rack", "edge-tube"]
    assert nodes[1]["parent_uuid"] == "edge-rack"
    assert nodes[0]["type"] == "container"
    assert nodes[0]["extra"]["edge_inventory"]["type"] == "container"
    assert nodes[1]["extra"]["update_resource_site"] == "A1"
    assert nodes[1]["extra"]["edge_inventory"]["template_id"] == "tpl-tube"
    assert nodes[1]["data"] == {
        "max_volume": 2.0,
        "temperature_c": 4,
        "substance": "water",
    }
    assert nodes[1]["liquids"] == [["water", 1.5]]
    assert nodes[1]["liquid_history"] == [["water", 1.5]]
    assert nodes[1]["unknown_counter"] == 2
    site = nodes[0]["sites"][0]
    canonical = service.store.list_material_sites("edge-rack")[0]
    assert site["uuid"] == canonical["uuid"]
    assert site["uuid"] != "site-a1"  # template identity is never reused
    assert site["label"] == "A1"
    assert site["occupied_by"] == "tube-a1"
    assert site["occupied_material_uuid"] == "edge-tube"
    assert site["material_uuid"] == "edge-rack"
    assert site["position"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert site["size"] == {"width": 10.0, "height": 10.0, "depth": 20.0}

    for node in nodes:
        ResourceDict.model_validate(node)
    tree_set = ResourceTreeSet.from_raw_dict_list(deepcopy(nodes))
    assert len(tree_set.trees) == 1
    assert tree_set.trees[0].root_node.res_content.uuid == "edge-rack"
    assert tree_set.trees[0].root_node.children[0].res_content.uuid == "edge-tube"


def test_query_supports_legacy_cloud_uuid_id_and_without_children() -> None:
    client = TestClient(create_app(_service()))

    by_cloud_uuid = client.post(
        "/api/v1/edge/material/query",
        json={"uuids": ["cloud-rack"], "with_children": False},
    ).json()["data"]["nodes"]
    by_logical_id = client.post(
        "/api/v1/edge/material/query",
        json={"id": "tube-logical-id", "with_children": True},
    ).json()["data"]["nodes"]

    assert [node["uuid"] for node in by_cloud_uuid] == ["edge-rack"]
    assert [node["uuid"] for node in by_logical_id] == ["edge-tube"]


def test_query_deduplicates_overlapping_roots_and_validates_selector() -> None:
    client = TestClient(create_app(_service()))

    response = client.post(
        "/api/v1/edge/material/query",
        json={"uuids": ["edge-rack", "edge-tube"], "with_children": True},
    )

    assert [node["uuid"] for node in response.json()["data"]["nodes"]] == [
        "edge-rack",
        "edge-tube",
    ]
    assert (
        client.post(
            "/api/v1/edge/material/query",
            json={"uuids": [], "with_children": True},
        ).status_code
        == 422
    )
