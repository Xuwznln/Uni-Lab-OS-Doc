"""``unilab -g`` 启动分派：权威拉取、缓存落盘与创建入口的对账/发号。"""

from __future__ import annotations

import json

import pytest

from unilabos.app.main import (
    _materialize_graph_from_authority,
    _read_graph_json,
    _register_graph_file_to_authority,
)
from unilabos.server.backend.legacy_adaptor.legacy.startup import (
    upgrade_startup_graph_payload,
)
from unilabos.server.database.layout import ServerDatabasePaths
from unilabos.server.services.materials.graph import GraphError, GraphService

PAYLOAD = {"nodes": [{"id": "pump"}], "links": []}


@pytest.fixture()
def authority(tmp_path):
    paths = ServerDatabasePaths.resolve(tmp_path)
    service = GraphService(paths.materials_db)
    try:
        yield service, paths
    finally:
        service.close()


class TestMaterializeFromAuthority:
    def test_by_name_and_uuid_share_one_cache_file(self, authority, tmp_path) -> None:
        service, paths = authority
        record = service.upsert_graph(name="lan", payload=PAYLOAD)
        args_dict = {"server_database_root": str(tmp_path)}

        by_name = _materialize_graph_from_authority("lan", args_dict, str(tmp_path))
        assert by_name is not None
        with open(by_name, encoding="utf-8") as stream:
            cached = json.load(stream)
        # upsert 是创建入口：缓存的是发号后的权威 payload。
        assert [n["id"] for n in cached["nodes"]] == ["pump"]
        assert cached["nodes"][0]["uuid"]
        assert by_name.endswith(f"{record['uuid']}.json")

        by_uuid = _materialize_graph_from_authority(
            record["uuid"], args_dict, str(tmp_path)
        )
        assert by_uuid == by_name

    def test_unknown_identity_returns_none(self, authority, tmp_path) -> None:
        args_dict = {"server_database_root": str(tmp_path)}
        assert (
            _materialize_graph_from_authority("nope", args_dict, str(tmp_path)) is None
        )

    def test_missing_database_returns_none(self, tmp_path) -> None:
        args_dict = {"server_database_root": str(tmp_path / "empty")}
        assert (
            _materialize_graph_from_authority(
                "lan", args_dict, str(tmp_path / "empty")
            )
            is None
        )


class TestUpsertReconcile:
    """upsert 即创建入口：草稿发号、身份复用、冲突拒绝与 diff 摘要。"""

    def test_draft_nodes_get_stable_identity_and_summary(self, authority) -> None:
        service, _paths = authority
        draft = {"nodes": [{"id": "pump"}, {"id": "rack"}], "links": []}

        first = service.upsert_graph(name="lab", payload=draft)
        assert first["revision"] == 1
        assert sorted(first["summary"]["created"]) == ["pump", "rack"]
        assert first["summary"]["uuid_assigned"] == 2
        assigned = {n["id"]: n["uuid"] for n in first["payload"]["nodes"]}
        assert all(assigned.values())

        # 同一草稿重复导入：发号稳定、内容一致、revision 不变。
        second = service.upsert_graph(name="lab", payload=draft)
        assert second["revision"] == 1
        assert sorted(second["summary"]["unchanged"]) == ["pump", "rack"]
        assert {n["id"]: n["uuid"] for n in second["payload"]["nodes"]} == assigned

        # 新增 + 移除节点的 diff 摘要。
        third = service.upsert_graph(
            name="lab", payload={"nodes": [{"id": "pump"}, {"id": "valve"}]}
        )
        assert third["revision"] == 2
        assert third["summary"]["created"] == ["valve"]
        assert third["summary"]["removed"] == ["rack"]
        assert third["summary"]["unchanged"] == ["pump"]
        assert {n["id"]: n["uuid"] for n in third["payload"]["nodes"]}["pump"] == assigned["pump"]

    def test_identity_conflict_is_rejected(self, authority) -> None:
        service, _paths = authority
        service.upsert_graph(name="lab", payload={"nodes": [{"id": "pump"}]})

        with pytest.raises(GraphError) as excinfo:
            service.upsert_graph(
                name="lab",
                payload={"nodes": [{"id": "pump", "uuid": "11111111-1111-1111-1111-111111111111"}]},
            )
        assert excinfo.value.code == "identity_conflict"

    def test_duplicate_node_id_is_rejected(self, authority) -> None:
        service, _paths = authority
        with pytest.raises(GraphError) as excinfo:
            service.upsert_graph(
                name="lab", payload={"nodes": [{"id": "pump"}, {"id": "pump"}]}
            )
        assert excinfo.value.code == "invalid_payload"

    def test_plr_flat_config_sites_become_canonical(self, authority) -> None:
        service, _paths = authority
        draft = {
            "nodes": [
                {
                    "id": "deck1",
                    "type": "deck",
                    "config": {
                        "type": "DemoDeck",
                        "sites": [
                            {
                                "label": "T1",
                                "position": {"x": 1, "y": 2, "z": 0},
                                "size": {"width": 10, "height": 20, "depth": 0},
                                "content_type": ["plate"],
                            }
                        ],
                    },
                }
            ]
        }
        stored = service.upsert_graph(name="lab", payload=draft)
        node = stored["payload"]["nodes"][0]
        assert "sites" not in node["config"]
        assert node["template_name"] == "DemoDeck"
        assert node["sites_initialized"] is True
        site = node["sites"][0]
        assert site["uuid"] and site["material_uuid"] == node["uuid"]
        assert site["template_name"] == "DemoDeck"
        assert site["allowed_resource_categories"] == ["plate"]
        assert site["pose"]["position3d"] == {"x": 1.0, "y": 2.0, "z": 0.0}

        # 重复导入：Site 身份同样稳定。
        again = service.upsert_graph(name="lab", payload=draft)
        assert again["revision"] == stored["revision"]
        assert again["payload"]["nodes"][0]["sites"][0]["uuid"] == site["uuid"]

    def test_authority_does_not_upgrade_legacy_fields(self, authority) -> None:
        """微后端只认当前契约：class 不回填 template_name，根级 position 直接拒绝。"""
        service, _paths = authority
        stored = service.upsert_graph(
            name="lab",
            payload={"nodes": [{"id": "pump", "type": "device", "class": "pump_demo"}]},
        )
        node = stored["payload"]["nodes"][0]
        assert node["class"] == "pump_demo"
        assert "template_name" not in node

        with pytest.raises(GraphError) as excinfo:
            service.upsert_graph(
                name="lab2",
                payload={"nodes": [{"id": "pump", "position": {"x": 1, "y": 2, "z": 0}}]},
            )
        assert excinfo.value.code == "invalid_payload"
        assert "根字段 position" in excinfo.value.message

    def test_children_lists_are_stripped(self, authority) -> None:
        """层级只由 parent 表达：派生的 children 列表不入库。"""
        service, _paths = authority
        stored = service.upsert_graph(
            name="lab",
            payload={
                "nodes": [
                    {"id": "station", "children": ["pump"]},
                    {"id": "pump", "parent": "station"},
                ]
            },
        )
        nodes = {node["id"]: node for node in stored["payload"]["nodes"]}
        assert "children" not in nodes["station"]
        assert nodes["pump"]["parent"] == "station"

    def test_sites_without_template_hint_rejected(self, authority) -> None:
        service, _paths = authority
        with pytest.raises(GraphError) as excinfo:
            service.upsert_graph(
                name="lab",
                payload={"nodes": [{"id": "deck1", "sites": [{"label": "T1"}]}]},
            )
        assert excinfo.value.code == "invalid_payload"


class TestRegisterGraphFileToAuthority:
    def test_draft_file_registered_and_cached_with_identity(
        self, authority, tmp_path
    ) -> None:
        service, paths = authority
        graph_file = tmp_path / "my_lab.json"
        graph_file.write_text(json.dumps(PAYLOAD), encoding="utf-8")
        args_dict = {"server_database_root": str(tmp_path)}

        cache_path = _register_graph_file_to_authority(
            str(graph_file), args_dict, str(tmp_path)
        )
        assert cache_path is not None
        with open(cache_path, encoding="utf-8") as stream:
            cached = json.load(stream)
        assert all(node.get("uuid") for node in cached["nodes"])
        assert service.get_graph("my_lab")["revision"] == 1

        # 再次启动同一文件：登记幂等，revision 不变。
        second = _register_graph_file_to_authority(
            str(graph_file), args_dict, str(tmp_path)
        )
        assert second == cache_path
        assert service.get_graph("my_lab")["revision"] == 1

    def test_legacy_file_is_converted_at_reception(
        self, authority, tmp_path, capsys
    ) -> None:
        """旧格式启动图在读取边界转成当前契约；Graph Authority 只收到当前契约。"""
        service, _paths = authority
        legacy = {
            "nodes": [
                {
                    "id": "host_node",
                    "type": "device",
                    "class": "host_node",
                    "children": ["pump"],
                    "position": {"x": 1, "y": 2, "z": 0},
                    "pose": {"position_3d": {"x": 1, "y": 2, "z": 0}},
                },
                {"id": "pump", "type": "device", "class": "pump_demo", "parent": "host_node"},
            ],
            "edges": [
                {"source": "host_node", "target": "pump", "source_handle": "out", "target_handle": "in"}
            ],
        }
        graph_file = tmp_path / "old_lab.json"
        graph_file.write_text(json.dumps(legacy), encoding="utf-8")
        args_dict = {"server_database_root": str(tmp_path)}

        payload = upgrade_startup_graph_payload(
            _read_graph_json(str(graph_file)), str(graph_file)
        )
        assert "使用旧格式图字段" in capsys.readouterr().out
        cache_path = _register_graph_file_to_authority(
            str(graph_file), args_dict, str(tmp_path), payload
        )
        assert cache_path is not None
        with open(cache_path, encoding="utf-8") as stream:
            cached = json.load(stream)
        host = next(n for n in cached["nodes"] if n["id"] == "host_node")
        assert host["template_name"] == "host_node"
        assert "position" not in host and "children" not in host
        assert host["pose"]["position"] == {"x": 1, "y": 2, "z": 0}
        assert host["pose"]["position3d"] == {"x": 1, "y": 2, "z": 0}
        assert cached["links"][0]["sourceHandle"] == "out"
        assert "source_handle" not in cached["links"][0]
        assert service.get_graph("old_lab")["revision"] == 1
