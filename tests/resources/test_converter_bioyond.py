import pytest
import json
from pathlib import Path

from unilabos.resources.graphio import (
    resource_bioyond_to_plr,
    resource_plr_to_bioyond,
)
from unilabos.registry.registry import lab_registry

from unilabos.resources.presets.bioyond.bottle_carriers import (
    BIOYOND_PolymerStation_1BottleCarrier,
    BIOYOND_PolymerStation_6StockCarrier,
)
from unilabos.resources.presets.bioyond.decks import BIOYOND_PolymerReactionStation_Deck

lab_registry.setup()


type_mapping = {
    "烧杯": ("BIOYOND_PolymerStation_1FlaskCarrier", "3a14196b-24f2-ca49-9081-0cab8021bf1a"),
    "试剂瓶": ("BIOYOND_PolymerStation_1BottleCarrier", ""),
    "样品板": ("BIOYOND_PolymerStation_6StockCarrier", "3a14196e-b7a0-a5da-1931-35f3000281e9"),
    "分装板": ("BIOYOND_PolymerStation_6VialCarrier", "3a14196e-5dfe-6e21-0c79-fe2036d052c4"),
    "样品瓶": ("BIOYOND_PolymerStation_Solid_Stock", "3a14196a-cf7d-8aea-48d8-b9662c7dba94"),
    "90%分装小瓶": ("BIOYOND_PolymerStation_Solid_Vial", "3a14196c-cdcf-088d-dc7d-5cf38f0ad9ea"),
    "10%分装小瓶": ("BIOYOND_PolymerStation_Liquid_Vial", "3a14196c-76be-2279-4e22-7310d69aed68"),
}


@pytest.fixture
def bioyond_materials_reaction() -> list[dict]:
    with Path(__file__).with_name("bioyond_materials_reaction.json").open(
        "r", encoding="utf-8"
    ) as f:
        data = json.load(f)
    return data


@pytest.fixture
def bioyond_materials_liquidhandling_1() -> list[dict]:
    with Path(__file__).with_name("bioyond_materials_liquidhandling_1.json").open(
        "r", encoding="utf-8"
    ) as f:
        data = json.load(f)
    return data


@pytest.fixture
def bioyond_materials_liquidhandling_2() -> list[dict]:
    with Path(__file__).with_name("bioyond_materials_liquidhandling_2.json").open(
        "r", encoding="utf-8"
    ) as f:
        data = json.load(f)
    return data


@pytest.mark.parametrize("materials_fixture", [
    "bioyond_materials_reaction",
    "bioyond_materials_liquidhandling_1",
])
def test_bioyond_to_plr(materials_fixture, request, tmp_path) -> None:
    materials = request.getfixturevalue(materials_fixture)
    deck = BIOYOND_PolymerReactionStation_Deck("test_deck")
    resource_bioyond_to_plr(materials, type_mapping=type_mapping, deck=deck)
    output_path = tmp_path / "test.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(deck.serialize(), f, indent=4)
    assert output_path.is_file()


def test_plr_to_bioyond_reads_occupants_from_resource_holders():
    carrier = BIOYOND_PolymerStation_6StockCarrier("carrier")
    outbound_mapping = {
        "BIOYOND_PolymerStation_6StockCarrier": ("样品板", "carrier-type"),
        "BIOYOND_PolymerStation_Liquid_Vial": ("10%分装小瓶", "liquid-type"),
        "BIOYOND_PolymerStation_Solid_Vial": ("90%分装小瓶", "solid-type"),
    }

    result = resource_plr_to_bioyond([carrier], type_mapping=outbound_mapping)

    assert len(result) == 1
    assert [(item["x"], item["y"], item["z"]) for item in result[0]["details"]] == [
        (1, 1, 1),
        (2, 1, 1),
        (1, 2, 1),
        (2, 2, 1),
        (1, 3, 1),
        (2, 3, 1),
    ]


def test_plr_to_bioyond_single_site_carrier_uses_holder_resource():
    carrier = BIOYOND_PolymerStation_1BottleCarrier("carrier")
    carrier[0].resource.tracker.liquids = [("water", 5)]
    outbound_mapping = {
        "BIOYOND_PolymerStation_1BottleCarrier": ("试剂瓶", "carrier-type"),
    }

    result = resource_plr_to_bioyond([carrier], type_mapping=outbound_mapping)

    assert result[0]["typeId"] == "carrier-type"
    assert result[0]["name"] == "water"
    assert result[0]["quantity"] == 5
