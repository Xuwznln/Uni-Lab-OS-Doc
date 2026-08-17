from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pylabrobot.resources import Carrier, Coordinate, Resource, ResourceHolder

from unilabos.resources.itemized_carrier import ItemizedCarrier
from unilabos.resources.site_definition import normalize_available_sites
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


def test_plr_offline_uuid_migration_updates_site_sidecar_references():
    owner_uuid = str(uuid4())
    occupant_uuid = str(uuid4())
    migrated_owner_uuid = str(uuid4())
    migrated_occupant_uuid = str(uuid4())
    site_payload = {
        "material_uuid": owner_uuid,
        "occupied_material_uuid": occupant_uuid,
    }
    resource = {
        "data": {"unilabos_uuid": owner_uuid},
        "sites": [deepcopy(site_payload)],
        "extra": {EXTRA_SITES: {"A1": deepcopy(site_payload)}},
        "children": [
            {
                "data": {"unilabos_uuid": occupant_uuid},
                "parent_uuid": owner_uuid,
                "extra": {},
                "children": [],
            }
        ],
    }

    tracker = DeviceNodeResourceTracker()
    assert tracker.loop_update_uuid(
        resource,
        {owner_uuid: migrated_owner_uuid, occupant_uuid: migrated_occupant_uuid},
    ) == 2

    assert resource["uuid"] == migrated_owner_uuid
    assert resource["data"]["unilabos_uuid"] == migrated_owner_uuid
    assert resource["children"][0]["uuid"] == migrated_occupant_uuid
    assert resource["children"][0]["data"]["unilabos_uuid"] == migrated_occupant_uuid
    assert resource["children"][0]["parent_uuid"] == migrated_owner_uuid
    assert resource["sites"][0] == {
        "material_uuid": migrated_owner_uuid,
        "occupied_material_uuid": migrated_occupant_uuid,
    }
    assert resource["extra"][EXTRA_SITES]["A1"] == resource["sites"][0]


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


def test_registry_available_sites_normalizes_legacy_geometry_without_entering_instance():
    definitions = normalize_available_sites(
        [
            {
                "index": "A1",
                "label": "A1",
                "position_x": 1,
                "width": 10,
                "length": 20,
            }
        ]
    )
    assert definitions[0]["pose"]["position3d"]["x"] == 1
    assert definitions[0]["pose"]["size"]["height"] == 20

    resource = ResourceDict.model_validate(
        _resource_payload(available_sites=definitions, sites=[], sites_initialized=True)
    )
    assert "available_sites" not in resource.model_dump()


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


def test_itemized_carrier_uses_native_sites_and_injected_extra_metadata():
    carrier = ItemizedCarrier(
        name="carrier",
        size_x=100,
        size_y=100,
        size_z=20,
        sites={"A1": None, "A2": None},
    )
    carrier.unilabos_uuid = str(uuid4())
    set_plr_template_name(carrier, "StrictCarrier")
    assert carrier.unilabos_extra[EXTRA_RESOURCE_CLASS] == "StrictCarrier"
    native = carrier.serialize()
    assert set(native["sites"][0]) == {
        "label",
        "visible",
        "occupied_by",
        "position",
        "size",
        "content_type",
    }

    backend_sites = [
        ResourceSite(
            uuid=str(uuid4()),
            template_name="StrictCarrier",
            material_uuid=carrier.unilabos_uuid,
            index=index,
            label=raw_site["label"],
            pose={
                "position": raw_site["position"],
                "position3d": raw_site["position"],
                "size": raw_site["size"],
            },
            content_type=raw_site["content_type"],
        )
        for index, raw_site in enumerate(native["sites"])
    ]
    apply_plr_site_metadata(carrier, {carrier.name: backend_sites})

    extracted = extract_plr_sites(carrier, carrier.serialize())
    assert extracted is not None
    injected_site = extracted[0].model_copy(
        deep=True,
        update={
            "content_type": ["plate"],
            "allowed_resource_template_uuids": ["template-1"],
            "parent_link": "deck/main",
            "meta_data": {"vendor": {"slot": "A1"}},
        }
    )
    injected_site.pose.position.x = 101
    injected_site.pose.position.y = 102
    apply_plr_site_metadata(carrier, {carrier.name: [injected_site, extracted[1]]})

    assert carrier.unilabos_extra[EXTRA_SITES]["A1"]["uuid"] == injected_site.uuid
    assert "uuid" not in carrier.serialize()["sites"][0]
    restored = extract_plr_sites(carrier, carrier.serialize())
    assert restored is not None
    assert restored[0].content_type == ["plate"]
    assert restored[0].allowed_resource_template_uuids == ["template-1"]
    assert restored[0].parent_link == "deck/main"
    assert restored[0].meta_data == {"vendor": {"slot": "A1"}}
    assert restored[0].pose.position.model_dump() == {"x": 101.0, "y": 102.0, "z": 0.0}


def test_itemized_carrier_sidecar_cannot_hide_missing_native_site():
    carrier = ItemizedCarrier(
        name="carrier",
        size_x=100,
        size_y=100,
        size_z=20,
        sites={"A1": None},
    )
    carrier.unilabos_uuid = str(uuid4())
    set_plr_template_name(carrier, "StrictCarrier")
    carrier.unilabos_extra = {
        EXTRA_SITES: {
            "A2": {
                "schema_version": 1,
                "uuid": str(uuid4()),
                "template_name": "StrictCarrier",
                "material_uuid": carrier.unilabos_uuid,
                "index": 1,
                "label": "A2",
            }
        }
    }

    with pytest.raises(ValueError, match="缺少.*sidecar"):
        extract_plr_sites(carrier, carrier.serialize())


def test_itemized_carrier_deserialization_adapter_uses_native_shape():
    owner_uuid = str(uuid4())
    occupant_uuid = str(uuid4())
    site = ResourceSite(
        uuid=str(uuid4()),
        template_name="StrictCarrier",
        material_uuid=owner_uuid,
        index=0,
        label="A1",
        occupied_material_uuid=occupant_uuid,
        pose={
            "position": {"x": 101, "y": 102, "z": 0},
            "position3d": {"x": 1, "y": 2, "z": 3},
            "size": {"width": 4, "height": 5, "depth": 6},
        },
        content_type=["tube"],
    )

    native = sites_for_plr_deserialization(
        ItemizedCarrier,
        [site],
        {occupant_uuid: "tube-1"},
    )
    assert native == [
        {
            "label": "A1",
            "visible": True,
            "occupied_by": "tube-1",
            "position": {"x": 1.0, "y": 2.0, "z": 3.0},
            "size": {"width": 4.0, "height": 5.0, "depth": 6.0},
            "content_type": ["tube"],
        }
    ]


def test_plr_serialized_site_boundary_has_only_native_fields():
    site = ResourceSite(
        uuid=str(uuid4()),
        template_name="StrictCarrier",
        material_uuid=str(uuid4()),
        index=7,
        label="T8",
        visible=False,
        occupied_material_uuid=str(uuid4()),
        pose={
            "position": {"x": 101, "y": 102, "z": 0},
            "position3d": {"x": 1, "y": 2, "z": 3},
            "size": {"width": 4, "height": 5, "depth": 6},
        },
        content_type=["plate", "plate"],
        allowed_resource_template_uuids=["template-1"],
        meta_data={"vendor": "kept-in-ResourceSite"},
    )

    native = resource_site_to_plr_site(site, occupied_by="plate-1")

    assert native == {
        "label": "T8",
        "visible": False,
        "occupied_by": "plate-1",
        "position": {"x": 1.0, "y": 2.0, "z": 3.0},
        "size": {"width": 4.0, "height": 5.0, "depth": 6.0},
        "content_type": ["plate"],
    }
    assert set(native) == {
        "label",
        "visible",
        "occupied_by",
        "position",
        "size",
        "content_type",
    }
    assert site.pose.position.model_dump() == {"x": 101.0, "y": 102.0, "z": 0.0}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PLRSerializedSite.model_validate({**native, "uuid": site.uuid})
    with pytest.raises(ValidationError, match="Field required"):
        PLRSerializedSite.model_validate({key: value for key, value in native.items() if key != "size"})


def test_plr_site_adapter_metadata_requires_exact_bidirectional_match():
    owner_uuid = str(uuid4())
    occupant_uuid = str(uuid4())
    site = ResourceSite(
        uuid=str(uuid4()),
        template_name="FakeDeck",
        material_uuid=owner_uuid,
        index=0,
        label="T1",
        occupied_material_uuid=occupant_uuid,
        pose={
            "position": {"x": 101, "y": 102, "z": 0},
            "position3d": {"x": 1, "y": 2, "z": 3},
            "size": {"width": 4, "height": 5, "depth": 6},
        },
        content_type=["plate"],
    )
    plate = Resource("plate-1", 1, 1, 1)
    plate.unilabos_uuid = occupant_uuid

    class FakeDeck:
        __unilabos_plr_site_format__ = True
        __unilabos_resource_site_storage__ = True

        def __init__(self, native_site):
            self.name = "deck"
            self.children = [plate]
            self.sites = [site.model_copy(update={"uuid": str(uuid4())})]
            self._native_site = native_site
            self._ordering = {}
            self._site_expected_occupants = {}
            self._site_expected_occupant_names = {0: "plate-1"}

        def _serialize_plr_sites(self):
            return [self._native_site]

    native = resource_site_to_plr_site(site, occupied_by="plate-1")
    assert sites_for_plr_deserialization(FakeDeck, [site], {occupant_uuid: "plate-1"}) == [
        site.model_dump()
    ]
    deck = FakeDeck(native)
    apply_plr_site_metadata(deck, {"deck": [site]})

    assert deck.sites == [site]
    assert deck._site_expected_occupants == {0: occupant_uuid}
    assert deck._site_expected_occupant_names == {}

    mismatched = deepcopy(native)
    mismatched["position"]["x"] = 99
    with pytest.raises(ValueError, match="与根字段不一致"):
        apply_plr_site_metadata(FakeDeck(mismatched), {"deck": [site]})


def test_prcxi_deck_uses_resource_sites_and_serializes_plr_boundary():
    prcxi = pytest.importorskip(
        "unilabos.devices.liquid_handling.prcxi.prcxi",
        reason="PRCXI 驱动导入依赖 ROS Jazzy 运行时",
        exc_type=ImportError,
    )
    owner_uuid = str(uuid4())
    site = ResourceSite(
        uuid=str(uuid4()),
        template_name="PRCXI9300Deck",
        material_uuid=owner_uuid,
        index=0,
        label="T1",
        pose={
            "position": {"x": 10, "y": 20, "z": 0},
            "position3d": {"x": 10, "y": 20, "z": 30},
            "size": {"width": 40, "height": 50, "depth": 60},
        },
        content_type=["plate"],
    )
    deck = prcxi.PRCXI9300Deck(
        name="deck",
        size_x=100,
        size_y=100,
        size_z=20,
        sites=[site],
    )

    assert len(deck.sites) == 1
    assert isinstance(deck.sites[0], ResourceSite)
    assert deck.sites[0].pose.position3d.x == 10
    assert deck.sites[0].pose.size.height == 50
    serialized_sites = deck.serialize()["sites"]
    assert serialized_sites == [resource_site_to_plr_site(site)]
    assert {"uuid", "material_uuid", "template_name", "index"}.isdisjoint(serialized_sites[0])

    plate = Resource("plate-1", 10, 10, 10)
    plate.unilabos_uuid = str(uuid4())
    deck.assign_child_resource(plate, spot=0)
    assert deck.serialize()["sites"][0]["occupied_by"] == "plate-1"
    assert deck.sites[0].occupied_material_uuid == plate.unilabos_uuid

    deck.unassign_child_resource(plate)
    assert deck.serialize()["sites"][0]["occupied_by"] is None
    assert deck.sites[0].occupied_material_uuid is None


def test_prcxi_resource_sites_remain_canonical_for_constructor():
    prcxi = pytest.importorskip(
        "unilabos.devices.liquid_handling.prcxi.prcxi",
        reason="PRCXI 驱动导入依赖 ROS Jazzy 运行时",
        exc_type=ImportError,
    )
    owner_uuid = str(uuid4())
    occupant_uuid = str(uuid4())
    site = ResourceSite(
        uuid=str(uuid4()),
        template_name="PRCXI9300Deck",
        material_uuid=owner_uuid,
        index=0,
        label="T1",
        occupied_material_uuid=occupant_uuid,
        pose={
            "position": {"x": 101, "y": 102, "z": 0},
            "position3d": {"x": 1, "y": 2, "z": 3},
            "size": {"width": 4, "height": 5, "depth": 6},
        },
        content_type=["plate"],
    )

    native = sites_for_plr_deserialization(
        prcxi.PRCXI9300Deck,
        [site],
        {occupant_uuid: "plate-1"},
    )

    assert native == [site.model_dump()]


def test_standard_plr_carrier_site_roundtrip_preserves_identity_and_metadata():
    holder = ResourceHolder(
        "slot-A",
        size_x=10,
        size_y=20,
        size_z=5,
        child_location=Coordinate.zero(),
    )
    holder.location = Coordinate(1, 2, 3)
    carrier = Carrier("carrier", 100, 100, 20, sites={7: holder}, model="carrier-model")
    occupant = Resource("tube", 1, 1, 1)
    holder.assign_child_resource(occupant, location=Coordinate.zero())
    carrier.unilabos_uuid = str(uuid4())
    holder.unilabos_uuid = str(uuid4())
    occupant.unilabos_uuid = str(uuid4())
    set_plr_template_name(carrier, "carrier-model")
    backend_site = ResourceSite(
        uuid=str(uuid4()),
        template_name="carrier-model",
        material_uuid=carrier.unilabos_uuid,
        index=7,
        label="slot-A",
        occupied_material_uuid=occupant.unilabos_uuid,
        pose={
            "position": {"x": 1, "y": 2, "z": 3},
            "position3d": {"x": 1, "y": 2, "z": 3},
            "size": {"width": 10, "height": 20, "depth": 5},
        },
    )
    apply_plr_site_metadata(carrier, {carrier.name: [backend_site]})

    tree = ResourceTreeSet.from_plr_resources([carrier], known_newly_created=True)
    site = tree.root_nodes[0].res_content.sites[0]
    assert site.template_name == "carrier-model"
    assert site.index == 7
    assert site.label == "slot-A"
    assert site.material_uuid == carrier.unilabos_uuid
    assert site.occupied_material_uuid == occupant.unilabos_uuid
    assert site.pose.position.model_dump() == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert site.pose.position3d.model_dump() == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert site.pose.size.model_dump() == {"depth": 5.0, "width": 10.0, "height": 20.0}
    loaded = ResourceTreeSet.load(tree.dump())
    assert loaded.root_nodes[0].res_content.sites[0] == site

    payload = site.model_copy(
        update={
            "content_type": ["tube"],
            "allowed_resource_template_uuids": ["tube-template"],
            "meta_data": {"vendor": {"slot": 7}},
        }
    )
    apply_plr_site_metadata(carrier, {carrier.name: [payload]})
    extracted = extract_plr_sites(carrier, carrier.serialize())
    assert extracted is not None
    assert extracted[0].uuid == site.uuid
    assert extracted[0].content_type == ["tube"]
    assert extracted[0].allowed_resource_template_uuids == ["tube-template"]
    assert extracted[0].meta_data == {"vendor": {"slot": 7}}


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


def test_graphio_keeps_protocol_fields_at_resource_root():
    graphio = pytest.importorskip(
        "unilabos.resources.graphio",
        reason="GraphIO 依赖 ROS Jazzy 生成的 unilabos_msgs",
        exc_type=ImportError,
    )
    owner_uuid = str(uuid4())
    node = _resource_payload(
        uuid=owner_uuid,
        template_name="StrictCarrier",
        resource_template_uuid="template-uuid",
        meta_data={"vendor": {"lot": "A-1"}},
        sites=[
            _site_payload(
                owner_uuid,
                pose={
                    "position": {"x": 4, "y": 5, "z": 6},
                    "position3d": {"x": 4, "y": 5, "z": 6},
                },
            )
        ],
        sites_initialized=True,
        vendor_config="driver-only",
    )
    tree = graphio.canonicalize_nodes_data([node])
    dumped = tree.dump()[0][0]

    assert dumped["template_name"] == "StrictCarrier"
    assert dumped["resource_template_uuid"] == "template-uuid"
    assert dumped["position"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert dumped["meta_data"] == {"vendor": {"lot": "A-1"}}
    assert "meta_data" not in dumped["config"]
    assert "meta_data" not in dumped["data"]
    assert dumped["sites"][0]["pose"]["position3d"]["x"] == 4.0
    assert dumped["config"]["vendor_config"] == "driver-only"
    assert "sites" not in dumped["config"]
    assert "template_name" not in dumped["config"]

    transport_uuid = str(uuid4())
    transport_node = _resource_payload(
        uuid="",
        data={"unilabos_uuid": transport_uuid},
    )
    transport_tree = graphio.canonicalize_nodes_data([transport_node])
    transported = transport_tree.dump()[0][0]
    assert transported["uuid"] == transport_uuid
    assert "unilabos_uuid" not in transported["data"]

    unknown_position = _resource_payload(position=None)
    unknown_tree = graphio.canonicalize_nodes_data([unknown_position])
    assert unknown_tree.dump()[0][0]["position"] is None

    conflicting_position = _resource_payload(position=None, x=1)
    with pytest.raises(ValueError, match="position=null.*冲突"):
        graphio.canonicalize_nodes_data([conflicting_position])


def test_ros_resource_config_transport_normalizes_root_site_fields():
    message_converter = pytest.importorskip(
        "unilabos.ros.msgs.message_converter",
        reason="ROS 消息转换依赖 Jazzy 版 unilabos_msgs.Resource",
        exc_type=ImportError,
    )
    site_uuid = str(uuid4())
    owner_uuid = str(uuid4())
    config = message_converter.obtain_config_with_root_fields(
        {
            "config": {
                "available_sites": [
                    {
                        "index": "A1",
                        "label": "A1",
                        "position": {"x": 1},
                    }
                ]
            },
            "template_name": "StrictCarrier",
            "resource_template_uuid": "template-uuid",
            "meta_data": {"vendor": {"lot": "A-1"}},
            "pose": {
                "size": {"width": 100, "height": 200, "depth": 30},
                "position": {"x": 1, "y": 2, "z": 3},
                "position3d": {"x": 10, "y": 20, "z": 30},
            },
            "available_sites": [
                {
                    "index": "A1",
                    "label": "A1",
                    "pose": {"position": {"x": 1}, "position3d": {"x": 1}},
                }
            ],
            "sites": [
                ResourceSite(
                    uuid=site_uuid,
                    template_name="StrictCarrier",
                    material_uuid=owner_uuid,
                    index="A1",
                    label="A1",
                    pose={"position": {"x": 1}, "position3d": {"x": 1}},
                )
            ],
            "sites_initialized": True,
        }
    )

    assert "available_sites" not in config
    assert config["resource_template_uuid"] == "template-uuid"
    assert config[EXTRA_RESOURCE_META_DATA] == {"vendor": {"lot": "A-1"}}
    assert config["pose"]["position"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert config["pose"]["position3d"] == {"x": 10.0, "y": 20.0, "z": 30.0}
    assert config["sites"][0]["uuid"] == site_uuid
    assert config["sites_initialized"] is True

    restored = ResourceDict.model_validate(
        _resource_payload(
            uuid=owner_uuid,
            template_name="StrictCarrier",
            position={"x": 40, "y": 50, "z": 60},
            config=config,
        )
    )
    assert "pose" not in restored.config
    assert restored.position.model_dump() == {"x": 40.0, "y": 50.0, "z": 60.0}
    assert restored.pose.position.model_dump() == {"x": 1.0, "y": 2.0, "z": 3.0}

    unknown_config = message_converter.obtain_config_with_root_fields(
        {"config": {}, "position": None}
    )
    assert unknown_config[message_converter.ROS_CONFIG_POSITION_UNKNOWN] is True

    ros_resource = message_converter.convert_to_ros_msg(
        message_converter.Resource,
        {
            "id": "unknown-position",
            "uuid": owner_uuid,
            "name": "unknown-position",
            "type": "carrier",
            "class": "",
            "position": None,
            "meta_data": {"vendor": {"lot": "A-1"}},
            "config": {},
            "data": {},
        },
    )
    restored_transport = message_converter.convert_from_ros_msg(ros_resource)
    assert restored_transport["position"] is None
    assert restored_transport["meta_data"] == {"vendor": {"lot": "A-1"}}
    assert EXTRA_RESOURCE_META_DATA not in restored_transport["config"]
    assert message_converter.ROS_CONFIG_POSITION_UNKNOWN not in restored_transport["config"]

    ros_resource.pose.position.x = 1.0
    ros_resource.pose.position.z = float("inf")
    restored_padding = message_converter.convert_from_ros_msg(ros_resource)
    assert restored_padding["position"] is None

    known_resource = message_converter.convert_to_ros_msg(
        message_converter.Resource,
        {
            "id": "known-position",
            "uuid": owner_uuid,
            "name": "known-position",
            "type": "carrier",
            "class": "",
            "position": {"x": 1, "y": 2, "z": 3},
            "config": {},
            "data": {},
        },
    )
    known_resource.pose.position.z = float("inf")
    with pytest.raises(ValueError, match="position 必须是有限 xyz"):
        message_converter.convert_from_ros_msg(known_resource)


@pytest.mark.parametrize("legacy_source", ["config", "data"])
def test_ros_missing_metadata_sidecar_allows_legacy_promotion(legacy_source):
    message_converter = pytest.importorskip(
        "unilabos.ros.msgs.message_converter",
        reason="ROS 消息转换依赖 Jazzy 版 unilabos_msgs.Resource",
        exc_type=ImportError,
    )
    owner_uuid = str(uuid4())
    legacy_meta_data = {"vendor": {"lot": f"legacy-{legacy_source}"}}
    ros_resource = message_converter.convert_to_ros_msg(
        message_converter.Resource,
        {
            "id": f"legacy-{legacy_source}",
            "uuid": owner_uuid,
            "name": f"legacy-{legacy_source}",
            "type": "carrier",
            "class": "",
            "position": {"x": 1, "y": 2, "z": 3},
            "config": {},
            "data": {},
        },
    )
    config = json.loads(ros_resource.config)
    config.pop(EXTRA_RESOURCE_META_DATA)
    data = json.loads(ros_resource.data)
    if legacy_source == "config":
        config["meta_data"] = legacy_meta_data
    else:
        data["meta_data"] = legacy_meta_data
    ros_resource.config = json.dumps(config)
    ros_resource.data = json.dumps(data)

    restored = message_converter.convert_from_ros_msg(ros_resource)
    assert "meta_data" not in restored
    canonical = ResourceDict.model_validate(
        _resource_payload(
            uuid=owner_uuid,
            config=restored["config"],
            data=restored["data"],
            position=restored["position"],
        )
    )
    assert canonical.meta_data == legacy_meta_data
    assert "meta_data" not in canonical.config
    assert "meta_data" not in canonical.data
