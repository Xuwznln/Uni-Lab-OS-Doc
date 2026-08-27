"""collect_discardable_labware：只收集 Deck 直接子耗材，不含 Deck/设备自身。"""
from types import SimpleNamespace

from unilabos.resources.resource_tracker import collect_discardable_labware


def _child(name: str, uid: str | None, category: str = "plate"):
    return SimpleNamespace(name=name, unilabos_uuid=uid, category=category, children=[])


def test_collects_direct_labware_with_uuid():
    plate = _child("plate_13", "u-plate")
    tips = _child("tips_3", "u-tips", category="tip_rack")
    deck = SimpleNamespace(
        name="PRCXI_Deck",
        unilabos_uuid="u-deck",
        category="deck",
        children=[plate, tips],
    )

    got = collect_discardable_labware(deck)
    assert got == [("u-plate", "plate_13"), ("u-tips", "tips_3")]


def test_skips_deck_device_and_missing_uuid():
    nested_deck = _child("inner_deck", "u-inner", category="deck")
    device_like = _child("PRCXI", "u-dev", category="device")
    ghost = _child("no_uuid_plate", None)
    plate = _child("ok", "u-ok")
    parent = SimpleNamespace(name="deck", children=[nested_deck, device_like, ghost, plate])

    got = collect_discardable_labware(parent)
    assert got == [("u-ok", "ok")]


def test_empty_or_none_children():
    assert collect_discardable_labware(SimpleNamespace(children=None)) == []
    assert collect_discardable_labware(SimpleNamespace(children=[])) == []
    assert collect_discardable_labware(SimpleNamespace()) == []


def test_dedupes_same_uuid():
    a = _child("a", "same")
    b = _child("b", "same")
    parent = SimpleNamespace(children=[a, b])
    assert collect_discardable_labware(parent) == [("same", "a")]
