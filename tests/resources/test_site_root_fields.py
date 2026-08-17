from __future__ import annotations


import json


from copy import deepcopy


from pathlib import Path


from uuid import uuid4


import pytest


from pydantic import ValidationError


from unilabos.resources.resource_tracker import (
    EXTRA_RESOURCE_CLASS,
    EXTRA_RESOURCE_META_DATA,
    EXTRA_SITES,
    DeviceNodeResourceTracker,
    PLRSerializedSite,
    ResourceDict,
    ResourceSite,
    ResourceTreeSet,
    apply_plr_site_metadata,
    extract_plr_sites,
    merge_resource_sites,
    prepare_resource_creation_payloads,
    prepare_resource_tree_for_creation,
    resource_site_to_plr_site,
    set_plr_template_name,
    sites_for_plr_deserialization,
)


def _resource_payload(**overrides):
    payload = {
        "id": "carrier",
        "uuid": str(uuid4()),
        "name": "carrier",
        "type": "carrier",
        "class": "",
        "config": {"type": "StrictCarrier"},
        "data": {},
        "extra": {},
        "position": {"x": 1, "y": 2, "z": 3},
    }
    payload.update(overrides)
    return payload


def _site_payload(owner_uuid: str, template_name: str = "StrictCarrier", **overrides):
    payload = {
        "schema_version": 1,
        "uuid": str(uuid4()),
        "template_name": template_name,
        "material_uuid": owner_uuid,
        "index": "A1",
        "label": "A1",
        "occupied_material_uuid": None,
        "pose": {
            "position": {"x": 10, "y": 20, "z": 0},
            "position3d": {"x": 10, "y": 20, "z": 30},
        },
    }
    payload.update(overrides)
    return payload


def test_runtime_create_response_preserves_static_pose_dynamic_position_and_site_identity():
    owner_uuid = str(uuid4())
    site = _site_payload(owner_uuid)
    raw = _resource_payload(
        uuid=owner_uuid,
        template_name="StrictCarrier",
        pose={"position": {"x": 4, "y": 5, "z": 6}},
        position={"x": 40, "y": 50, "z": 60},
        sites=[site],
        sites_initialized=True,
        children=[],
    )

    _, prepared = prepare_resource_creation_payloads([raw])
    created = prepared[0]
    assert created["position"] == {"x": 40.0, "y": 50.0, "z": 60.0}
    assert created["pose"]["position"] == {"x": 4.0, "y": 5.0, "z": 6.0}
    assert created["sites"][0]["material_uuid"] == created["uuid"]
    assert created["sites_initialized"] is True
    site_uuid = created["sites"][0]["uuid"]
    assert site_uuid == site["uuid"]

    _, prepared_again = prepare_resource_creation_payloads(prepared)
    assert prepared_again[0]["uuid"] == created["uuid"]
    assert prepared_again[0]["sites"][0]["uuid"] == site_uuid


def test_instance_payload_discards_available_sites_without_instantiating_them():
    raw = _resource_payload(
        template_name="StrictCarrier",
        available_sites=[{"index": "A1", "label": "A1", "content_type": ["tube"]}],
        sites=[],
        sites_initialized=True,
        children=[],
    )

    tree, prepared = prepare_resource_creation_payloads([raw])

    resource = tree.root_nodes[0].res_content
    assert resource.sites_initialized is True
    assert resource.sites == []
    assert "available_sites" not in resource.model_dump()
    assert "available_sites" not in prepared[0]


def test_startup_material_tree_must_already_have_authoritative_site_snapshot():
    tree = ResourceTreeSet.from_raw_dict_list(
        [
            _resource_payload(
                available_sites=[{"index": 0, "label": "slot-0"}],
                sites=[],
                sites_initialized=True,
            )
        ]
    )

    assert prepare_resource_tree_for_creation(tree) == 1

    startup_resource = tree.dump()[0][0]
    assert startup_resource["sites_initialized"] is True
    assert startup_resource["sites"] == []
    assert "available_sites" not in startup_resource

    pending = ResourceTreeSet.from_raw_dict_list(
        [_resource_payload(sites=None, sites_initialized=False)]
    )
    with pytest.raises(ValueError, match="尚未由微后端"):
        prepare_resource_tree_for_creation(pending)


def test_offline_uuid_migration_updates_tree_and_site_references_atomically():
    owner_uuid = str(uuid4())
    occupant_uuid = str(uuid4())
    site_uuid = str(uuid4())
    tree = ResourceTreeSet.from_raw_dict_list(
        [
            _resource_payload(
                uuid=owner_uuid,
                template_name="StrictCarrier",
                sites_initialized=True,
                sites=[
                    {
                        "schema_version": 1,
                        "uuid": site_uuid,
                        "template_name": "StrictCarrier",
                        "material_uuid": owner_uuid,
                        "index": "A1",
                        "label": "A1",
                        "occupied_material_uuid": occupant_uuid,
                    }
                ],
            ),
            _resource_payload(
                id="tube-1",
                uuid=occupant_uuid,
                name="tube-1",
                type="tube",
                config={"type": "Tube"},
                parent_uuid=owner_uuid,
            ),
        ]
    )
    migrated_owner_uuid = str(uuid4())
    migrated_occupant_uuid = str(uuid4())

    assert tree.replace_resource_uuids(
        {owner_uuid: migrated_owner_uuid, occupant_uuid: migrated_occupant_uuid}
    ) == 2

    owner = tree.find_by_uuid(migrated_owner_uuid)
    occupant = tree.find_by_uuid(migrated_occupant_uuid)
    assert owner is not None and occupant is not None
    assert occupant.res_content.parent_uuid == migrated_owner_uuid
    assert occupant.res_content.parent is owner.res_content
    assert owner.res_content.sites is not None
    site = owner.res_content.sites[0]
    assert site.uuid == site_uuid
    assert site.material_uuid == migrated_owner_uuid
    assert site.occupied_material_uuid == migrated_occupant_uuid

    before = tree.dump()
    with pytest.raises(ValueError, match="重复资源 UUID"):
        tree.replace_resource_uuids({migrated_owner_uuid: migrated_occupant_uuid})
    assert tree.dump() == before


def test_legacy_site_geometry_is_promoted_to_shared_pose():
    owner_uuid = str(uuid4())
    occupant_uuid = str(uuid4())
    resource = ResourceDict.model_validate(
        _resource_payload(
            uuid=owner_uuid,
            config={
                "type": "StrictCarrier",
                "sites": [
                    {
                        "uuid": str(uuid4()),
                        "template_name": "StrictCarrier",
                        "material_uuid": owner_uuid,
                        "index": "A1",
                        "label": "A1",
                        "occupied_by": occupant_uuid,
                        "position": {"x": 10, "y": 20, "z": 30},
                        "size": {"width": 40, "height": 50, "depth": 60},
                        "rotation": {"x": 1, "y": 2, "z": 3},
                        "content_type": ["plate", "plate"],
                        "vendor_extension": {"rack": 7},
                    }
                ],
            },
        )
    )

    dumped = resource.model_dump(by_alias=True)
    assert "sites" not in dumped["config"]
    assert dumped["config"]["type"] == "StrictCarrier"
    assert dumped["position"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    site = dumped["sites"][0]
    assert site["template_name"] == "StrictCarrier"
    assert site["material_uuid"] == owner_uuid
    assert site["occupied_material_uuid"] == occupant_uuid
    assert site["pose"]["position"] == {"x": 10.0, "y": 20.0, "z": 30.0}
    assert site["pose"]["position3d"] == {"x": 10.0, "y": 20.0, "z": 30.0}
    assert site["pose"]["size"] == {"width": 40.0, "height": 50.0, "depth": 60.0}
    assert site["pose"]["rotation"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert site["content_type"] == ["plate"]
    assert site["meta_data"]["legacy_fields"]["vendor_extension"] == {"rack": 7}
    assert {"position_x", "position_y", "position_z", "width", "length", "depth"}.isdisjoint(site)
    assert "occupied_by" not in site
    assert dumped["sites_initialized"] is True


def test_old_experiment_without_strict_identity_is_rejected_at_edge_import():
    experiment_path = (
        Path(__file__).resolve().parents[2]
        / "unilabos"
        / "test"
        / "experiments"
        / "prcxi_9320_slim.json"
    )
    raw = json.loads(experiment_path.read_text(encoding="utf-8"))

    with pytest.raises((ValueError, ValidationError), match="UUID|Field required"):
        ResourceTreeSet.from_raw_dict_list(deepcopy(raw["nodes"]))


def test_sites_initialized_state_machine_preserves_authoritative_empty_snapshot():
    owner_uuid = str(uuid4())
    authoritative_empty = ResourceDict.model_validate(
        _resource_payload(
            uuid=owner_uuid,
            template_name="StrictCarrier",
            available_sites=[{"index": "A1", "label": "A1"}],
            sites=[],
            sites_initialized=True,
        )
    )
    assert authoritative_empty.sites_initialized is True
    assert authoritative_empty.sites == []
    assert "available_sites" not in authoritative_empty.model_dump()

    pending = ResourceDict.model_validate(
        _resource_payload(uuid=owner_uuid, sites=[], sites_initialized=False)
    )
    assert pending.sites_initialized is False
    assert pending.sites == []

    populated = ResourceDict.model_validate(
        _resource_payload(
            uuid=owner_uuid,
            template_name="StrictCarrier",
            sites=[_site_payload(owner_uuid)],
            sites_initialized=False,
        )
    )
    assert populated.sites_initialized is True


def test_canonical_site_rejects_unknown_or_conflicting_fields():
    owner_uuid = str(uuid4())
    canonical = {
        "schema_version": 1,
        "uuid": str(uuid4()),
        "template_name": "StrictCarrier",
        "material_uuid": owner_uuid,
        "index": 0,
        "label": "A1",
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResourceSite.model_validate({**canonical, "unexpected": True})

    with pytest.raises(ValidationError, match="material_uuid"):
        ResourceDict.model_validate(
            _resource_payload(
                uuid=owner_uuid,
                template_name="StrictCarrier",
                sites=[{**canonical, "material_uuid": str(uuid4())}],
            )
        )

    resource = ResourceDict.model_validate(
        _resource_payload(
            position={"x": 1, "y": 2, "z": 3},
            pose={"position": {"x": 9, "y": 8, "z": 7}},
        )
    )
    assert resource.position.model_dump() == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert resource.pose.position.model_dump() == {"x": 9.0, "y": 8.0, "z": 7.0}


def test_dynamic_position_can_be_unknown_without_zero_default():
    resource = ResourceDict.model_validate(_resource_payload(position=None))

    assert resource.position is None
    dumped = resource.model_dump(by_alias=True)
    assert dumped["position"] is None
    assert ResourceDict.model_validate(dumped).position is None


def test_legacy_full_position_shape_migrates_to_pose_and_dynamic_xyz():
    resource = ResourceDict.model_validate(
        _resource_payload(
            position={
                "position": {"x": 1, "y": 2, "z": 3},
                "position3d": {"x": 4, "y": 5, "z": 6},
                "size": {"width": 7, "height": 8, "depth": 9},
            },
        )
    )

    assert resource.position.model_dump() == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert resource.pose.position3d.model_dump() == {"x": 4.0, "y": 5.0, "z": 6.0}
    assert resource.pose.size.model_dump() == {"depth": 9.0, "width": 7.0, "height": 8.0}


def test_legacy_occupied_name_is_resolved_and_checked_bidirectionally():
    owner_uuid = str(uuid4())
    occupant_uuid = str(uuid4())
    raw = [
        _resource_payload(
            uuid=owner_uuid,
            sites=[
                {
                    "uuid": str(uuid4()),
                    "template_name": "StrictCarrier",
                    "material_uuid": owner_uuid,
                    "index": "A1",
                    "label": "A1",
                    "occupied_by": "tube-1",
                }
            ],
        ),
        _resource_payload(
            id="tube-1",
            uuid=occupant_uuid,
            name="tube-1",
            type="tube",
            config={"type": "Tube"},
            parent_uuid=owner_uuid,
            position={"x": 0, "y": 0, "z": 0},
        ),
    ]
    tree = ResourceTreeSet.from_raw_dict_list(raw)
    assert tree.root_nodes[0].res_content.sites[0].occupied_material_uuid == occupant_uuid

    broken = deepcopy(raw)
    broken[1]["parent_uuid"] = str(uuid4())
    with pytest.raises(ValueError, match="不在物料树|不属于 owner"):
        ResourceTreeSet.from_raw_dict_list(broken)


@pytest.mark.parametrize("capacity", [0, 1, 111, 384, 1024])
def test_site_model_preserves_large_root_lists(capacity):
    owner_uuid = str(uuid4())
    sites = [
        {
            "schema_version": 1,
            "uuid": str(uuid4()),
            "template_name": "LargeCarrier",
            "material_uuid": owner_uuid,
            "index": index,
            "label": f"S{index}",
            "pose": {
                "position": {"x": float(index), "y": 0, "z": 0},
                "position3d": {"x": float(index), "y": 0, "z": 0},
            },
            "content_type": ["plate"],
            "meta_data": {"ordinal": index},
        }
        for index in range(capacity)
    ]
    resource = ResourceDict.model_validate(
        _resource_payload(
            uuid=owner_uuid,
            config={"type": "LargeCarrier"},
            template_name="LargeCarrier",
            sites=sites,
        )
    )
    restored = ResourceDict.model_validate(resource.model_dump(by_alias=True))
    assert restored.sites is not None
    assert len(restored.sites) == capacity
    if capacity:
        assert restored.sites[-1].pose.position3d.x == float(capacity - 1)
        assert restored.sites[-1].meta_data == {"ordinal": capacity - 1}


def test_site_snapshot_merge_is_uuid_based_and_never_deletes_missing_sites():
    owner_uuid = str(uuid4())
    first_uuid = str(uuid4())
    second_uuid = str(uuid4())
    current = [
        {
            "schema_version": 1,
            "uuid": first_uuid,
            "template_name": "StrictCarrier",
            "material_uuid": owner_uuid,
            "index": 0,
            "label": "A1",
            "visible": True,
            "occupied_material_uuid": None,
            "meta_data": {"vendor": {"stable": 1, "old": 2}},
        },
        {
            "schema_version": 1,
            "uuid": second_uuid,
            "template_name": "StrictCarrier",
            "material_uuid": owner_uuid,
            "index": 1,
            "label": "A2",
            "visible": True,
            "occupied_material_uuid": None,
            "meta_data": {},
        },
    ]
    occupant_uuid = str(uuid4())
    incoming = [
        {
            **current[0],
            "occupied_material_uuid": occupant_uuid,
        }
    ]

    merged = merge_resource_sites(current, incoming)
    assert merged is not None
    assert [site["uuid"] for site in merged] == [first_uuid, second_uuid]
    assert merged[0]["occupied_material_uuid"] == occupant_uuid
    assert merged[0]["visible"] is True
    assert merged[0]["meta_data"] == current[0]["meta_data"]

    fixed_field_change = [{**incoming[0], "visible": False}]
    with pytest.raises(ValueError, match="不可变字段 visible 冲突"):
        merge_resource_sites(current, fixed_field_change)

    metadata_change = [{**incoming[0], "meta_data": {"vendor": {"stable": 9}}}]
    with pytest.raises(ValueError, match="不可变字段 meta_data 冲突"):
        merge_resource_sites(current, metadata_change)

    conflicting = [{**incoming[0], "uuid": str(uuid4())}]
    with pytest.raises(ValueError, match="身份冲突"):
        merge_resource_sites(current, conflicting)
