"""Tests for building_region, registry-model resolve, graph_export (edge Material), POST /optimize/scene."""
import copy
import json
import math

import pytest
from fastapi.testclient import TestClient

from ..building_region import load_scene_file, parse_building_region
from ..graph_export import placements_to_graph
from ..models import Lab, WallObstacle
from ..server import app

client = TestClient(app)

# edge Material（labortest.json）节点字段
_MATERIAL_KEYS = {
    "uuid", "parent_uuid", "id", "name", "type", "class", "parent",
    "pose", "config", "data", "schema", "description", "model", "position",
}
_POSE_KEYS = {
    "layout", "position", "position_3d", "size", "scale", "rotation",
    "extra", "cross_section_type",
}


# ---------- building_region ----------


def _building_scene():
    """两面墙围出的简单 building（米）。"""
    return {
        "nodes": {
            "site_x": {"id": "site_x", "type": "site", "parentId": None,
                       "polygon": {"type": "polygon", "points": [[-1, -1], [11, 9]]}, "children": []},
            "wall_a": {"id": "wall_a", "type": "wall", "parentId": "level",
                       "start": [0, 0], "end": [10, 0], "thickness": 0.2, "height": 2.7},
            "wall_b": {"id": "wall_b", "type": "wall", "parentId": "level",
                       "start": [0, 0], "end": [0, 8], "thickness": 0.2, "height": 2.7},
        },
        "rootNodeIds": ["site_x"],
    }


def test_parse_building_region_bbox_and_walls():
    region = parse_building_region(_building_scene())
    assert region is not None
    origin, width, depth, walls = region
    # 墙端点包围盒：x[0,10], y[0,8]
    assert origin == (0.0, 0.0)
    assert width == pytest.approx(10.0)
    assert depth == pytest.approx(8.0)
    assert len(walls) == 2
    assert all(isinstance(w, WallObstacle) for w in walls)
    wa = walls[0]
    assert wa.length == pytest.approx(10.0)
    assert wa.thickness == pytest.approx(0.2)


def test_parse_building_region_translates_to_local_frame():
    """墙坐标带偏移时，区域原点平移到 (0,0)，墙中心也平移到局部帧。"""
    scene = {
        "nodes": {
            "w": {"type": "wall", "start": [100, 50], "end": [110, 50], "thickness": 0.3},
        },
        "rootNodeIds": [],
    }
    origin, width, depth, walls = parse_building_region(scene)
    assert origin == (100.0, 50.0)
    assert width == pytest.approx(10.0)
    assert walls[0].cx == pytest.approx(5.0)  # 105 - 100
    assert walls[0].cy == pytest.approx(0.0)  # 50 - 50


def test_parse_building_region_empty_returns_none():
    assert parse_building_region({"nodes": {}, "rootNodeIds": []}) is None
    assert parse_building_region(None) is None


def test_load_scene_file_roundtrip(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps(_building_scene()), encoding="utf-8")
    scene = load_scene_file(str(p))
    assert "wall_a" in scene["nodes"]


def test_load_scene_file_missing_raises():
    with pytest.raises(ValueError):
        load_scene_file("no/such/file.json")


# ---------- 避墙惩罚（constraints + Lab.wall_obstacles） ----------


def test_wall_obstacle_penalty_in_cost():
    from ..constraints import evaluate_default_hard_constraints
    from ..mock_checkers import MockCollisionChecker
    from ..models import Device, Placement

    dev = Device(id="d", name="d", bbox=(1.0, 1.0))
    lab = Lab(
        width=10.0, depth=10.0,
        wall_obstacles=[WallObstacle(cx=5.0, cy=5.0, length=4.0, thickness=0.4, yaw=0.0)],
    )
    checker = MockCollisionChecker()
    # 设备压在墙中心 -> 穿透惩罚 > 0
    on_wall = [Placement(device_id="d", x=5.0, y=5.0, theta=0.0)]
    cost_on = evaluate_default_hard_constraints([dev], on_wall, lab, checker)
    # 设备远离墙 -> 无墙惩罚
    off_wall = [Placement(device_id="d", x=1.0, y=1.0, theta=0.0)]
    cost_off = evaluate_default_hard_constraints([dev], off_wall, lab, checker)
    assert cost_on > cost_off
    # binary 模式压墙 -> inf
    cost_bin = evaluate_default_hard_constraints([dev], on_wall, lab, checker, graduated=False)
    assert math.isinf(cost_bin)


# ---------- registry model 解析（mesh/path/type/format） ----------


def test_load_registry_models_indexes(tmp_path):
    from ..device_catalog import load_registry_models

    devdir = tmp_path / "devices"
    devdir.mkdir()
    (devdir / "x.yaml").write_text(
        "asset_model.foo_dev:\n"
        "  model:\n"
        "    mesh: foo_dev\n"
        "    path: https://oss/uni-lab/devices/foo_dev/macro_device.xacro\n"
        "    type: device\n",
        encoding="utf-8",
    )
    idx = load_registry_models(str(tmp_path))
    for k in ("asset_model.foo_dev", "foo_dev"):
        assert idx[k]["path"].endswith("foo_dev/macro_device.xacro")
        assert idx[k]["mesh"] == "foo_dev"
        assert idx[k]["type"] == "device"
        assert idx[k]["format"] == "xacro"


# ---------- placements_to_graph（edge Material graph） ----------


def _sample_placed():
    return [
        {
            "id": "robotic_arm.SCARA_with_slider.moveit.virtual",
            "uuid": "uuid-a",
            "display_name": "Arm",
            "model": {"mesh": "arm_slider", "path": "p", "type": "device", "format": "xacro"},
            "x": 1.2, "y": 0.5, "z": 0.0, "theta": math.pi / 2,
        },
        {
            "id": "robotic_arm.SCARA_with_slider.moveit.virtual",
            "uuid": "uuid-b",
            "x": 2.0, "y": 1.0, "z": 0.0, "theta": 0.0,
        },
    ]


def test_placements_to_graph_format():
    graph = placements_to_graph(_sample_placed())
    assert set(graph.keys()) == {"nodes", "edges"}
    assert graph["edges"] == []
    assert isinstance(graph["nodes"], list) and len(graph["nodes"]) == 2

    node = graph["nodes"][0]
    assert set(node.keys()) == _MATERIAL_KEYS
    assert node["uuid"] == "uuid-a"
    assert node["type"] == "device"
    assert node["class"] == "robotic_arm.SCARA_with_slider.moveit.virtual"
    # id 规整：点 -> 下划线
    assert node["id"] == "robotic_arm_SCARA_with_slider_moveit_virtual"
    assert node["name"] == "Arm"
    assert node["parent_uuid"] == "" and node["parent"] == ""
    assert node["schema"] == {} and node["description"] == ""
    # model 为固定 4 键结构（mesh/path/type/format）
    assert node["model"] == {
        "mesh": "arm_slider",
        "path": "p",
        "type": "device",
        "format": "xacro",
    }
    # pose 完整对象；position 毫米；rotation 弧度
    assert set(node["pose"].keys()) == _POSE_KEYS
    assert node["pose"]["position"] == {"x": 1200.0, "y": 500.0, "z": 0.0}
    assert node["pose"]["position_3d"] == {"x": 1200.0, "y": 500.0, "z": 0.0}
    assert node["position"] == {"x": 1200.0, "y": 500.0, "z": 0.0}
    assert node["pose"]["rotation"]["z"] == pytest.approx(math.pi / 2)
    assert node["pose"]["scale"] == {"x": 1.0, "y": 1.0, "z": 1.0}
    assert node["pose"]["extra"] == {"parent_link": "", "mount_point": ""}
    assert node["pose"]["layout"] == "x-y"

    # 未传 model 的节点也必须补齐固定结构（不能是 {}）
    node_2 = graph["nodes"][1]
    assert node_2["model"] == {
        "mesh": "robotic_arm.SCARA_with_slider.moveit.virtual",
        "path": "",
        "type": "device",
        "format": "xacro",
    }


def test_placements_to_graph_dup_id_suffix():
    """同 class 多实例：id 追加数字后缀（serial/serial1 模式），uuid 仍唯一。"""
    graph = placements_to_graph(_sample_placed())
    ids = [n["id"] for n in graph["nodes"]]
    assert ids == [
        "robotic_arm_SCARA_with_slider_moveit_virtual",
        "robotic_arm_SCARA_with_slider_moveit_virtual1",
    ]
    assert len({n["uuid"] for n in graph["nodes"]}) == 2


def test_placements_to_graph_mount_extra_and_parent_backfill():
    graph = placements_to_graph(
        [
            {"id": "parent_cls", "uuid": "p-1", "x": 0.0, "y": 0.0, "z": 0.0, "theta": 0.0},
            {
                "id": "child_cls",
                "uuid": "c-1",
                "parent_uuid": "p-1",
                "extra": {"parent_link": "p1_tool0", "mount_point": "tool0"},
                "x": 1.0,
                "y": 2.0,
                "z": 0.0,
                "theta": 0.0,
            },
        ]
    )
    by_uuid = {n["uuid"]: n for n in graph["nodes"]}
    child = by_uuid["c-1"]
    assert child["parent_uuid"] == "p-1"
    # 未显式给 parent 时，按 parent_uuid 回填 parent 的 class
    assert child["parent"] == "parent_cls"
    assert child["pose"]["extra"] == {"parent_link": "p1_tool0", "mount_point": "tool0"}


# ---------- POST /optimize/scene ----------


_FAKE_MODEL = {
    "mesh": "dev_a",
    "path": "https://oss/uni-lab/devices/dev_a/macro_device.xacro",
    "type": "device", "format": "xacro",
}


def _install_fakes(monkeypatch, reg_model=_FAKE_MODEL):
    captured = {}

    class _FakeClient:
        remote_addr = "http://fake/api/v1"
        auth = "fake-auth"

        def __init__(self, *a, **k):
            pass

        def material_add(self, nodes, mount_uuid, first_add=True):
            captured["nodes"] = nodes
            captured["mount_uuid"] = mount_uuid
            return {n["uuid"]: f"cloud-{n['uuid']}" for n in nodes}

    try:
        import unilabos.app.web.client as client_mod
        import unilabos.layout_optimizer.device_catalog as dc
    except Exception:  # pragma: no cover
        pytest.skip("unilabos 依赖不可用")
    monkeypatch.setattr(client_mod, "HTTPClient", _FakeClient)
    monkeypatch.setattr(dc, "resolve_registry_model",
                        lambda t: copy.deepcopy(reg_model) if reg_model else None)
    monkeypatch.setenv("LAYOUT_SKIP_CLOUD_PREFLIGHT", "1")
    return captured


def _scene_body(**overrides):
    body = {
        "devices": [{"type": "dev_a", "count": 2}],
        "scene": _building_scene(),
        "run_de": False,
        "mount_uuid": "mount-1",
        "save_local": False,
    }
    body.update(overrides)
    return body


def test_optimize_scene_one_shot(monkeypatch):
    captured = _install_fakes(monkeypatch)
    resp = client.post("/optimize/scene", json=_scene_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["uploaded"] is True
    graph = data["graph"]
    # edge Material graph：nodes 列表 + edges:[]，仅设备无 building
    assert graph["edges"] == []
    assert all(n["type"] == "device" for n in graph["nodes"])
    assert len(graph["nodes"]) == 2
    assert all(set(n.keys()) == _MATERIAL_KEYS for n in graph["nodes"])
    # 自动补全 model（来自注册表 mock）
    node = graph["nodes"][0]
    assert node["model"]["mesh"] == "dev_a"
    assert set(node["model"].keys()) == {"mesh", "path", "type", "format"}
    assert node["pose"]["extra"] == {"parent_link": "", "mount_point": ""}
    assert node["name"] == "dev_a"
    # 上传走 fake material_add：直接是 graph["nodes"]
    assert captured["mount_uuid"] == "mount-1"
    assert captured["nodes"] == graph["nodes"]
    assert all(isinstance(m["pose"]["position"], dict) for m in captured["nodes"])
    assert len(data["cloud_uuid_mapping"]) == 2


def test_optimize_scene_count_and_unique_uuid(monkeypatch):
    _install_fakes(monkeypatch)
    resp = client.post("/optimize/scene", json=_scene_body(devices=[{"type": "dev_a", "count": 3}]))
    assert resp.status_code == 200
    nodes = resp.json()["graph"]["nodes"]
    assert len(nodes) == 3
    assert len({n["uuid"] for n in nodes}) == 3                  # 逐实例唯一
    assert {n["id"] for n in nodes} == {"dev_a", "dev_a1", "dev_a2"}  # id 加后缀
    assert {n["class"] for n in nodes} == {"dev_a"}             # class 同 type


def test_optimize_scene_mount_fields_passthrough(monkeypatch):
    _install_fakes(monkeypatch)
    resp = client.post(
        "/optimize/scene",
        json=_scene_body(
            devices=[
                {
                    "type": "dev_a",
                    "count": 1,
                    "parentUuid": "parent-xyz",
                    "parent": "parent_cls",
                    "parentLink": "parent_tool0",
                    "mountPoint": "tool0",
                }
            ]
        ),
    )
    assert resp.status_code == 200
    node = resp.json()["graph"]["nodes"][0]
    assert node["parent_uuid"] == "parent-xyz"
    assert node["parent"] == "parent_cls"
    assert node["pose"]["extra"] == {
        "parent_link": "parent_tool0",
        "mount_point": "tool0",
    }


def test_optimize_scene_save_local_before_upload(monkeypatch, tmp_path):
    captured = _install_fakes(monkeypatch)
    save_path = tmp_path / "layout_graph.json"
    resp = client.post(
        "/optimize/scene",
        json=_scene_body(
            devices=[{"type": "dev_a", "count": 1}],
            saveLocal=True,
            outputPath=str(save_path),
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved_local"] is True
    assert data["local_graph_path"] == str(save_path)
    assert save_path.exists()
    saved = json.loads(save_path.read_text(encoding="utf-8"))
    assert saved == data["graph"]
    # 上传确实放在本地保存之后执行（同一份 graph 被上传）
    assert captured["nodes"] == saved["nodes"]


def test_optimize_scene_devices_placed_in_building_region(monkeypatch):
    """设备位置在 building 区域内（毫米；区域 10m×8m -> 0..10000, 0..8000）。"""
    _install_fakes(monkeypatch)
    resp = client.post("/optimize/scene", json=_scene_body(devices=[{"type": "dev_a", "count": 1}]))
    assert resp.status_code == 200
    node = resp.json()["graph"]["nodes"][0]
    pos = node["pose"]["position"]
    assert -1.0 <= pos["x"] <= 10000.0
    assert -1.0 <= pos["y"] <= 8000.0


def test_optimize_scene_bad_scene_path_returns_400(monkeypatch):
    _install_fakes(monkeypatch)
    # scene_path 优先于 scene；指向不存在文件 -> 400
    resp = client.post("/optimize/scene", json=_scene_body(scene_path="no/such.json"))
    assert resp.status_code == 400
    assert "场景文件" in resp.json()["detail"]


def test_optimize_scene_missing_mount_uuid_uses_root_mount(monkeypatch):
    captured = _install_fakes(monkeypatch)
    monkeypatch.delenv("LAYOUT_MOUNT_UUID", raising=False)
    resp = client.post("/optimize/scene", json=_scene_body(mount_uuid=""))
    assert resp.status_code == 200
    assert resp.json()["uploaded"] is True
    assert captured["mount_uuid"] == ""


def test_optimize_scene_missing_cloud_config_returns_400(monkeypatch):
    import unilabos.app.web.client as client_mod
    import unilabos.layout_optimizer.device_catalog as dc

    class _NoAuth:
        remote_addr = "http://fake/api/v1"
        auth = ""

        def __init__(self, *a, **k):
            pass

        def material_add(self, *a, **k):  # pragma: no cover
            raise AssertionError("should not upload")

    monkeypatch.setattr(client_mod, "HTTPClient", _NoAuth)
    monkeypatch.setattr(dc, "resolve_registry_model", lambda t: None)
    resp = client.post("/optimize/scene", json=_scene_body())
    assert resp.status_code == 400
    assert "云端配置" in resp.json()["detail"]
