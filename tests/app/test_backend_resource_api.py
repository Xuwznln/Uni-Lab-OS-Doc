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
                    "config_info": [],
                    "scene": [],
                    "device_params": {},
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0
    return response.json()["data"]["templates"][0]["uuid"]


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
        },
    )
    assert created.status_code == 201
    assert created.json()["code"] == 0
    material = created.json()["data"]
    material_uuid = material["uuid"]
    assert material["resource_template_uuid"] == template_uuid
    assert "deleted_at" not in material

    listed = client.get("/api/v1/materials").json()["data"]
    assert [row["uuid"] for row in listed["items"]] == [material_uuid]

    deleted = client.delete(f"/api/v1/materials/{material_uuid}")
    assert deleted.status_code == 200
    assert deleted.json() == {"code": 0}
    assert client.get(f"/api/v1/materials/{material_uuid}").json()["code"] == 6000
    assert store.query_one(
        "SELECT deleted_at FROM material WHERE uuid=?", (material_uuid,)
    )["deleted_at"] is not None
    store.close()


def test_resource_handle_uuid_is_stable_and_omitted_update_preserves_it(tmp_path):
    client, store = _client(tmp_path)
    template_uuid = _sync_template(client)
    detail = client.get(f"/api/v1/resource-templates/{template_uuid}").json()[
        "data"
    ]
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
    site = client.get(f"/api/v1/materials/{owner['uuid']}/sites").json()[
        "data"
    ][0]
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
    assert store.query_one(
        "SELECT deleted_at FROM relative_position WHERE material_uuid=?",
        (material["uuid"],),
    )["deleted_at"] is not None
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
