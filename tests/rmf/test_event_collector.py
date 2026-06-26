"""运行态归一化单测（#18 §5.1 / §4.4）。"""

from unilabos.sim.fleet.rmf.event_collector import (
    normalize_battery,
    normalize_door_state,
    normalize_fleet_state,
    normalize_lift_state,
    normalize_robot_state,
    robot_mode_to_status,
)


def test_normalize_battery():
    assert normalize_battery(87.5) == 0.875   # ROS msg 0-100 → 0-1
    assert normalize_battery(0.9) == 0.9       # 已是 0-1 原样
    assert normalize_battery(None) is None


def test_robot_mode_to_status():
    assert robot_mode_to_status(0) == "idle"
    assert robot_mode_to_status(1) == "charging"
    assert robot_mode_to_status(2) == "moving"
    assert robot_mode_to_status(8) == "error"
    assert robot_mode_to_status(None) == "idle"


def test_normalize_robot_state_dict_input():
    robot = {
        "name": "agv_sim_01",
        "task_id": "go_to-001",
        "battery_percent": 87.5,
        "mode": {"mode": 2},
        "location": {"x": 12.34, "y": 5.67, "yaw": 1.5708, "level_name": "L1"},
    }
    dto = normalize_robot_state(robot, "unilab_agv", mode="sim")
    assert dto["robotId"] == "agv_sim_01"
    assert dto["fleetName"] == "unilab_agv"
    assert dto["mapName"] == "L1"
    assert dto["pose"] == {"x": 12.34, "y": 5.67, "yaw": 1.5708}
    assert dto["battery"] == 0.875
    assert dto["status"] == "moving"
    assert dto["taskId"] == "go_to-001"
    assert dto["stale"] is False


def test_normalize_fleet_state_list_and_dict():
    robot = {"name": "r1", "mode": {"mode": 0}, "location": {"x": 0, "y": 0, "yaw": 0, "level_name": "L1"}}
    fleet_list = {"name": "f", "robots": [robot]}
    fleet_dict = {"name": "f", "robots": {"r1": robot}}
    assert len(normalize_fleet_state(fleet_list)) == 1
    assert len(normalize_fleet_state(fleet_dict)) == 1


def test_normalize_door_state():
    dto = normalize_door_state({"door_name": "main_door", "current_mode": {"value": 2}})
    assert dto == {"doorName": "main_door", "mode": "open", "stale": False}


def test_normalize_lift_state():
    dto = normalize_lift_state({
        "lift_name": "Lift1",
        "current_floor": "L1",
        "destination_floor": "L3",
        "door_state": 1,
        "motion_state": 1,
        "session_id": "s1",
    })
    assert dto["liftName"] == "Lift1"
    assert dto["doorState"] == "moving"
    assert dto["motionState"] == "up"
    assert dto["destinationFloor"] == "L3"


def test_stale_propagates():
    robot = {"name": "r", "mode": {"mode": 2}, "location": {"x": 1, "y": 2, "yaw": 0, "level_name": "L1"}}
    dto = normalize_robot_state(robot, "f", stale=True)
    assert dto["stale"] is True
