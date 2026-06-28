"""`ResourceTreeSet.merge_remote_resources` 的回归测试。

重点覆盖「云端资源 → 本地资源树」启动合并阶段的兜底：当云端（持久化）树里存在
**本地 graph.json 不含的设备级二级物料**（运行期动态产生、被同步上云的物料，例如配平板 /
枪头盒等被挂到设备根、与 deck 平级）时，合并必须**引入整棵子树而非 KeyError 崩溃**。

历史故障：`merge_remote_resources` 的「二级节点是物料」分支直接
`local_children_map[remote_child_name]` 取值，缺成员检查，遇到设备级孤儿物料即
`KeyError`，导致 edge 启动期（早于设备初始化）直接中断。

运行：
    pytest tests/resources/test_merge_remote_resources.py -v
"""

from __future__ import annotations

from typing import Any, Dict, List

from unilabos.resources.resource_tracker import ResourceTreeSet


def _node(uuid: str, name: str, type_: str, parent_uuid: str = "") -> Dict[str, Any]:
    return {
        "uuid": uuid,
        "id": name,
        "name": name,
        "type": type_,
        "parent_uuid": parent_uuid,
        "class": "",
        "config": {},
        "data": {},
    }


def _local_device_with_deck() -> ResourceTreeSet:
    """本地静态 graph：设备 + 唯一子节点 deck（无任何动态物料）。"""
    return ResourceTreeSet.from_raw_dict_list([
        _node("L-dev", "bioyond_sirna_station", "device"),
        _node("L-deck", "Bioyond_Sirna_Deck", "deck", parent_uuid="L-dev"),
    ])


def _device_children_names(tree_set: ResourceTreeSet, device_id: str) -> List[str]:
    for root in tree_set.root_nodes:
        if root.res_content.id == device_id:
            return [c.res_content.name for c in root.children]
    raise AssertionError(f"未找到设备根节点: {device_id}")


def test_merge_introduces_device_level_orphan_material_without_crash() -> None:
    """云端设备级孤儿物料（deck 同级）→ 引入整棵子树，不 KeyError。"""
    local = _local_device_with_deck()
    remote = ResourceTreeSet.from_raw_dict_list([
        _node("R-dev", "bioyond_sirna_station", "device"),
        _node("R-deck", "Bioyond_Sirna_Deck", "deck", parent_uuid="R-dev"),
        # 设备级孤儿（应在 deck 内库位，却被挂到设备根）——历史崩溃点
        _node("R-orph", "Tecan-50ul枪头盒", "tip_rack", parent_uuid="R-dev"),
    ])

    # 不应抛 KeyError
    local.merge_remote_resources(remote)

    names = _device_children_names(local, "bioyond_sirna_station")
    assert "Bioyond_Sirna_Deck" in names
    assert "Tecan-50ul枪头盒" in names, "设备级孤儿物料应被引入到本地设备下"


def test_merge_introduces_multiple_orphans() -> None:
    """多个设备级孤儿（本次实盘 4 个）全部引入，遍历不中断。"""
    local = _local_device_with_deck()
    orphan_names = ["Tecan-50ul枪头盒", "384孔配平板", "PCR配平板", "细胞培养板_2"]
    remote_nodes = [
        _node("R-dev", "bioyond_sirna_station", "device"),
        _node("R-deck", "Bioyond_Sirna_Deck", "deck", parent_uuid="R-dev"),
    ]
    for i, nm in enumerate(orphan_names):
        remote_nodes.append(_node(f"R-orph-{i}", nm, "plate", parent_uuid="R-dev"))
    remote = ResourceTreeSet.from_raw_dict_list(remote_nodes)

    local.merge_remote_resources(remote)

    names = _device_children_names(local, "bioyond_sirna_station")
    for nm in orphan_names:
        assert nm in names, f"孤儿物料未被引入: {nm}"


def test_merge_existing_level2_material_adds_missing_level3_children() -> None:
    """既有行为不回归：二级物料本地已存在 → 补齐其缺失的三级子节点。"""
    local = ResourceTreeSet.from_raw_dict_list([
        _node("L-dev", "bioyond_sirna_station", "device"),
        _node("L-deck", "Bioyond_Sirna_Deck", "deck", parent_uuid="L-dev"),
        # 本地已存在的二级物料（无三级子节点）
        _node("L-mat", "已有板", "plate", parent_uuid="L-dev"),
    ])
    remote = ResourceTreeSet.from_raw_dict_list([
        _node("R-dev", "bioyond_sirna_station", "device"),
        _node("R-mat", "已有板", "plate", parent_uuid="R-dev"),
        _node("R-well", "孔A1", "well", parent_uuid="R-mat"),
    ])

    local.merge_remote_resources(remote)

    for root in local.root_nodes:
        if root.res_content.id == "bioyond_sirna_station":
            mat = next(c for c in root.children if c.res_content.name == "已有板")
            child_names = [c.res_content.name for c in mat.children]
            assert "孔A1" in child_names, "本地已存在二级物料的缺失三级子节点应被补齐"
            break
    else:
        raise AssertionError("未找到设备根节点")


def test_merge_skips_unknown_device() -> None:
    """云端有、本地无的一级 device（非 host_node）→ 跳过，不抛异常。"""
    local = _local_device_with_deck()
    remote = ResourceTreeSet.from_raw_dict_list([
        _node("R-other", "some_other_station", "device"),
        _node("R-other-deck", "Other_Deck", "deck", parent_uuid="R-other"),
    ])

    local.merge_remote_resources(remote)

    # 本地设备子节点不变（仍只有 deck）
    assert _device_children_names(local, "bioyond_sirna_station") == ["Bioyond_Sirna_Deck"]
