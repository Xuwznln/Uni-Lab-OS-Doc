"""P9 — ``liquid_history`` schema v3 + helper 单元测试。

测试覆盖：
    - :func:`append_liquid_history`：写 v3 entry / tracker 缺失 graceful / 滚动上限
    - :func:`normalize_liquid_history`：v3 dict / v2 tuple / list[str] / 混合 / 非法
    - :func:`well_current_liquid_name`：tracker.liquids 末项 / get_liquids fallback / 缺失

注：``LiquidHandlerAbstract.set_liquid`` 写 history 的集成（"set" action）覆盖
逻辑相同（直接调用 :func:`append_liquid_history`），由本测试间接验证；端到端走 PLR
真实 ``Well.set_liquids`` 的集成测试在 ``tests/devices/liquid_handling/unit_test.py``
范围内随 PLR 环境就绪后增补，本 P9 提交保持解耦。

详见 ``product_designs/protocol_convert/09-liquid-history-unknown-debug.md`` §8。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Tuple

import pytest

# liquid_history 模块**不依赖** pylabrobot，可在 PLR 环境缺失时独立 import / 单测。
from unilabos.devices.liquid_handling.liquid_history import (
    LIQUID_HISTORY_MAX_ENTRIES,
    LiquidHistoryEntry,
    append_liquid_history,
    normalize_liquid_history,
    well_current_liquid_name,
)


# ---------------------------------------------------------------------------
# Fixtures：DummyTracker / DummyWell（避免引入真实 PLR Well/VolumeTracker 依赖）
# ---------------------------------------------------------------------------


@dataclass
class DummyTracker:
    """模拟 PLR VolumeTracker：仅暴露 P9 hook 关心的字段。"""

    liquid_history: List[Any] = field(default_factory=list)
    liquids: List[Tuple[Any, float]] = field(default_factory=list)
    max_volume: float = 200.0
    is_disabled: bool = False


@dataclass
class DummyWell:
    """模拟 PLR Well：仅暴露 ``tracker``。"""

    name: str = "well_A1"
    max_volume: float = 200.0
    tracker: DummyTracker = field(default_factory=DummyTracker)


# ---------------------------------------------------------------------------
# append_liquid_history
# ---------------------------------------------------------------------------


class TestAppendLiquidHistory:
    def test_append_creates_v3_entry(self) -> None:
        well = DummyWell()
        append_liquid_history(well, "Plasma", 100.0, "set")

        assert len(well.tracker.liquid_history) == 1
        entry = well.tracker.liquid_history[0]
        assert entry["name"] == "Plasma"
        assert entry["volume"] == 100.0
        assert entry["action"] == "set"
        assert "timestamp" in entry and isinstance(entry["timestamp"], str)

    def test_append_aspirate_negative_volume(self) -> None:
        well = DummyWell()
        append_liquid_history(well, "Water", -50.0, "aspirate")

        assert well.tracker.liquid_history[0]["volume"] == -50.0
        assert well.tracker.liquid_history[0]["action"] == "aspirate"

    def test_append_with_empty_name_keeps_empty_string(self) -> None:
        """name 为空时应写入 ``""`` 而非字面 "unknown"（避免视觉混淆 bottom_type）。"""
        well = DummyWell()
        append_liquid_history(well, "", 50.0, "dispense")

        assert well.tracker.liquid_history[0]["name"] == ""

    def test_append_with_none_name_normalized_to_empty_string(self) -> None:
        well = DummyWell()
        append_liquid_history(well, None, 50.0, "dispense")  # type: ignore[arg-type]

        assert well.tracker.liquid_history[0]["name"] == ""

    def test_append_initializes_history_if_missing(self) -> None:
        """tracker 没有 liquid_history 属性时 helper 自动创建空 list 并写入。"""
        well = DummyWell()
        del well.tracker.liquid_history  # 模拟全新 PLR tracker
        append_liquid_history(well, "X", 10.0, "set")

        assert hasattr(well.tracker, "liquid_history")
        assert len(well.tracker.liquid_history) == 1

    def test_append_no_tracker_is_graceful(self) -> None:
        """well 无 tracker 时静默不抛（保护主流程）。"""

        class NoTrackerWell:
            name = "no_tracker"

        well = NoTrackerWell()
        append_liquid_history(well, "X", 10.0, "set")  # 不应抛
        assert not hasattr(well, "tracker")

    def test_append_action_defaults_to_legacy_when_empty(self) -> None:
        well = DummyWell()
        append_liquid_history(well, "X", 1.0, "")

        assert well.tracker.liquid_history[0]["action"] == "legacy"

    # -----------------------------------------------------------------
    # 0-vol guard（用户 2026-05-28 决策）：vol == 0 应被静默跳过，
    # 避免 set_liquid(name, 0) 等场景把 (name, 0.0) 噪声塞进审计日志。
    # -----------------------------------------------------------------

    def test_append_zero_volume_is_skipped(self) -> None:
        """vol == 0.0 → 不写 history（用户决策）。"""
        well = DummyWell()
        append_liquid_history(well, "agar", 0.0, "set")
        assert well.tracker.liquid_history == []

    def test_append_tiny_volume_below_epsilon_is_skipped(self) -> None:
        """abs(vol) < 1e-9 → 不写（防浮点误差）。"""
        well = DummyWell()
        append_liquid_history(well, "agar", 1e-12, "set")
        append_liquid_history(well, "agar", -1e-12, "aspirate")
        assert well.tracker.liquid_history == []

    def test_append_nonzero_volume_still_writes(self) -> None:
        """vol > 0 / vol < 0（aspirate 负数）正常通过 guard，按原路径写入。

        注：当前实现为 PLR 兼容写 tuple ``(name, vol)``（见 liquid_history.py:96-118 注释），
        所以这里用 tuple 索引断言，不用 dict key。schema-v3 dict 形态的对外
        normalize 由 :func:`normalize_liquid_history` 完成。
        """
        well = DummyWell()
        append_liquid_history(well, "agar", 50.0, "set")
        append_liquid_history(well, "agar", -50.0, "aspirate")
        assert len(well.tracker.liquid_history) == 2
        assert well.tracker.liquid_history[0][1] == 50.0
        assert well.tracker.liquid_history[1][1] == -50.0

    def test_append_volume_just_above_epsilon_is_kept(self) -> None:
        """abs(vol) >= 1e-9 → 保留（边界值正向验证）。"""
        well = DummyWell()
        append_liquid_history(well, "x", 1e-6, "set")  # 0.001 nL，仍 >> 1e-9
        assert len(well.tracker.liquid_history) == 1
        assert well.tracker.liquid_history[0][1] == 1e-6

    def test_append_zero_volume_still_normalizes_existing_dict_entries(self) -> None:
        """归零 append 也必须先归一化 history（防 PLR ``current_liquids`` 解 dict 失败）。

        场景：远端 snapshot 把 dict 形态的 v3 entry 直接塞进 ``tracker.liquid_history``，
        随后第一次调到本 helper 是 ``set_liquid(name, 0)``。若 0-vol skip 走在归一化之前，
        history 会一直保留 dict → PLR ``for name, vol in self.liquid_history`` 崩溃，
        进而拖垮 aspirate / drop 时序，最终让通道残留 tip 引发 HasTipError。
        """
        well = DummyWell()
        well.tracker.liquid_history = [
            {"name": "Plasma", "volume": 50.0, "action": "set"},
            ("Water", 30.0, "ul"),  # 兼容 3-tuple 旧形态
        ]
        append_liquid_history(well, "noop", 0.0, "set")
        assert well.tracker.liquid_history == [
            ("Plasma", 50.0),
            ("Water", 30.0),
        ]

    def test_append_zero_volume_still_normalizes_string_entries(self) -> None:
        well = DummyWell()
        well.tracker.liquid_history = ["A", "B"]
        append_liquid_history(well, "noop", 0.0, "set")
        assert well.tracker.liquid_history == [("A", 0.0), ("B", 0.0)]

    def test_append_respects_max_entries_rolling(self) -> None:
        """超过 ``LIQUID_HISTORY_MAX_ENTRIES`` 时丢弃头部，保留最近 entries。"""
        well = DummyWell()
        well.tracker.liquid_history = [
            {"name": f"old_{i}"} for i in range(LIQUID_HISTORY_MAX_ENTRIES + 5)
        ]
        append_liquid_history(well, "newest", 1.0, "set")

        assert len(well.tracker.liquid_history) == LIQUID_HISTORY_MAX_ENTRIES
        assert well.tracker.liquid_history[-1]["name"] == "newest"
        assert well.tracker.liquid_history[0]["name"] != "old_0"


# ---------------------------------------------------------------------------
# normalize_liquid_history
# ---------------------------------------------------------------------------


class TestNormalizeLiquidHistory:
    def test_v3_dict_passthrough_with_field_defaults(self) -> None:
        raw = [{"name": "A", "volume": 100, "action": "set", "timestamp": "2026-05-22T00:00:00Z"}]
        result = normalize_liquid_history(raw)

        assert result == [{
            "name": "A",
            "volume": 100.0,
            "action": "set",
            "timestamp": "2026-05-22T00:00:00Z",
        }]

    def test_v3_dict_missing_optional_fields_filled_with_defaults(self) -> None:
        raw = [{"name": "A"}]
        result = normalize_liquid_history(raw)

        assert result == [{"name": "A", "volume": 0.0, "action": "legacy"}]
        assert "timestamp" not in result[0]

    def test_v2_tuple_upgraded_to_v3_legacy(self) -> None:
        raw = [("A", 100), ("B", 50.5)]
        result = normalize_liquid_history(raw)

        assert result == [
            {"name": "A", "volume": 100.0, "action": "legacy"},
            {"name": "B", "volume": 50.5, "action": "legacy"},
        ]

    def test_list_of_strings_upgraded(self) -> None:
        raw = ["A", "B"]
        result = normalize_liquid_history(raw)

        assert result == [
            {"name": "A", "volume": 0.0, "action": "legacy"},
            {"name": "B", "volume": 0.0, "action": "legacy"},
        ]

    def test_mixed_input_normalized(self) -> None:
        raw = [
            {"name": "A", "volume": 1, "action": "set"},
            ("B", 2),
            "C",
        ]
        result = normalize_liquid_history(raw)

        assert [e["name"] for e in result] == ["A", "B", "C"]
        assert [e["action"] for e in result] == ["set", "legacy", "legacy"]

    def test_invalid_entries_dropped(self) -> None:
        raw = [42, None, {"name": "A"}, ("only_one",)]
        result = normalize_liquid_history(raw)

        # 只保留 {"name": "A"} 这一条；其它都被丢弃
        assert len(result) == 1
        assert result[0]["name"] == "A"
        assert result[0]["volume"] == 0.0  # 缺省补 0

    def test_non_list_input_returns_empty(self) -> None:
        assert normalize_liquid_history(None) == []
        assert normalize_liquid_history("not_a_list") == []
        assert normalize_liquid_history({"name": "X"}) == []

    def test_tuple_with_unconvertible_volume_falls_back_to_zero(self) -> None:
        raw = [("A", "not_a_number")]
        result = normalize_liquid_history(raw)

        assert result[0]["volume"] == 0.0


# ---------------------------------------------------------------------------
# well_current_liquid_name
# ---------------------------------------------------------------------------


class TestWellCurrentLiquidName:
    def test_returns_last_liquid_name_from_tuple(self) -> None:
        well = DummyWell()
        well.tracker.liquids = [("Water", 50.0), ("Plasma", 100.0)]
        assert well_current_liquid_name(well) == "Plasma"

    def test_returns_enum_like_name_attr(self) -> None:
        class FakeLiquid:
            name = "ETHANOL"

        well = DummyWell()
        well.tracker.liquids = [(FakeLiquid(), 100.0)]
        assert well_current_liquid_name(well) == "ETHANOL"

    def test_empty_liquids_returns_empty_string(self) -> None:
        well = DummyWell()
        well.tracker.liquids = []
        assert well_current_liquid_name(well) == ""

    def test_no_tracker_returns_empty_string(self) -> None:
        class NoTrackerWell:
            name = "x"

        assert well_current_liquid_name(NoTrackerWell()) == ""

    def test_none_liquid_returns_empty_string(self) -> None:
        well = DummyWell()
        well.tracker.liquids = [(None, 100.0)]
        assert well_current_liquid_name(well) == ""

    def test_string_liquid_returned_as_is(self) -> None:
        well = DummyWell()
        well.tracker.liquids = ["Saline"]
        assert well_current_liquid_name(well) == "Saline"
