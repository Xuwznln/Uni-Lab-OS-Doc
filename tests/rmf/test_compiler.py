"""Pascal scene → building.yaml 编译器单测（#18 §5.1 / #17 Phase 2）。"""

from unilabos.sim.fleet.rmf.compiler import compile_scene


def _scene():
    return {
        "nodes": [
            {
                "id": "chg",
                "name": "charger_01",
                "uuid": "u-chg",
                "position": {"x": 1232421, "y": 658567, "z": 0},
                "data": {"rmf": {"workcellType": "charger", "placeId": "charger_01", "enabled": True}},
            },
            {
                "id": "pan",
                "name": "pantry",
                "uuid": "u-pan",
                "position": {"x": 1990000, "y": 638364, "z": 0},
                "data": {"rmf": {"workcellType": "dispenser", "placeId": "pantry",
                                  "pickupWaypoint": "coke_dispenser", "enabled": True}},
            },
        ]
    }


def _robots():
    return [{
        "robot_name": "agv_sim_01",
        "fleet_name": "unilab_agv",
        "kind": "sim",
        "charger_waypoint": "charger_01",
        "initial_waypoint": "charger_01",
    }]


def test_compile_building_structure():
    ir, building, semantic = compile_scene(_scene(), _robots(), lab_uuid="lab1", scene_hash="h1")
    assert building["coordinate_system"] == "cartesian_meters"
    level = building["levels"]["L1"]
    names = {row[3] for row in level["vertices"]}
    assert {"charger_01", "pantry"} <= names


def test_param_type_encoding_and_coords():
    _, building, _ = compile_scene(_scene(), _robots(), lab_uuid="lab1", scene_hash="h1")
    vertices = building["levels"]["L1"]["vertices"]
    charger = next(row for row in vertices if row[3] == "charger_01")
    x, y = charger[0], charger[1]
    assert round(x, 3) == 1232.421
    assert round(y, 3) == -658.567  # Y 翻转
    params = charger[4]
    assert params["is_charger"] == [4, True]            # bool → type_code 4
    assert params["spawn_robot_name"] == [1, "agv_sim_01"]  # str → type_code 1


def test_semantic_map_collects_chargers_and_pickups():
    _, _, semantic = compile_scene(_scene(), _robots(), lab_uuid="lab1", scene_hash="h1")
    assert semantic["chargers"] == ["charger_01"]
    assert semantic["pickups"] == ["pantry"]
    assert semantic["waypoint_device_uuid"]["charger_01"] == "u-chg"


def test_missing_charger_is_error():
    robots = [{"robot_name": "agv_x", "fleet_name": "f", "kind": "sim", "charger_waypoint": "nope"}]
    ir, _, _ = compile_scene(_scene(), robots, lab_uuid="lab1", scene_hash="h1")
    codes = {d["code"] for d in ir.diagnostics_as_dicts()}
    assert "charger_not_found" in codes
    assert ir.has_errors()


def test_real_robot_missing_target_map_is_error():
    robots = [{"robot_name": "seer", "fleet_name": "f", "kind": "real", "charger_waypoint": "charger_01"}]
    ir, _, _ = compile_scene(_scene(), robots, lab_uuid="lab1", scene_hash="h1")
    codes = {d["code"] for d in ir.diagnostics_as_dicts()}
    assert "missing_target_map" in codes
