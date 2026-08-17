from __future__ import annotations


from uuid import uuid4


import pytest
from pylabrobot.resources import Coordinate, Rotation


from pylabrobot import serializer as plr_serializer


from unilabos.resources.container import RegularContainer


from unilabos.resources.resource_pose import (
    ResourceDictPosition,
    ResourceDictPositionObject,
    ResourceDictPositionSize,
)


from unilabos.resources.resource_tracker import (
    EXTRA_RESOURCE_CLASS,
    EXTRA_RESOURCE_META_DATA,
    RESOURCE_ROOT_FIELDS,
    TRACKER_STATE_KEYS,
    ResourceDict,
    ResourceDictInstance,
    ResourceTreeSet,
    assemble_tracker_state,
)


def _resource_payload(**overrides):
    payload = {
        "id": "beaker",
        "uuid": str(uuid4()),
        "name": "beaker",
        "type": "container",
        "class": "",
        "config": {"type": "RegularContainer"},
        "data": {},
        "extra": {},
    }
    payload.update(overrides)
    return payload


def test_tracker_state_is_promoted_from_data_to_root():
    resource = ResourceDict.model_validate(
        _resource_payload(
            data={
                "thing": "beaker_volume_tracker",
                "max_volume": 100.0,
                "liquids": [["water", 30.0, "ul"]],
                "liquid_history": [["water", 30.0, "ul"]],
                "unknown_counter": 0,
            }
        )
    )

    assert resource.liquids == [("water", 30.0, "ul")]
    assert resource.liquid_history == [("water", 30.0, "ul")]
    assert resource.unknown_counter == 0
    assert resource.data == {"thing": "beaker_volume_tracker", "max_volume": 100.0}
    assert set(TRACKER_STATE_KEYS) <= set(RESOURCE_ROOT_FIELDS)


def test_root_tracker_state_wins_and_roundtrips_to_plr_shape():
    resource = ResourceDict.model_validate(
        _resource_payload(
            data={
                "max_volume": 100.0,
                "liquids": [["stale", 1.0, "ul"]],
                "liquid_history": [["stale", 1.0, "ul"]],
                "unknown_counter": 9,
            },
            liquids=[],
            liquid_history=[],
            unknown_counter=0,
        )
    )

    assert resource.liquids == []
    assert resource.liquid_history == []
    assert resource.unknown_counter == 0
    assert assemble_tracker_state(resource) == {
        "max_volume": 100.0,
        "liquids": [],
        "liquid_history": [],
        "unknown_counter": 0,
    }

    nested = ResourceDictInstance(resource).get_plr_nested_dict()
    assert nested["data"] == assemble_tracker_state(resource)
    assert all(state_key not in nested for state_key in TRACKER_STATE_KEYS)


def test_non_container_keeps_tracker_roots_none():
    resource = ResourceDict.model_validate(
        _resource_payload(type="tip_spot", data={"tip_state": {"has_tip": True}})
    )

    assert resource.liquids is None
    assert resource.liquid_history is None
    assert resource.unknown_counter is None
    assert assemble_tracker_state(resource) == {"tip_state": {"has_tip": True}}


def test_plr_container_tracker_state_survives_resource_tree_roundtrip(monkeypatch):
    container = RegularContainer(
        name="beaker",
        size_x=10,
        size_y=10,
        size_z=20,
        max_volume=100.0,
    )
    container.tracker.add_liquid("water", 50.0)
    container.tracker.add_liquid(None, 10.0)
    container.tracker.remove_liquid(20.0)
    container.unilabos_uuid = str(uuid4())
    container.unilabos_extra = {
        EXTRA_RESOURCE_CLASS: "BeakerTemplate",
        EXTRA_RESOURCE_META_DATA: {"vendor": {"lot": "A-1"}},
    }

    tree = ResourceTreeSet.from_plr_resources([container], known_newly_created=True)
    root = tree.root_nodes[0].res_content
    original_state = container.serialize_state()

    expected_liquids = [
        (item[0], item[1], item[2] if len(item) >= 3 else "ul")
        for item in original_state["liquids"]
    ]
    expected_history = [
        (item[0], item[1], item[2] if len(item) >= 3 else "ul")
        for item in original_state["liquid_history"]
    ]
    assert root.liquids == expected_liquids
    assert root.liquid_history == expected_history
    assert root.unknown_counter == original_state["unknown_counter"]
    assert all(state_key not in root.data for state_key in TRACKER_STATE_KEYS)
    assert root.template_name == "BeakerTemplate"
    assert EXTRA_RESOURCE_CLASS not in root.extra
    assert root.meta_data == {"vendor": {"lot": "A-1"}}
    assert EXTRA_RESOURCE_META_DATA not in root.extra

    # 静态 pose 与动态 position 可不同；PLR location 只跟随动态 position，
    # 静态几何通过 UniLabOS sidecar 无损往返。
    root.position = ResourceDictPositionObject(x=40, y=50, z=60)
    root.pose = ResourceDictPosition(
        position=ResourceDictPositionObject(x=1, y=2, z=3),
        position3d=ResourceDictPositionObject(x=10, y=20, z=30),
        size=ResourceDictPositionSize(width=10, height=10, depth=20),
    )

    monkeypatch.setattr("unilabos.resources.resource_tracker.register", lambda: None)
    original_deserialize = plr_serializer.deserialize

    def deserialize_geometry_without_backend_scan(value, allow_marshal=False):
        if isinstance(value, dict) and value.get("type") == "Coordinate":
            return Coordinate(value["x"], value["y"], value["z"])
        if isinstance(value, dict) and value.get("type") == "Rotation":
            return Rotation(value["x"], value["y"], value["z"])
        return original_deserialize(value, allow_marshal=allow_marshal)

    monkeypatch.setattr(plr_serializer, "deserialize", deserialize_geometry_without_backend_scan)
    restored = tree.to_plr_resources(skip_devices=False)[0]
    assert restored.serialize_state() == original_state
    assert restored.unilabos_extra[EXTRA_RESOURCE_CLASS] == "BeakerTemplate"
    assert restored.unilabos_extra[EXTRA_RESOURCE_META_DATA] == {
        "vendor": {"lot": "A-1"}
    }
    assert restored.location == Coordinate(40, 50, 60)

    roundtripped = ResourceTreeSet.from_plr_resources(
        [restored], known_newly_created=True
    ).root_nodes[0].res_content
    assert roundtripped.position.model_dump() == {"x": 40.0, "y": 50.0, "z": 60.0}
    assert roundtripped.pose.position.model_dump() == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert roundtripped.pose.position3d.model_dump() == {"x": 10.0, "y": 20.0, "z": 30.0}
    assert roundtripped.meta_data == {"vendor": {"lot": "A-1"}}


def test_plr_resource_without_location_preserves_unknown_position(monkeypatch):
    container = RegularContainer(
        name="unlocated_beaker",
        size_x=10,
        size_y=10,
        size_z=20,
        max_volume=100.0,
    )
    container.unilabos_uuid = str(uuid4())
    container.unilabos_extra = {EXTRA_RESOURCE_CLASS: "BeakerTemplate"}
    assert container.location is None

    tree = ResourceTreeSet.from_plr_resources([container], known_newly_created=True)
    assert tree.root_nodes[0].res_content.position is None
    assert tree.root_nodes[0].get_plr_nested_dict()["position"] is None

    monkeypatch.setattr("unilabos.resources.resource_tracker.register", lambda: None)
    original_deserialize = plr_serializer.deserialize

    def deserialize_geometry_without_backend_scan(value, allow_marshal=False):
        if isinstance(value, dict) and value.get("type") == "Coordinate":
            return Coordinate(value["x"], value["y"], value["z"])
        if isinstance(value, dict) and value.get("type") == "Rotation":
            return Rotation(value["x"], value["y"], value["z"])
        return original_deserialize(value, allow_marshal=allow_marshal)

    monkeypatch.setattr(plr_serializer, "deserialize", deserialize_geometry_without_backend_scan)
    restored = tree.to_plr_resources(skip_devices=False)[0]
    assert restored.location is None

    roundtripped = ResourceTreeSet.from_plr_resources(
        [restored], known_newly_created=True
    ).root_nodes[0].res_content
    assert roundtripped.position is None


@pytest.mark.parametrize("legacy_source", ["config", "data"])
def test_resource_tree_set_missing_metadata_sidecar_allows_legacy_promotion(
    monkeypatch, legacy_source
):
    container = RegularContainer(
        name=f"legacy_{legacy_source}_beaker",
        size_x=10,
        size_y=10,
        size_z=20,
        max_volume=100.0,
    )
    container.unilabos_uuid = str(uuid4())
    container.unilabos_extra = {EXTRA_RESOURCE_CLASS: "BeakerTemplate"}
    legacy_meta_data = {"vendor": {"lot": f"legacy-{legacy_source}"}}

    if legacy_source == "config":
        original_serialize = container.serialize

        def serialize_with_legacy_meta_data():
            serialized = original_serialize()
            serialized["meta_data"] = legacy_meta_data
            return serialized

        monkeypatch.setattr(container, "serialize", serialize_with_legacy_meta_data)
    else:
        container._unilabos_state = {"meta_data": legacy_meta_data}

    tree_resource = ResourceTreeSet.from_plr_resources(
        [container], known_newly_created=True
    ).root_nodes[0].res_content
    assert tree_resource.meta_data == legacy_meta_data
    assert "meta_data" not in tree_resource.config
    assert "meta_data" not in tree_resource.data


@pytest.mark.parametrize("legacy_source", ["config", "data"])
def test_graphio_plr_missing_metadata_sidecar_allows_legacy_promotion(
    monkeypatch, legacy_source
):
    graphio = pytest.importorskip(
        "unilabos.resources.graphio",
        reason="GraphIO 依赖 ROS Jazzy 生成的 unilabos_msgs",
        exc_type=ImportError,
    )
    container = RegularContainer(
        name=f"legacy_graphio_{legacy_source}_beaker",
        size_x=10,
        size_y=10,
        size_z=20,
        max_volume=100.0,
    )
    container.unilabos_uuid = str(uuid4())
    container.unilabos_extra = {EXTRA_RESOURCE_CLASS: "BeakerTemplate"}
    legacy_meta_data = {"vendor": {"lot": f"legacy-{legacy_source}"}}

    if legacy_source == "config":
        original_serialize = container.serialize

        def serialize_with_legacy_meta_data():
            serialized = original_serialize()
            serialized["meta_data"] = legacy_meta_data
            return serialized

        monkeypatch.setattr(container, "serialize", serialize_with_legacy_meta_data)
    else:
        container._unilabos_state = {"meta_data": legacy_meta_data}

    graph_payload = graphio.resource_plr_to_ulab(container)
    assert "meta_data" not in graph_payload
    graph_resource = ResourceDict.model_validate(graph_payload)
    assert graph_resource.meta_data == legacy_meta_data
    assert "meta_data" not in graph_resource.config
    assert "meta_data" not in graph_resource.data
