"""Shared Backend/Edge Resource Interface contract tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from unilabos.app.scheduler.inventory.backend_api import (
    install_backend_resource_api,
)
from unilabos.app.scheduler.inventory.backend_contract import (
    BackendResourceService,
)
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.store import InventoryStore


def _client(tmp_path):
    store = InventoryStore(str(tmp_path / "inventory.db"))
    app = FastAPI()
    install_backend_resource_api(app, BackendResourceService(store))
    return TestClient(app), store


def _sync_template(client: TestClient) -> str:
    response = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "device.pump",
                    "display_name": "Pump",
                    "registry_type": "device",
                    "model": {},
                    "class": {
                        "module": "drivers.pump",
                        "type": "python",
                        "action_value_mappings": {},
                    },
                    "handles": [
                        {
                            "handler_key": "sample",
                            "label": "Sample",
                            "data_type": "material",
                            "io_type": "target",
                            "data_key": "sample_uuid",
                            "data_source": "param",
                            "side": "left",
                        }
                    ],
                    "category": [],
                    "config_info": [{"name": "device.pump", "type": "pump"}],
                    "scene": [],
                    "device_params": {},
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0
    return response.json()["data"]["templates"][0]["uuid"]


def test_backend_adapter_accepts_migrated_edge_identifiers(tmp_path):
    """The Backend wire stays stable while migrated v5 string IDs remain usable."""

    client, store = _client(tmp_path)
    inventory = InventoryService(store)
    inventory.upsert_template(
        "tpl-legacy",
        name="Legacy tube",
        category="tube",
        spec={"type": "tube"},
    )
    inventory.register_instance(
        template_id="tpl-legacy",
        edge_uuid="material-legacy",
        barcode="LEGACY-1",
    )

    template = client.get("/api/v1/resource-templates/tpl-legacy").json()
    assert template["code"] == 0
    assert template["data"]["uuid"] == "tpl-legacy"

    material = client.get("/api/v1/materials/material-legacy").json()
    assert material["code"] == 0
    assert material["data"]["resource_template_uuid"] == "tpl-legacy"

    appended = client.post(
        "/api/v1/materials/material-legacy/states",
        json={"status": "observed", "state_data": {"temperature": 22}},
    ).json()
    assert appended["code"] == 0
    states = client.get("/api/v1/materials/material-legacy/states").json()
    assert states["code"] == 0
    assert states["data"]["items"][0]["state_data"] == {"temperature": 22}

    created = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": "tpl-legacy",
            "parent_uuid": "material-legacy",
            "name": "Legacy child",
        },
    ).json()
    assert created["code"] == 0
    assert created["data"]["parent_uuid"] == "material-legacy"
    store.close()


def test_material_routes_use_backend_envelope_and_soft_delete(tmp_path):
    client, store = _client(tmp_path)
    template_uuid = _sync_template(client)

    created = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": template_uuid,
            "parent_uuid": None,
            "barcode": "PUMP-001",
            "name": "Pump 1",
            "meta_data": {},
            "config": {"port": "loopback"},
            "data": {"connected": False},
            "type": "client-forged",
        },
    )
    assert created.status_code == 201
    assert created.json()["code"] == 0
    material = created.json()["data"]
    material_uuid = material["uuid"]
    assert material["resource_template_uuid"] == template_uuid
    assert material["type"] == "pump"
    assert material["data"] == {"connected": False}
    assert "deleted_at" not in material

    listed = client.get("/api/v1/materials").json()["data"]
    assert [row["uuid"] for row in listed["items"]] == [material_uuid]

    deleted = client.delete(f"/api/v1/materials/{material_uuid}")
    assert deleted.status_code == 200
    assert deleted.json() == {"code": 0}
    assert client.get(f"/api/v1/materials/{material_uuid}").json()["code"] == 6000
    assert (
        store.query_one(
            "SELECT deleted_at FROM material WHERE uuid=?", (material_uuid,)
        )["deleted_at"]
        is not None
    )
    store.close()


def test_material_delete_soft_deletes_component_tree_and_clears_site_occupancy(
    tmp_path,
):
    client, store = _client(tmp_path)
    tree_template_uuid = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "tree.template",
                    "display_name": "Tree",
                    "registry_type": "device",
                    "config_info": [
                        {
                            "id": "root",
                            "type": "deck",
                            "position": {"x": 1},
                            "sites": [{"label": "A1"}],
                        },
                        {
                            "id": "child",
                            "type": "carrier",
                            "position": {"x": 2},
                            "sites": [{"label": "B1"}],
                        },
                    ],
                }
            ]
        },
    ).json()["data"]["templates"][0]["uuid"]
    root = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": tree_template_uuid,
            "barcode": "TREE-1",
            "name": "Tree 1",
        },
    ).json()["data"]
    child = root["children"][0]

    external_template_uuid = _sync_template(client)
    external = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": external_template_uuid,
            "name": "External carrier",
        },
    ).json()["data"]
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO site(
                create_time,update_time,meta_data,material_uuid,name,sort_order,
                allowed_resource_template_uuids,occupied_material_uuid,
                position_x,position_y,position_z,depth,length,width
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "2026-08-08T00:00:00Z",
                "2026-08-08T00:00:00Z",
                "{}",
                external["uuid"],
                "external-site",
                0,
                "[]",
                child["uuid"],
                0,
                0,
                0,
                1,
                1,
                1,
            ),
        )

    deleted = client.delete(f"/api/v1/materials/{root['uuid']}")
    assert deleted.json() == {"code": 0}
    assert client.get(f"/api/v1/materials/{root['uuid']}").json()["code"] == 6000
    assert client.get(f"/api/v1/materials/{child['uuid']}").json()["code"] == 6000
    assert (
        store.query_one(
            "SELECT COUNT(*) AS n FROM site "
            "WHERE material_uuid IN (?,?) AND deleted_at IS NOT NULL",
            (root["uuid"], child["uuid"]),
        )["n"]
        == 2
    )
    assert (
        store.query_one(
            "SELECT COUNT(*) AS n FROM relative_position "
            "WHERE material_uuid IN (?,?) AND deleted_at IS NOT NULL",
            (root["uuid"], child["uuid"]),
        )["n"]
        == 2
    )
    assert (
        store.query_one(
            "SELECT occupied_material_uuid FROM site "
            "WHERE material_uuid=? AND name='external-site'",
            (external["uuid"],),
        )["occupied_material_uuid"]
        is None
    )
    statuses = store.query_all(
        "SELECT inventory_status,disposition FROM material_inventory "
        "WHERE material_uuid IN (?,?) ORDER BY material_uuid",
        (root["uuid"], child["uuid"]),
    )
    assert statuses == [
        {"inventory_status": "discarded", "disposition": "discarded"},
        {"inventory_status": "discarded", "disposition": "discarded"},
    ]
    store.close()


def test_resource_handle_uuid_is_stable_and_omitted_update_preserves_it(tmp_path):
    client, store = _client(tmp_path)
    template_uuid = _sync_template(client)
    detail = client.get(f"/api/v1/resource-templates/{template_uuid}").json()["data"]
    assert len(detail["handles"]) == 1
    handle = detail["handles"][0]
    assert handle["resource_template_uuid"] == template_uuid
    assert handle["name"] == "sample"
    assert handle["io_type"] == "target"

    updated = client.put(
        f"/api/v1/resource-templates/{template_uuid}",
        json={"display_name": "Pump v2", "registry_type": "device"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["handles"][0]["uuid"] == handle["uuid"]
    assert updated.json()["data"]["display_name"] == "Pump v2"
    store.close()


def test_site_uuid_is_position_identity_and_state_updates_material_projection(
    tmp_path,
):
    client, store = _client(tmp_path)
    template_uuid = _sync_template(client)
    owner = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": template_uuid,
            "barcode": "OWNER",
            "name": "Owner",
        },
    ).json()["data"]
    occupant = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": template_uuid,
            "barcode": "OCCUPANT",
            "name": "Occupant",
            "parent_uuid": owner["uuid"],
        },
    ).json()["data"]
    with store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO site(
                create_time,update_time,meta_data,material_uuid,name,sort_order,
                allowed_resource_template_uuids,occupied_material_uuid,
                position_x,position_y,position_z,depth,length,width
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "2026-08-04T00:00:00Z",
                "2026-08-04T00:00:00Z",
                "{}",
                owner["uuid"],
                "A1",
                0,
                "[]",
                occupant["uuid"],
                0,
                0,
                0,
                1,
                1,
                1,
            ),
        )
    site = client.get(f"/api/v1/materials/{owner['uuid']}/sites").json()["data"][0]
    assert site["uuid"] not in {owner["uuid"], occupant["uuid"]}
    assert site["material_uuid"] == owner["uuid"]
    assert site["occupied_material_uuid"] == occupant["uuid"]

    state = client.post(
        f"/api/v1/materials/{occupant['uuid']}/states",
        json={
            "status": "observed",
            "state_data": {"temperature": 25},
            "source": "test",
        },
    ).json()
    assert state["code"] == 0
    detail = client.get(f"/api/v1/materials/{occupant['uuid']}").json()["data"]
    assert detail["data"] == {"temperature": 25}
    store.close()


def test_relative_position_round_trips_and_explicit_null_soft_deletes(tmp_path):
    client, store = _client(tmp_path)
    template_uuid = _sync_template(client)
    payload = {
        "resource_template_uuid": template_uuid,
        "barcode": "POSITIONED",
        "name": "Positioned",
        "relative_position": {
            "position_x": 1.5,
            "position_y": 2,
            "position_z": 3,
            "depth": 4,
            "length": 5,
            "width": 6,
            "scale_x": 1,
            "scale_y": 1,
            "scale_z": 1,
        },
    }
    created = client.post("/api/v1/materials", json=payload)
    assert created.status_code == 201
    material = created.json()["data"]
    assert material["relative_position"]["material_uuid"] == material["uuid"]
    assert material["relative_position"]["position_x"] == 1.5

    payload["relative_position"] = None
    updated = client.put(f"/api/v1/materials/{material['uuid']}", json=payload)
    assert updated.status_code == 200
    assert updated.json()["data"]["relative_position"] is None
    assert (
        store.query_one(
            "SELECT deleted_at FROM relative_position WHERE material_uuid=?",
            (material["uuid"],),
        )["deleted_at"]
        is not None
    )
    store.close()


def test_material_creation_allocates_instance_site_uuids_from_template(tmp_path):
    client, store = _client(tmp_path)
    synced = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "rack.template",
                    "display_name": "Rack",
                    "registry_type": "device",
                    "config_info": [
                        {
                            "name": "rack.template",
                            "type": "rack",
                            "sites": [
                                {
                                    "uuid": "template-site-must-not-leak",
                                    "index": 4,
                                    "label": "A1",
                                    "position": {"x": 1, "y": 2, "z": 3},
                                    "size": {
                                        "width": 10,
                                        "height": 11,
                                        "depth": 12,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    ).json()
    template_uuid = synced["data"]["templates"][0]["uuid"]

    materials = []
    for ordinal in (1, 2):
        response = client.post(
            "/api/v1/materials",
            json={
                "resource_template_uuid": template_uuid,
                "barcode": f"RACK-{ordinal}",
                "name": f"Rack {ordinal}",
            },
        )
        assert response.status_code == 201
        materials.append(response.json()["data"])

    first_site = materials[0]["sites"][0]
    second_site = materials[1]["sites"][0]
    assert first_site["uuid"] != second_site["uuid"]
    assert first_site["uuid"] != "template-site-must-not-leak"
    assert first_site["name"] == "A1"
    assert first_site["sort_order"] == 4
    assert first_site["position_x"] == 1
    assert first_site["length"] == 11
    assert first_site["depth"] == 12

    database = store.path
    first_uuid = first_site["uuid"]
    store.close()
    reopened = InventoryStore(database)
    assert reopened.list_material_sites(materials[0]["uuid"])[0]["uuid"] == first_uuid
    reopened.close()


def test_material_create_expands_components_defaults_positions_and_allowed_sites(
    tmp_path,
):
    client, store = _client(tmp_path)
    allowed = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "tube.template",
                    "display_name": "Tube",
                    "registry_type": "resource",
                    "tags": ["tube"],
                }
            ]
        },
    ).json()["data"]["templates"][0]["uuid"]
    template_uuid = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "deck.template",
                    "display_name": "Deck",
                    "registry_type": "device",
                    "config_info": [
                        {
                            "id": "deck-root",
                            "type": "deck",
                            "position": {"x": 10, "y": 20, "z": 30},
                            "config": {
                                "speed": 10,
                                "nested": {"template": True},
                                "sites": [
                                    {
                                        "label": "slot-1",
                                        "content_type": ["tube"],
                                        "position": {"x": 1, "y": 2, "z": 3},
                                        "size": {
                                            "depth": 4,
                                            "height": 5,
                                            "width": 6,
                                        },
                                    }
                                ],
                            },
                            "data": {"status": "idle", "temperature": 25},
                        },
                        {
                            "id": "well-1",
                            "name": "Well",
                            "class": "well-class",
                            "type": "well",
                            "config": {"capacity": 2},
                            "data": {"volume": 1},
                            "pose": {
                                "position": {"x": 7, "y": 8, "z": 9},
                                "size": {"depth": 0.5, "height": 2, "width": 3},
                            },
                        },
                    ],
                }
            ]
        },
    ).json()["data"]["templates"][0]["uuid"]

    response = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": template_uuid,
            "barcode": "DECK-001",
            "name": "Deck 1",
            "config": {
                "speed": 20,
                "nested": {"request": True},
                "sites": [{"label": "forged"}],
            },
            "data": {"status": "request"},
            "relative_position": {
                "position_x": 999,
                "scale_x": 1,
                "scale_y": 1,
                "scale_z": 1,
            },
        },
    )
    assert response.status_code == 201
    root = response.json()["data"]
    assert root["class"] == "deck.template"
    assert root["type"] == "deck"
    assert root["config"]["speed"] == 20
    assert root["config"]["nested"] == {"template": True, "request": True}
    assert root["config"]["sites"][0]["label"] == "slot-1"
    assert root["data"] == {"status": "request", "temperature": 25}
    assert root["relative_position"]["position_x"] == 10

    assert len(root["children"]) == 1
    child = root["children"][0]
    assert child["parent_uuid"] == root["uuid"]
    assert child["barcode"] == "DECK-001/well-1"
    assert child["class"] == "well-class"
    assert child["type"] == "well"
    assert child["config"] == {"capacity": 2}
    assert child["data"] == {"volume": 1}
    child_position = store.query_one(
        "SELECT * FROM relative_position WHERE material_uuid=?",
        (child["uuid"],),
    )
    assert child_position["position_x"] == 7
    assert child_position["length"] == 2

    assert len(root["sites"]) == 1
    site = root["sites"][0]
    assert site["material_uuid"] == root["uuid"]
    assert site["allowed_resource_template_uuids"] == []
    assert site["content_type"] == ["tube"]
    assert site["occupied_material_uuid"] is None
    tube = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": allowed,
            "parent_uuid": root["uuid"],
            "name": "Tube in slot 1",
            "site_placement": {"action": "place", "site_uuid": site["uuid"]},
        },
    )
    assert tube.status_code == 201
    assert (
        store.query_one(
            "SELECT occupied_material_uuid FROM site WHERE uuid=?", (site["uuid"],)
        )["occupied_material_uuid"]
        == tube.json()["data"]["uuid"]
    )
    assert store.query_one("SELECT COUNT(*) AS n FROM material_state_history")["n"] == 3
    assert client.get(f"/api/v1/materials/{child['uuid']}/states/latest").json()[
        "data"
    ]["state_data"] == {"volume": 1}
    assert client.get("/api/v1/materials").json()["data"]["total"] == 1
    assert (
        client.get("/api/v1/materials", params={"with_children": True}).json()["data"][
            "total"
        ]
        == 3
    )
    store.close()


def test_resource_material_requires_non_resource_hierarchy_root(tmp_path):
    client, store = _client(tmp_path)
    resource_uuid = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "resource.template",
                    "display_name": "Resource",
                    "registry_type": "resource",
                }
            ]
        },
    ).json()["data"]["templates"][0]["uuid"]
    rejected = client.post(
        "/api/v1/materials",
        json={"resource_template_uuid": resource_uuid, "name": "Orphan"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["code"] == 1000

    device_uuid = _sync_template(client)
    device = client.post(
        "/api/v1/materials",
        json={"resource_template_uuid": device_uuid, "name": "Device root"},
    ).json()["data"]
    child = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": resource_uuid,
            "parent_uuid": device["uuid"],
            "name": "Attached resource",
        },
    )
    assert child.status_code == 201
    assert child.json()["data"]["parent_uuid"] == device["uuid"]
    store.close()


def test_material_update_is_partial_preserves_sites_and_rejects_data_authority(
    tmp_path,
):
    client, store = _client(tmp_path)
    template_uuid = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "update.template",
                    "display_name": "Update",
                    "registry_type": "device",
                    "config_info": [
                        {
                            "id": "root",
                            "type": "device-root",
                            "config": {
                                "kept_only_until_replace": True,
                                "sites": [{"label": "A1"}],
                            },
                            "data": {"status": "initial"},
                        }
                    ],
                }
            ]
        },
    ).json()["data"]["templates"][0]["uuid"]
    material = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": template_uuid,
            "name": "Before",
            "config": {"request": 1},
        },
    ).json()["data"]

    updated = client.put(
        f"/api/v1/materials/{material['uuid']}",
        json={
            "name": "After",
            "config": {"replacement": 2, "sites": [{"label": "forged"}]},
            "data": {"status": "forged"},
            "class": "forged",
            "type": "forged",
            "resource_template_uuid": "00000000-0000-0000-0000-000000000000",
        },
    ).json()["data"]
    assert updated["name"] == "After"
    assert updated["resource_template_uuid"] == template_uuid
    assert updated["class"] == "update.template"
    assert updated["type"] == "device-root"
    assert updated["data"] == {"status": "initial"}
    assert updated["config"] == {
        "replacement": 2,
        "sites": [{"label": "A1"}],
    }

    ignored_null = client.put(
        f"/api/v1/materials/{material['uuid']}", json={"name": None}
    ).json()["data"]
    assert ignored_null["name"] == "After"
    assert (
        store.query_one(
            "SELECT COUNT(*) AS n FROM material_state_history WHERE material_uuid=?",
            (material["uuid"],),
        )["n"]
        == 1
    )
    store.close()


def test_unmatched_site_content_type_is_persisted_without_registered_candidate(
    tmp_path,
):
    client, store = _client(tmp_path)
    template_uuid = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "invalid-site.template",
                    "registry_type": "device",
                    "config_info": [
                        {
                            "id": "root",
                            "config": {
                                "sites": [
                                    {
                                        "label": "A1",
                                        "content_type": ["missing-tag"],
                                    }
                                ]
                            },
                        }
                    ],
                }
            ]
        },
    ).json()["data"]["templates"][0]["uuid"]
    response = client.post(
        "/api/v1/materials",
        json={"resource_template_uuid": template_uuid, "name": "Invalid"},
    )
    assert response.status_code == 201
    site = response.json()["data"]["sites"][0]
    assert site["allowed_resource_template_uuids"] == []
    assert site["content_type"] == ["missing-tag"]
    assert (
        store.query_one("SELECT content_type FROM site WHERE uuid=?", (site["uuid"],))[
            "content_type"
        ]
        == '["missing-tag"]'
    )
    store.close()


def test_site_content_type_alias_is_resolved_when_material_is_placed(tmp_path):
    client, store = _client(tmp_path)
    bottle_template_uuid = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "bottle.template",
                    "registry_type": "resource",
                    "tags": ["bottle"],
                }
            ]
        },
    ).json()["data"]["templates"][0]["uuid"]
    carrier_template_uuid = client.post(
        "/api/v1/resource-templates",
        json={
            "resources": [
                {
                    "id": "carrier.template",
                    "registry_type": "device",
                    "config_info": [
                        {
                            "id": "root",
                            "config": {
                                "sites": [{"label": "A1", "content_type": ["bottles"]}]
                            },
                        }
                    ],
                }
            ]
        },
    ).json()["data"]["templates"][0]["uuid"]
    carrier = client.post(
        "/api/v1/materials",
        json={"resource_template_uuid": carrier_template_uuid, "name": "Carrier"},
    ).json()["data"]
    site = carrier["sites"][0]

    incompatible_template_uuid = _sync_template(client)
    rejected = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": incompatible_template_uuid,
            "parent_uuid": carrier["uuid"],
            "name": "Not a bottle",
            "site_placement": {"action": "place", "site_uuid": site["uuid"]},
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["code"] == 6008

    bottle = client.post(
        "/api/v1/materials",
        json={
            "resource_template_uuid": bottle_template_uuid,
            "parent_uuid": carrier["uuid"],
            "name": "Bottle",
            "site_placement": {"action": "place", "site_uuid": site["uuid"]},
        },
    )
    assert bottle.status_code == 201
    assert (
        store.query_one(
            "SELECT occupied_material_uuid FROM site WHERE uuid=?", (site["uuid"],)
        )["occupied_material_uuid"]
        == bottle.json()["data"]["uuid"]
    )
    store.close()


def test_openapi_exposes_backend_resource_paths_not_only_inventory_paths(tmp_path):
    client, store = _client(tmp_path)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/resource-templates" in paths
    assert "/api/v1/materials" in paths
    assert "/api/v1/materials/{material_uuid}/sites" in paths
    assert "/api/v1/materials/{material_uuid}/states" in paths
    assert "/api/v1/sites/{site_uuid}" in paths
    store.close()
