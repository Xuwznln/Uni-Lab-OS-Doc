from unilabos.resources.presets.bioyond.bottle_carriers import (
    BIOYOND_Electrolyte_1BottleCarrier,
    BIOYOND_Electrolyte_6VialCarrier,
)


def test_bottle_carriers_initialize_expected_site_resources():
    bottle_carrier = BIOYOND_Electrolyte_6VialCarrier("powder_carrier_01")
    beaker_carrier = BIOYOND_Electrolyte_1BottleCarrier("solution_carrier_01")

    assert len(bottle_carrier.sites) == 6
    assert len(beaker_carrier.sites) == 1
    bottle_at_0 = bottle_carrier[0].resource
    beaker_at_0 = beaker_carrier[0].resource

    assert bottle_at_0 is not None
    assert beaker_at_0 is not None
    assert bottle_at_0.name == "powder_carrier_01_vial_1"
    assert beaker_at_0.name == "solution_carrier_01_beaker_1"
