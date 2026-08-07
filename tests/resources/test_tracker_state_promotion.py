"""ResourceDict 提升字段的契约测试（barcode / sites / VolumeTracker 范式）。

覆盖：漏斗提升（get_resource_instance_from_dict）、根字段优先、None/空值语义、
各回装出口、sites UUID，以及
「PLR → ResourceTreeSet → JSON（模拟 TCP/HTTP 传输）→ ResourceTreeSet → PLR」
的 tracker / standard Carrier / ItemizedCarrier / PRCXI round-trip。
"""

import json

import pytest

from unilabos.resources.resource_tracker import (
    PLR_CONFIG_ROOT_KEYS,
    RESOURCE_ROOT_FIELDS,
    TRACKER_STATE_KEYS,
    ResourceDict,
    ResourceDictInstance,
    ResourceTreeSet,
    assemble_tracker_state,
)


def make_content(data=None, **overrides):
    content = {
        "id": "beaker_1",
        "name": "beaker_1",
        "type": "container",
        "class": "",
        "config": {"size_x": 10, "size_y": 10, "size_z": 20},
        "data": data if data is not None else {},
        "extra": {},
        "position": {"x": 0, "y": 0, "z": 0},
    }
    content.update(overrides)
    return content


TRACKER_STATE = {
    "thing": "beaker_1",
    "max_volume": 100.0,
    "liquids": [["water", 30.0], ["Unknown1", 10.0]],
    "liquid_history": [["water", 50.0], ["Unknown1", 10.0], [None, -20.0]],
    "unknown_counter": 1,
}

SITES = [
    {
        "uuid": "site-uuid-a1",
        "index": 3,
        "label": "A1",
        "visible": True,
        "occupied_by": None,
        "position": {"x": 1.0, "y": 2.0, "z": 3.0},
        "size": {"width": 10.0, "height": 11.0, "depth": 12.0},
        "content_type": ["container"],
    }
]


class TestFunnelPromotion:
    def test_promotes_sites_from_config_to_root(self):
        res = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(config={"size_x": 10, "sites": json.loads(json.dumps(SITES))})
        ).res_content
        assert res.sites == SITES
        assert "sites" not in res.config

    def test_root_sites_win_over_config(self):
        root_sites = [{**SITES[0], "uuid": "root-site-uuid"}]
        res = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(config={"sites": SITES}, sites=root_sites)
        ).res_content
        assert res.sites == root_sites
        assert "sites" not in res.config

    def test_empty_sites_is_distinct_from_no_sites(self):
        empty = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(config={"sites": SITES}, sites=[])
        ).res_content
        absent = ResourceDictInstance.get_resource_instance_from_dict(make_content()).res_content
        assert empty.sites == []
        assert absent.sites is None

    def test_legacy_site_without_uuid_gets_stable_identity_on_dump(self):
        legacy_site = {key: value for key, value in SITES[0].items() if key not in {"uuid", "index"}}
        first = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(config={"sites": [legacy_site]})
        ).res_content
        assert first.sites is not None
        assert first.sites[0]["uuid"]
        assert first.sites[0]["index"] == 0

        dumped = json.loads(json.dumps(first.model_dump(by_alias=True)))
        second = ResourceDictInstance.get_resource_instance_from_dict(dumped).res_content
        assert second.sites == first.sites

    def test_promotes_tracker_state_to_root(self):
        instance = ResourceDictInstance.get_resource_instance_from_dict(make_content(data=dict(TRACKER_STATE)))
        res = instance.res_content
        assert res.liquids == [["water", 30.0], ["Unknown1", 10.0]]
        assert res.liquid_history == [["water", 50.0], ["Unknown1", 10.0], [None, -20.0]]
        assert res.unknown_counter == 1
        # 物质面三键移出 data；规格面/冗余键留在 data
        assert res.data == {"thing": "beaker_1", "max_volume": 100.0}

    def test_root_fields_win_over_data(self):
        content = make_content(
            data=dict(TRACKER_STATE),
            liquids=[["ethanol", 5.0]],
            liquid_history=[["ethanol", 5.0]],
            unknown_counter=3,
        )
        res = ResourceDictInstance.get_resource_instance_from_dict(content).res_content
        assert res.liquids == [["ethanol", 5.0]]
        assert res.liquid_history == [["ethanol", 5.0]]
        assert res.unknown_counter == 3
        assert "liquids" not in res.data and "liquid_history" not in res.data

    def test_empty_container_state_is_promoted_not_none(self):
        # 空容器的 []/0 与「非容器」的 None 必须可区分
        data = {"thing": "b", "max_volume": 50.0, "liquids": [], "liquid_history": [], "unknown_counter": 0}
        res = ResourceDictInstance.get_resource_instance_from_dict(make_content(data=data)).res_content
        assert res.liquids == []
        assert res.liquid_history == []
        assert res.unknown_counter == 0

    def test_non_container_keeps_none(self):
        # tip 状态等其他 serialize_state 形态不含液体三键，不受提升影响
        res = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(data={"tip_state": {"has_tip": True}}, type="tip_spot")
        ).res_content
        assert res.liquids is None
        assert res.liquid_history is None
        assert res.unknown_counter is None
        assert res.data == {"tip_state": {"has_tip": True}}

    def test_promotion_is_idempotent_over_dump(self):
        # dump（剥离形态）再走一遍漏斗，结果不变——TCP/HTTP 传输后重建的路径
        first = ResourceDictInstance.get_resource_instance_from_dict(make_content(data=dict(TRACKER_STATE)))
        dumped = json.loads(json.dumps(first.res_content.model_dump(by_alias=True)))
        second = ResourceDictInstance.get_resource_instance_from_dict(dumped).res_content
        assert second.liquids == first.res_content.liquids
        assert second.liquid_history == first.res_content.liquid_history
        assert second.unknown_counter == first.res_content.unknown_counter
        assert second.data == first.res_content.data

    def test_site_promotion_is_idempotent_over_dump(self):
        first = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(config={"sites": json.loads(json.dumps(SITES))})
        )
        dumped = json.loads(json.dumps(first.res_content.model_dump(by_alias=True)))
        second = ResourceDictInstance.get_resource_instance_from_dict(dumped).res_content
        assert second.sites == first.res_content.sites
        assert "sites" not in second.config


class TestAssemble:
    def test_assemble_restores_full_state(self):
        res = ResourceDictInstance.get_resource_instance_from_dict(make_content(data=dict(TRACKER_STATE))).res_content
        assembled = assemble_tracker_state(res)
        assert assembled == {
            "thing": "beaker_1",
            "max_volume": 100.0,
            "liquids": [["water", 30.0], ["Unknown1", 10.0]],
            "liquid_history": [["water", 50.0], ["Unknown1", 10.0], [None, -20.0]],
            "unknown_counter": 1,
        }

    def test_assemble_skips_none_roots(self):
        res = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(data={"tip_state": {"has_tip": False}}, type="tip_spot")
        ).res_content
        assert assemble_tracker_state(res) == {"tip_state": {"has_tip": False}}

    def test_nested_dict_reassembles_data_without_root_keys(self):
        instance = ResourceDictInstance.get_resource_instance_from_dict(make_content(data=dict(TRACKER_STATE)))
        nested = instance.get_plr_nested_dict()
        assert nested["data"]["liquids"] == [["water", 30.0], ["Unknown1", 10.0]]
        assert nested["data"]["liquid_history"] == [["water", 50.0], ["Unknown1", 10.0], [None, -20.0]]
        assert nested["data"]["unknown_counter"] == 1
        for state_key in TRACKER_STATE_KEYS:
            assert state_key not in nested  # 根键不保留在 PLR 嵌套形态

    def test_nested_dict_keeps_sites_at_plr_root(self):
        instance = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(config={"sites": json.loads(json.dumps(SITES))})
        )
        nested = instance.get_plr_nested_dict()
        assert nested["sites"] == SITES
        assert "sites" not in nested["config"]


class TestRootFieldContract:
    """根字段守护契约：新增 ResourceDict 根字段时本组用例兜底各点位不漂移。

    规则全文见 AGENTS.md「ResourceDict 根字段（提升字段）新增守则」。
    """

    def test_whitelist_is_derived_from_model(self):
        # 派生常量必须覆盖 ResourceDict 全部根级键（含别名 schema/class）；
        # TRACKER_STATE_KEYS 等提升家族必须是模型字段的子集（防改名漂移）
        expected = {
            field.serialization_alias or field.alias or field_name
            for field_name, field in ResourceDict.model_fields.items()
        }
        assert set(RESOURCE_ROOT_FIELDS) == expected
        assert {"schema", "class"} <= expected
        assert set(TRACKER_STATE_KEYS) <= expected
        assert set(PLR_CONFIG_ROOT_KEYS) <= expected

    def test_dump_form_never_leaks_root_keys_into_config(self):
        # dump 形态含全部模型键（新字段的默认值也在）：任何根键被标准化搬进 config
        # 即为白名单机制被破坏（如改回硬编码清单），此测试立即失败
        from unilabos.resources.graphio import canonicalize_nodes_data

        instance = ResourceDictInstance.get_resource_instance_from_dict(
            make_content(data=dict(TRACKER_STATE), barcode="BC-9", sites=SITES)
        )
        node = json.loads(json.dumps(instance.res_content.model_dump(by_alias=True)))
        tree_set = canonicalize_nodes_data([node])
        res = tree_set.trees[0].root_node.res_content
        leaked = set(res.config) & (set(RESOURCE_ROOT_FIELDS) - {"config"})
        assert not leaked, f"根键被搬进 config：{leaked}"
        assert res.liquids == TRACKER_STATE["liquids"]
        assert res.barcode == "BC-9"
        assert res.sites == SITES


class TestGraphWhitelist:
    def test_canonicalize_keeps_tracker_roots(self):
        # graphio 标准化白名单：根级液体三键不得被搬进 config（与 barcode 同为白名单成员）
        from unilabos.resources.graphio import canonicalize_nodes_data

        node = make_content(
            data={"thing": "beaker_1", "max_volume": 100.0},
            liquids=[["water", 30.0]],
            liquid_history=[["water", 30.0]],
            unknown_counter=0,
            barcode="BC-1",
            sites=SITES,
        )
        tree_set = canonicalize_nodes_data([node])
        res = tree_set.trees[0].root_node.res_content
        assert res.liquids == [["water", 30.0]]
        assert res.liquid_history == [["water", 30.0]]
        assert res.unknown_counter == 0
        assert res.barcode == "BC-1"
        assert res.sites == SITES
        assert "sites" not in res.config
        for state_key in TRACKER_STATE_KEYS:
            assert state_key not in res.config


class TestRosMsgConversion:
    def test_dump_form_roots_survive_resource_msg(self):
        # Resource msg 无根字段：dump 形态输入须把 barcode 归位 config、液体三键归位 data
        pytest.importorskip("unilabos_msgs")
        from unilabos.ros.msgs.message_converter import Resource, convert_from_ros_msg, convert_to_ros_msg

        dump_form = {
            "id": "beaker_1",
            "uuid": "uuid-1",
            "name": "beaker_1",
            "type": "container",
            "class": "",
            "position": {"x": 0, "y": 0, "z": 0},
            "config": {"size_x": 10},
            "data": {"thing": "beaker_1", "max_volume": 100.0},
            "liquids": [["water", 30.0]],
            "liquid_history": [["water", 50.0], [None, -20.0]],
            "unknown_counter": 0,
            "barcode": "BC-1",
            "barcode_symbology": "Code 128",
            "sites": SITES,
        }
        back = convert_from_ros_msg(convert_to_ros_msg(Resource, dump_form))
        assert back["data"]["liquids"] == [["water", 30.0]]
        assert back["data"]["liquid_history"] == [["water", 50.0], [None, -20.0]]
        assert back["data"]["unknown_counter"] == 0
        assert back["config"]["barcode"] == {
            "data": "BC-1",
            "symbology": "Code 128",
            "position_on_resource": "front",
        }
        assert back["config"]["sites"] == SITES
        # msg 回来的老形态再进漏斗 → 提升等值（msg 通路全链路无损）
        back.setdefault("extra", {})
        res = ResourceDictInstance.get_resource_instance_from_dict(back).res_content
        assert res.liquids == [["water", 30.0]]
        assert res.liquid_history == [["water", 50.0], [None, -20.0]]
        assert res.unknown_counter == 0
        assert res.barcode == "BC-1"
        assert res.sites == SITES
        assert "sites" not in res.config

    def test_legacy_form_untouched(self):
        # 老形态（data/config 自带完整状态）原样透传，不重复包装
        pytest.importorskip("unilabos_msgs")
        from unilabos.ros.msgs.message_converter import Resource, convert_from_ros_msg, convert_to_ros_msg

        legacy = {
            "id": "beaker_1",
            "uuid": "uuid-1",
            "name": "beaker_1",
            "type": "container",
            "position": {"x": 0, "y": 0, "z": 0},
            "config": {"barcode": {"data": "OLD", "symbology": "", "position_on_resource": "front"}},
            "data": {"liquids": [["oil", 1.0]], "liquid_history": [["oil", 1.0]], "unknown_counter": 0},
        }
        back = convert_from_ros_msg(convert_to_ros_msg(Resource, legacy))
        assert back["config"]["barcode"]["data"] == "OLD"
        assert back["data"]["liquids"] == [["oil", 1.0]]


class TestPlrRoundTrip:
    @pytest.fixture()
    def container(self):
        pytest.importorskip("pylabrobot")
        from unilabos.resources.container import RegularContainer

        c = RegularContainer(name="rt_beaker", size_x=10, size_y=10, size_z=20, max_volume=100.0)
        c.tracker.add_liquid("water", 50.0)
        c.tracker.add_liquid(None, 10.0)  # 产生 Unknown 名，unknown_counter+1
        c.tracker.remove_liquid(20.0)  # (None, -20) 按比例移除
        return c

    def test_from_plr_promotes_and_to_plr_restores(self, container):
        tree_set = ResourceTreeSet.from_plr_resources([container])
        root = tree_set.trees[0].root_node.res_content
        original_state = container.serialize_state()

        # 提升：物质面三键在根字段且与 PLR serialize_state 等值，data 只剩规格面/冗余
        assert json.loads(json.dumps(root.liquids)) == json.loads(json.dumps(original_state["liquids"]))
        assert json.loads(json.dumps(root.liquid_history)) == json.loads(json.dumps(original_state["liquid_history"]))
        assert root.unknown_counter == original_state["unknown_counter"]
        assert "liquids" not in root.data and "liquid_history" not in root.data

        # 模拟 HostLink/HTTP 传输：dump → JSON → from_raw_dict_list → to_plr
        wire = json.loads(json.dumps([n.res_content.model_dump(by_alias=True) for n in tree_set.all_nodes]))
        rebuilt_set = ResourceTreeSet.from_raw_dict_list(wire)
        restored = rebuilt_set.to_plr_resources(skip_devices=False)[0]
        restored_state = restored.serialize_state()

        assert json.loads(json.dumps(restored_state)) == json.loads(json.dumps(original_state))
        assert restored.tracker.volume == container.tracker.volume
        assert restored.tracker.current_liquids == container.tracker.current_liquids

    def test_standard_carrier_sites_uuid_and_index_round_trip(self):
        pytest.importorskip("pylabrobot")
        from pylabrobot.resources import Carrier, Coordinate, ResourceHolder

        site = ResourceHolder(name="carrier_site_A1", size_x=10, size_y=11, size_z=12)
        site.location = Coordinate(1, 2, 3)
        site.unilabos_uuid = "site-holder-uuid-a1"
        carrier = Carrier(name="carrier_rt", size_x=100, size_y=80, size_z=20, sites={3: site})
        carrier.unilabos_uuid = "carrier-uuid"

        tree_set = ResourceTreeSet.from_plr_resources([carrier])
        root = tree_set.trees[0].root_node.res_content
        assert root.sites is not None
        assert root.sites[0]["uuid"] == "site-holder-uuid-a1"
        assert root.sites[0]["index"] == 3
        assert root.sites[0]["label"] == "carrier_site_A1"
        assert "sites" not in root.config

        wire = json.loads(json.dumps([n.res_content.model_dump(by_alias=True) for n in tree_set.all_nodes]))
        restored = ResourceTreeSet.from_raw_dict_list(wire).to_plr_resources(skip_devices=False)[0]
        assert list(restored.sites) == [3]
        assert restored.sites[3].unilabos_uuid == "site-holder-uuid-a1"

        second_root = ResourceTreeSet.from_plr_resources([restored]).trees[0].root_node.res_content
        assert second_root.sites == root.sites

    def test_itemized_carrier_serialization_keeps_site_uuid(self):
        pytest.importorskip("pylabrobot")
        from pylabrobot.resources import Coordinate, ResourceHolder

        from unilabos.resources.itemized_carrier import ItemizedCarrier

        placeholder = ResourceHolder(name="itemized_A1", size_x=10, size_y=11, size_z=12)
        placeholder.location = Coordinate(1, 2, 3)
        carrier = ItemizedCarrier(
            name="itemized_rt",
            size_x=100,
            size_y=80,
            size_z=20,
            sites={"A1": placeholder},
        )
        first = carrier.serialize()["sites"]
        assert first[0]["uuid"]
        assert first[0]["index"] == 0

        restored = ItemizedCarrier.deserialize(carrier.serialize(), allow_marshal=True)
        assert restored.serialize()["sites"][0]["uuid"] == first[0]["uuid"]

    def test_non_itemized_plr_site_list_round_trip(self):
        """PRCXI9300Deck 也是 sites[] 协议源，但不继承 ItemizedCarrier。"""
        pytest.importorskip("pylabrobot")
        from unilabos.devices.liquid_handling.prcxi.prcxi import PRCXI9300Deck

        deck = PRCXI9300Deck(name="prcxi_sites_rt", size_x=600, size_y=400, size_z=10)
        deck.unilabos_uuid = "prcxi-deck-uuid"
        first_set = ResourceTreeSet.from_plr_resources([deck])
        first_sites = first_set.trees[0].root_node.res_content.sites
        assert first_sites is not None and len(first_sites) == 16
        assert all(site["uuid"] for site in first_sites)

        wire = json.loads(json.dumps([n.res_content.model_dump(by_alias=True) for n in first_set.all_nodes]))
        restored = ResourceTreeSet.from_raw_dict_list(wire).to_plr_resources(skip_devices=False)[0]
        second_sites = ResourceTreeSet.from_plr_resources([restored]).trees[0].root_node.res_content.sites
        assert second_sites == first_sites


class TestLegacyGraphioRoundTrip:
    def test_standard_carrier_sites_survive_legacy_converter(self):
        pytest.importorskip("pylabrobot")
        from pylabrobot.resources import Carrier, Coordinate, ResourceHolder

        from unilabos.resources.graphio import resource_plr_to_ulab, resource_ulab_to_plr

        site = ResourceHolder(name="legacy_site_A1", size_x=10, size_y=11, size_z=12)
        site.location = Coordinate(1, 2, 3)
        site.unilabos_uuid = "legacy-site-uuid-a1"
        carrier = Carrier(name="legacy_carrier", size_x=100, size_y=80, size_z=20, sites={5: site})
        carrier.location = Coordinate.zero()

        ulab = resource_plr_to_ulab(carrier)
        assert ulab["sites"][0]["uuid"] == "legacy-site-uuid-a1"
        assert ulab["sites"][0]["index"] == 5
        assert "sites" not in ulab["config"]

        restored = resource_ulab_to_plr(ulab, plr_model=True)
        assert list(restored.sites) == [5]
        assert restored.sites[5].unilabos_uuid == "legacy-site-uuid-a1"
