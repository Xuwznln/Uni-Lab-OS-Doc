"""默认子件（default_children）设计的 TDD 测试。

对应设计文档：
    product_designs/square_devices_and_labs/10_default_children_device_template_plan.md

本轮（TDD 第一阶段）只覆盖 OS 侧、可在当前代码上运行的两条 reviewer 边界约束：

- Edge#1：`backend=None` 不仅要对「构造对象」安全，还要对「注册期 dump config_info」安全。
  工厂必须在 `__init__` 内把完整结构（carrier + 嵌套 rack + 内容物）backend-free 建成，
  dump 只读内存结构、不碰 backend。
- Edge#2：pylabrobot 的 site 是资源树 child；但 UniLab `ItemizedCarrier` 的**空 site**
  是 carrier 自身的 slot metadata（走 `serialize()["sites"]` 通道），**不是**独立资源树节点。
  只有实际放进去的内容物才会成为 `children` / dump 出的子节点。

注册表 `_expand_default_children` + `DefaultChild` 尚未实现，相关断言以 xfail 标记，作为
后续阶段 1 的 TDD 驱动。
"""

from __future__ import annotations

import pytest

from unilabos.resources.itemized_carrier import Bottle, ItemizedCarrier
from unilabos.resources.resource_tracker import ResourceTreeSet


def _dump_flat(resource):
    """按注册期同源路径 dump 出单个资源的扁平树（config_info 形态）。

    与 `registry.py::_populate_resource_config_info` 使用完全一致的调用：
        ResourceTreeSet.from_plr_resources([inst]).dump()
    返回该资源对应的扁平节点列表（[0]=主节点，[1:]=真实子节点）。
    """
    dumped = ResourceTreeSet.from_plr_resources(
        [resource], known_newly_created=True, old_size=True
    ).dump(old_position=True)
    return dumped[0]


def _empty_rack(name: str, n_sites: int = 51) -> ItemizedCarrier:
    """一个空的 rack：n_sites 个空 site，全部作为 slot metadata（无内容物）。"""
    return ItemizedCarrier(
        name=name,
        size_x=100.0,
        size_y=100.0,
        size_z=50.0,
        num_items_x=1,
        num_items_y=n_sites,
        num_items_z=1,
        sites={i: None for i in range(n_sites)},
    )


# --------------------------------------------------------------------------- #
# Edge#2 — 空 site 是 slot metadata，不是资源树 child
# --------------------------------------------------------------------------- #


def test_empty_carrier_sites_are_slot_metadata_not_children():
    """空 rack 的 51 个 site 只出现在 serialize()["sites"]，不出现在资源树 children。"""
    rack = _empty_rack("rack_A", n_sites=51)

    serialized = rack.serialize()
    # 通道 (b)：空 site 走 sites 元数据数组
    assert len(serialized["sites"]) == 51
    # 通道 (a)：资源树 children 为空——空 site 不是 child
    assert len(serialized["children"]) == 0

    # dump（注册期同源）只含主节点，无 site 子节点
    flat = _dump_flat(rack)
    assert len(flat) == 1
    assert flat[0]["name"] == "rack_A"


def test_assigned_content_becomes_real_tree_child_but_sites_metadata_unchanged():
    """只有实际放入的内容物才成为资源树 child；空 site 仍是 slot metadata。

    这是「双通道」模型的关键对照：装入内容物走通道 (a)（children/dump），
    空 site 始终走通道 (b)（serialize sites），二者不混为一棵树。
    """
    rack = _empty_rack("rack_A", n_sites=4)
    assert len(_dump_flat(rack)) == 1  # 装入前：无子节点

    plate = Bottle(name="plate_0", diameter=10.0, height=20.0, max_volume=100.0)
    rack.assign_resource_to_site(plate, 0)

    serialized = rack.serialize()
    # slot metadata 通道条数不因装入而变（仍是 4 个 site 槽位）
    assert len(serialized["sites"]) == 4
    # 装入的内容物成为唯一的资源树 child
    assert len(serialized["children"]) == 1

    flat = _dump_flat(rack)
    assert len(flat) == 2
    assert [node["name"] for node in flat] == ["rack_A", "plate_0"]


# --------------------------------------------------------------------------- #
# Edge#1 — backend=None 对注册期 dump 也安全，dump 出完整结构树
# --------------------------------------------------------------------------- #


class _IncubatorLikeFactory(ItemizedCarrier):
    """模拟「设备驱动即 pylabrobot 资源」的工厂型模板（如 Cytomat Incubator）。

    契约（Edge#1）：完整结构（carrier + N 个 rack 子件 + 每个 rack 装一个内容物）
    必须在 `__init__` 内 backend-free 建成；`backend` 只被存下，注册期 dump 不触碰它。
    首参为 `name`，以便注册期 `res_class(res_class.__name__)` 单位置参调用。
    """

    N_RACKS = 3
    SITES_PER_RACK = 4

    def __init__(self, name: str, backend=None):
        super().__init__(
            name=name,
            size_x=300.0,
            size_y=300.0,
            size_z=300.0,
            num_items_x=1,
            num_items_y=self.N_RACKS,
            num_items_z=1,
            sites={i: None for i in range(self.N_RACKS)},
        )
        # backend 仅存储，绝不在结构构建 / dump 路径中使用（模拟 Machine.__init__ 语义）
        self.backend = backend
        # 完整结构在 __init__ 内建成：每个 rack 作为真实子件装入，且各装一个内容物
        for r in range(self.N_RACKS):
            rack = ItemizedCarrier(
                name=f"{name}_rack_{r}",
                size_x=80.0,
                size_y=80.0,
                size_z=80.0,
                num_items_x=1,
                num_items_y=self.SITES_PER_RACK,
                num_items_z=1,
                sites={i: None for i in range(self.SITES_PER_RACK)},
            )
            content = Bottle(
                name=f"{name}_rack_{r}_plate", diameter=10.0, height=20.0, max_volume=100.0
            )
            rack.assign_resource_to_site(content, 0)
            self.assign_resource_to_site(rack, r)


def test_backend_free_dump_yields_full_structure_tree():
    """以注册期 dump 的同源调用（backend 缺省 None）dump 出完整多层结构。

    断言 dump 不因 backend=None 缺失结构：carrier + 3 个 rack + 3 个内容物 = 7 个节点，
    证明结构在 __init__ 内 backend-free 建成、dump 只读内存。
    """
    # 注册期调用形态：res_class(res_class.__name__)，backend 走默认 None
    inst = _IncubatorLikeFactory(_IncubatorLikeFactory.__name__)
    assert inst.backend is None

    flat = _dump_flat(inst)
    names = [node["name"] for node in flat]

    # 主节点存在
    assert names[0] == _IncubatorLikeFactory.__name__
    # 3 个 rack 子件全部 dump 出（真实子件，通道 a）
    rack_names = [n for n in names if n.endswith(tuple(f"_rack_{r}" for r in range(3)))]
    assert len(rack_names) == 3
    # 3 个内容物也 dump 出（rack 内实际装入的 plate）
    plate_names = [n for n in names if n.endswith("_plate")]
    assert len(plate_names) == 3
    # 总节点数 = 1 carrier + 3 rack + 3 plate
    assert len(flat) == 7


def test_backend_free_dump_does_not_touch_backend():
    """dump 路径不得访问 backend：注入一个「一碰就炸」的 backend stub，dump 仍须成功。

    对应 Edge#1 硬约束：serialize / dump 只读内存结构，不读固件 / 在线状态。
    """

    class _ExplodingBackend:
        def __getattr__(self, item):  # 任何属性访问都抛错
            raise AssertionError(f"dump 路径不应触碰 backend（访问了 {item!r}）")

    inst = _IncubatorLikeFactory(_IncubatorLikeFactory.__name__, backend=_ExplodingBackend())
    # 若 dump 触碰 backend，_ExplodingBackend 会抛 AssertionError 使本用例失败
    flat = _dump_flat(inst)
    assert len(flat) == 7


# --------------------------------------------------------------------------- #
# 阶段 1 TDD 驱动 — 注册表 default_children 展开（尚未实现）
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(reason="DefaultChild / _expand_default_children 尚未实现（阶段 1）", strict=False)
def test_default_children_declaration_and_expansion():
    """声明含 default_children 的模板，注册表应展开进 config_info 内联树。

    期望：
    - `DefaultChild(ref=..., kind="resource", name="rack_A", slot="rack_slot_0")` 可声明；
    - 注册后父 entry 的 `config_info` 追加了被引用 rack 模板的展开树；
    - `slot` 解析为子节点 pose.extra.mount_point（复用既有挂载字段，不加新列）。
    """
    from unilabos.registry.decorators import DefaultChild  # noqa: F401  (阶段 1 新增)
    from unilabos.registry.registry import _expand_default_children  # noqa: F401  (阶段 1 新增)

    child = DefaultChild(
        ref="cytomat_rack_9mm_51", kind="resource", name="rack_A", slot="rack_slot_0"
    )
    parent_entry = {"registry_type": "device", "default_children": [child.model_dump()], "config_info": []}
    registry = {
        "cytomat_rack_9mm_51": {
            "registry_type": "resource",
            "config_info": [[{"name": f"site_{i}", "type": "resource"} for i in range(51)]],
        }
    }

    expanded = _expand_default_children(parent_entry, registry)

    flat = expanded["config_info"][0]
    # 父的 config_info 追加了 rack + 51 site
    assert any(node["name"] == "rack_A" for node in flat)
    assert sum(1 for node in flat if node["name"].startswith("site_")) == 51
    # slot 落到 pose.extra.mount_point，而非新增 slot 字段
    rack_node = next(node for node in flat if node["name"] == "rack_A")
    assert rack_node["pose"]["extra"]["mount_point"] == "rack_slot_0"
