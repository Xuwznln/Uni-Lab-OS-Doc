from __future__ import annotations


from uuid import uuid4


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


def test_non_container_keeps_tracker_roots_none():
    resource = ResourceDict.model_validate(
        _resource_payload(type="tip_spot", data={"tip_state": {"has_tip": True}})
    )

    assert resource.liquids is None
    assert resource.liquid_history is None
    assert resource.unknown_counter is None
    assert assemble_tracker_state(resource) == {"tip_state": {"has_tip": True}}
