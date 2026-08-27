"""TubeRack 取液兼容：Uni-Lab 把 Tube 直接作为子资源时，不能先调 PLR get_tube。

新版 pylabrobot 的 ``TubeRack.get_tube`` → ``has_container`` → ``holder.resource``，
在 ``ordered_items`` 是 Tube（PRCXI 工厂 / 反序列化）时会 AttributeError。
``_resolve_tube_compat`` 必须先 ``get_item``，再按 Tube / Holder 分支。
"""
from __future__ import annotations

import pytest

lha = pytest.importorskip(
    "unilabos.devices.liquid_handling.liquid_handler_abstract",
    reason="pylabrobot 链未完整可用，跳过 device 单测",
    exc_type=ImportError,
)
plr_resources = pytest.importorskip(
    "pylabrobot.resources",
    reason="pylabrobot 不可用",
    exc_type=ImportError,
)
prcxi = pytest.importorskip(
    "unilabos.devices.liquid_handling.prcxi.prcxi",
    reason="prcxi 驱动不可用",
    exc_type=ImportError,
)

Tube = plr_resources.Tube
Coordinate = plr_resources.Coordinate
create_ordered_items_2d = pytest.importorskip(
    "pylabrobot.resources.utils",
    reason="pylabrobot.resources.utils 不可用",
    exc_type=ImportError,
).create_ordered_items_2d
LiquidHandlerAbstract = lha.LiquidHandlerAbstract
PRCXI9300TubeRack = prcxi.PRCXI9300TubeRack


def _factory_style_rack():
    """与 ``PRCXI_EP_Adapter`` 相同：ordered_items 直接是 Tube。"""
    return PRCXI9300TubeRack(
        name="PRCXI_EP_Adapter_slot_2",
        size_x=128.04,
        size_y=85.8,
        size_z=42.66,
        ordered_items=create_ordered_items_2d(
            Tube,
            num_items_x=6,
            num_items_y=4,
            dx=3.54,
            dy=10.7,
            dz=4.58,
            item_dx=21.0,
            item_dy=18.0,
            size_x=10.6,
            size_y=10.6,
            size_z=40.0,
            max_volume=1500.0,
        ),
    )


def test_resolve_tube_compat_with_direct_tube_items():
    """工厂模型：get_tube 会崩，_resolve_tube_compat 仍应返回 Tube。"""
    rack = _factory_style_rack()
    item = rack.get_item("A1")
    assert item.__class__.__name__ == "Tube"
    with pytest.raises(AttributeError):
        rack.get_tube("A1")

    tube = LiquidHandlerAbstract._resolve_tube_compat(rack, "A1")
    assert tube.__class__.__name__ == "Tube"
    assert tube is item
    assert "A1" in tube.name


def test_resolve_tube_compat_with_resource_holder():
    """原生 PLR holder 模型：get_item 是 ResourceHolder，应取出其中的 Tube。"""
    ResourceHolder = pytest.importorskip(
        "pylabrobot.resources.resource_holder",
        reason="当前 pylabrobot 无 ResourceHolder",
        exc_type=ImportError,
    ).ResourceHolder
    TubeRack = plr_resources.TubeRack

    if not hasattr(TubeRack, "has_container"):
        pytest.skip("当前 TubeRack 不是 ContainerRack 模型")

    ordered = {}
    for i, key in enumerate(["A1", "B1"]):
        tube = Tube(name=f"tube_{key}", size_x=10, size_y=10, size_z=40, max_volume=1000)
        holder = ResourceHolder(name=f"holder_{key}", size_x=10, size_y=10, size_z=40)
        holder.location = Coordinate(i * 20, 0, 5)
        holder.assign_child_resource(tube, location=Coordinate(0, 0, 0))
        ordered[key] = holder
    rack = TubeRack(name="R", size_x=127, size_y=85, size_z=45, ordered_items=ordered)

    tube = LiquidHandlerAbstract._resolve_tube_compat(rack, "A1")
    assert tube.__class__.__name__ == "Tube"
    assert tube.name.endswith("tube_A1") or "tube_A1" in tube.name
