"""task 信封组装 + 下发单测（#18 §5.1 / §5.4）。"""

import math

from unilabos.sim.fleet.rmf.task_dispatcher import (
    RmfTaskDispatcher,
    build_cancel_request,
    build_delivery_request,
    build_go_to_request,
    build_patrol_request,
)


def test_go_to_envelope_structure():
    env = build_go_to_request("lounge", orientation_deg=90)
    assert env["type"] == "dispatch_task_request"
    req = env["request"]
    assert req["category"] == "compose"
    assert req["description"]["category"] == "go_to_place"
    activity = req["description"]["phases"][0]["activity"]
    assert activity["category"] == "go_to_place"
    place = activity["description"]["one_of"][0]
    assert place["waypoint"] == "lounge"
    assert math.isclose(place["orientation"], math.pi / 2, abs_tol=1e-9)


def test_go_to_robot_task_request_when_fleet_and_robot():
    env = build_go_to_request("lounge", fleet="unilab_agv", robot="agv_sim_01")
    assert env["type"] == "robot_task_request"
    assert env["robot"] == "agv_sim_01"
    assert env["fleet"] == "unilab_agv"


def test_delivery_envelope_structure():
    env = build_delivery_request("pantry", "coke_dispenser", "hardware_2", "coke_ingestor",
                                 payload=[{"sku": "coke", "quantity": 1}])
    req = env["request"]
    assert req["category"] == "delivery"
    assert req["description"]["pickup"] == {"place": "pantry", "handler": "coke_dispenser",
                                            "payload": [{"sku": "coke", "quantity": 1}]}
    assert req["description"]["dropoff"]["place"] == "hardware_2"
    assert req["description"]["dropoff"]["handler"] == "coke_ingestor"


def test_patrol_envelope_structure():
    env = build_patrol_request(["patrol_A1", "patrol_B"], rounds=2)
    assert env["request"]["category"] == "patrol"
    assert env["request"]["description"] == {"places": ["patrol_A1", "patrol_B"], "rounds": 2}


def test_cancel_request():
    assert build_cancel_request("task-1") == {"type": "cancel_task_request", "task_id": "task-1"}


def test_dispatcher_publish_fn_called():
    captured = {}

    def fake_publish(json_msg, request_id):
        captured["json"] = json_msg
        captured["rid"] = request_id

    dispatcher = RmfTaskDispatcher(publish_fn=fake_publish)
    rid = dispatcher.dispatch(build_go_to_request("lounge"))
    assert rid == captured["rid"]
    assert "go_to_place" in captured["json"]


def test_dispatcher_unwired_raises():
    import pytest

    with pytest.raises(RuntimeError):
        RmfTaskDispatcher().dispatch(build_cancel_request("t"))
