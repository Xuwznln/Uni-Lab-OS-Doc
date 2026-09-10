"""清台移除枪头架时重置枪头池（LiquidHandlerAbstract.resource_tree_remove）。

背景：同一进程内多次运行工作流时，消费游标 ``_tip_next_index`` 会随每次 transfer 累积，
而 ``_init_all_tip_pools`` 只在进程首次 ``set_tiprack`` 时执行一次（受 ``_tip_pools_initialized``
守卫），清台 + create_resource 复用同名枪头架时游标不归零，最终误报 ``Tip rack exhausted``。

修复：清台（clear_device_resources → s2c_resource_tree remove）在卸载前回调
``driver_instance.resource_tree_remove``；若被移除资源里含 TipRack/TipSpot，则把
``_tip_pools_initialized`` 置 False，令下次 ``set_tiprack`` 重新扫描 deck 并使游标归零。

纯抽象层逻辑：用 stub deck + 可迭代的 TipRack 占位，不走真实 backend。
环境无法 import 抽象层时整体 skip。
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

try:
    from unilabos.devices.liquid_handling.liquid_handler_abstract import (
        LiquidHandlerAbstract,
        TipRack,
    )

    _ABSTRACT_AVAILABLE = True
    _ABSTRACT_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - 环境相关
    LiquidHandlerAbstract = None  # type: ignore[assignment, misc]
    TipRack = object  # type: ignore[assignment, misc]
    _ABSTRACT_AVAILABLE = False
    _ABSTRACT_IMPORT_ERROR = exc


_skip_if_no_abstract = pytest.mark.skipif(
    not _ABSTRACT_AVAILABLE,
    reason=f"LiquidHandlerAbstract not importable in this env: {_ABSTRACT_IMPORT_ERROR!r}",
)

_RACK_NAME_SLOT3 = "PRCXI_300ul_Tips_slot_3"
_RACK_NAME_SLOT6 = "PRCXI_300ul_Tips_slot_6"
_TIP_MODEL = "PRCXI_300ul_Tips"
_TIPS_PER_RACK = 8


class _FakeTipRack(TipRack):  # type: ignore[misc]
    """isinstance(x, TipRack) 为 True，且可被 ``_flatten_tips_from_one`` 展开。"""

    def __init__(self, name: str, n: int = _TIPS_PER_RACK, model: str = _TIP_MODEL) -> None:
        # 不调用 super().__init__；先补 parent/_name，避免 PLR 的 name setter 读未初始化字段
        self.parent = None
        self._name = name
        self.model = model
        self.children: List[Any] = []
        self._spots = [type("Spot", (), {"name": f"{name}/{i}"})() for i in range(n)]

    def __iter__(self):
        return iter(self._spots)


class _FakePlate:
    """普通板占位：不是 TipRack，也没有枪头子孙。"""

    def __init__(self) -> None:
        self.name = "some_plate"
        self.children: List[Any] = []


class _FakeDeck:
    def __init__(self, children: Optional[List[Any]] = None) -> None:
        self.children = list(children or [])


def _make_handler(deck_children: Optional[List[Any]] = None) -> Any:
    inst: Any = LiquidHandlerAbstract.__new__(LiquidHandlerAbstract)
    inst._ros_node = None
    inst.deck = _FakeDeck(deck_children)
    inst._tip_pools_initialized = False
    return inst


def _make_pair() -> tuple[_FakeTipRack, _FakeTipRack]:
    return (
        _FakeTipRack(_RACK_NAME_SLOT3),
        _FakeTipRack(_RACK_NAME_SLOT6),
    )


@_skip_if_no_abstract
class TestResourceTreeRemoveResetsTipPool:
    def test_remove_tip_rack_invalidates_pool(self) -> None:
        """移除枪头架 → 标记枪头池待重建。"""
        h = _make_handler()
        h._tip_pools_initialized = True
        h.resource_tree_remove([_FakeTipRack(_RACK_NAME_SLOT3)])
        assert h._tip_pools_initialized is False

    def test_remove_plain_plate_keeps_pool(self) -> None:
        """仅移除普通板（transfer_resource / deduct）→ 不影响枪头池。"""
        h = _make_handler()
        h._tip_pools_initialized = True
        h.resource_tree_remove([_FakePlate()])
        assert h._tip_pools_initialized is True

    def test_mixed_removal_resets_when_tip_rack_present(self) -> None:
        """混合移除只要含枪头架就重置。"""
        h = _make_handler()
        h._tip_pools_initialized = True
        h.resource_tree_remove([_FakePlate(), _FakeTipRack(_RACK_NAME_SLOT3)])
        assert h._tip_pools_initialized is False

    def test_empty_removal_noop(self) -> None:
        """空列表不改变状态。"""
        h = _make_handler()
        h._tip_pools_initialized = True
        h.resource_tree_remove([])
        assert h._tip_pools_initialized is True

    def test_nested_tip_rack_detected(self) -> None:
        """被移除的容器子孙里含枪头架也应重置。"""
        h = _make_handler()
        h._tip_pools_initialized = True
        container = _FakePlate()
        container.children = [_FakeTipRack(_RACK_NAME_SLOT3)]
        h.resource_tree_remove([container])
        assert h._tip_pools_initialized is False


@_skip_if_no_abstract
class TestClearThenSetTiprackResetsCursor:
    """锁住现场根因：同名满架重建后，必须能再取到枪头，而不是 next_index=pool_len。"""

    def test_same_name_racks_without_clear_keep_exhausted_cursor(self) -> None:
        """复现 bug：不清台、只 create 同名架时，游标不归零。"""
        rack3, rack6 = _make_pair()
        h = _make_handler([rack3, rack6])
        h.set_tiprack([rack3, rack6])
        key = h._active_tip_type_key
        pool_len = len(h._tip_flat_spots[key])
        assert pool_len == 2 * _TIPS_PER_RACK

        for _ in range(pool_len):
            h._get_next_tip()
        assert h._tip_next_index[key] == pool_len
        with pytest.raises(RuntimeError, match="Tip rack exhausted"):
            h._get_next_tip()

        # 模拟新一轮 create_resource：对象是新的，名字没变，且没有 resource_tree_remove
        new3, new6 = _make_pair()
        h.deck.children = [new3, new6]
        h.set_tiprack([new3, new6])
        assert h._tip_pools_initialized is True
        assert h._tip_next_index[key] == pool_len
        with pytest.raises(RuntimeError, match="Tip rack exhausted"):
            h._get_next_tip()

    def test_clear_then_recreate_same_name_racks_resets_cursor(self) -> None:
        """清台后重建同名满架：下次 set_tiprack 游标归零，能取到新架第一个枪头。"""
        rack3, rack6 = _make_pair()
        h = _make_handler([rack3, rack6])
        h.set_tiprack([rack3, rack6])
        key = h._active_tip_type_key
        pool_len = len(h._tip_flat_spots[key])
        first_old = h._tip_flat_spots[key][0]
        for _ in range(pool_len):
            h._get_next_tip()
        with pytest.raises(RuntimeError, match="Tip rack exhausted"):
            h._get_next_tip()

        # 清台：先回调，再从 deck 卸下旧架（与 s2c_resource_tree 顺序一致）
        h.resource_tree_remove([rack3, rack6])
        assert h._tip_pools_initialized is False
        h.deck.children = []

        # create_resource：挂上同名新满架
        new3, new6 = _make_pair()
        h.deck.children = [new3, new6]
        h.set_tiprack([new3, new6])

        assert h._tip_pools_initialized is True
        assert h._active_tip_type_key == key
        assert h._tip_next_index[key] == 0
        assert len(h._tip_flat_spots[key]) == pool_len
        first_new = h._get_next_tip()
        assert first_new is h._tip_flat_spots[key][0]
        assert first_new is not first_old
        assert first_new.name == f"{_RACK_NAME_SLOT3}/0"
        assert h._tip_next_index[key] == 1
