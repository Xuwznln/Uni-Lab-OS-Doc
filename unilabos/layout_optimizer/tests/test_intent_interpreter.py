"""Intent interpreter tests — PCR workflow devices."""
import pytest

from ..intent_interpreter import interpret_intents
from ..models import Intent


# --- reachable_by ---

def test_reachable_by_generates_hard_reachability():
    intents = [Intent(
        intent="reachable_by",
        params={"arm": "arm_slider", "targets": ["opentrons_liquid_handler", "inheco_odtc_96xl"]},
        description="Robot arm must reach liquid handler and thermal cycler",
    )]
    result = interpret_intents(intents)
    assert len(result.constraints) == 2
    assert all(c.rule_name == "reachability" for c in result.constraints)
    assert all(c.type == "hard" for c in result.constraints)
    assert result.constraints[0].params == {"arm_id": "arm_slider", "target_device_id": "opentrons_liquid_handler"}
    assert result.constraints[1].params == {"arm_id": "arm_slider", "target_device_id": "inheco_odtc_96xl"}
    assert len(result.translations) == 1
    assert len(result.translations[0]["generated_constraints"]) == 2


def test_reachable_by_missing_arm():
    result = interpret_intents([Intent(intent="reachable_by", params={"targets": ["a"]})])
    assert len(result.constraints) == 0
    assert len(result.errors) == 1
    assert "arm" in result.errors[0].lower()


def test_reachable_by_empty_targets():
    result = interpret_intents([Intent(intent="reachable_by", params={"arm": "arm_slider", "targets": []})])
    assert len(result.constraints) == 0
    assert len(result.errors) == 1
    assert "targets" in result.errors[0].lower()


# --- close_together ---

def test_close_together_generates_minimize_distance():
    intents = [Intent(intent="close_together", params={
        "devices": ["opentrons_liquid_handler", "inheco_odtc_96xl", "agilent_plateloc"],
    })]
    result = interpret_intents(intents)
    assert len(result.constraints) == 3  # C(3,2) = 3 pairs
    assert all(c.rule_name == "minimize_distance" for c in result.constraints)
    assert all(c.type == "soft" for c in result.constraints)


def test_close_together_priority_scales_weight():
    low = interpret_intents([Intent(intent="close_together", params={"devices": ["a", "b"], "priority": "low"})])
    high = interpret_intents([Intent(intent="close_together", params={"devices": ["a", "b"], "priority": "high"})])
    assert high.constraints[0].weight > low.constraints[0].weight


def test_close_together_single_device_error():
    result = interpret_intents([Intent(intent="close_together", params={"devices": ["a"]})])
    assert len(result.errors) == 1


# --- far_apart ---

def test_far_apart_generates_maximize_distance():
    result = interpret_intents([Intent(intent="far_apart", params={
        "devices": ["inheco_odtc_96xl", "thermo_orbitor_rs2_hotel"],
    })])
    assert len(result.constraints) == 1
    assert result.constraints[0].rule_name == "maximize_distance"


# --- max_distance / min_distance ---

def test_max_distance_generates_distance_less_than():
    result = interpret_intents([Intent(intent="max_distance", params={
        "device_a": "opentrons_liquid_handler", "device_b": "inheco_odtc_96xl", "distance": 1.5,
    })])
    assert len(result.constraints) == 1
    c = result.constraints[0]
    assert c.rule_name == "distance_less_than"
    assert c.type == "hard"
    assert c.params["distance"] == 1.5


def test_min_distance_generates_distance_greater_than():
    result = interpret_intents([Intent(intent="min_distance", params={
        "device_a": "inheco_odtc_96xl", "device_b": "thermo_orbitor_rs2_hotel", "distance": 2.0,
    })])
    c = result.constraints[0]
    assert c.rule_name == "distance_greater_than"
    assert c.type == "hard"
    assert c.params["distance"] == 2.0


def test_max_distance_zero_is_valid():
    """distance=0 is falsy but valid — must not be rejected."""
    result = interpret_intents([Intent(intent="max_distance", params={
        "device_a": "a", "device_b": "b", "distance": 0,
    })])
    assert len(result.constraints) == 1
    assert len(result.errors) == 0


def test_max_distance_missing_param():
    result = interpret_intents([Intent(intent="max_distance", params={"device_a": "a"})])
    assert len(result.errors) == 1
    assert len(result.constraints) == 0


# --- orientation ---

def test_face_outward():
    result = interpret_intents([Intent(intent="face_outward")])
    assert result.constraints[0].rule_name == "prefer_orientation_mode"
    assert result.constraints[0].params["mode"] == "outward"


def test_face_inward():
    result = interpret_intents([Intent(intent="face_inward")])
    assert result.constraints[0].params["mode"] == "inward"


def test_align_cardinal():
    result = interpret_intents([Intent(intent="align_cardinal")])
    assert result.constraints[0].rule_name == "prefer_aligned"


# --- min_spacing ---

def test_min_spacing():
    result = interpret_intents([Intent(intent="min_spacing", params={"min_gap": 0.3})])
    c = result.constraints[0]
    assert c.rule_name == "min_spacing"
    assert c.type == "hard"
    assert c.params["min_gap"] == 0.3


# --- workflow_hint (PCR scenario) ---

def test_workflow_hint_pcr():
    """PCR workflow: pipette → thermal cycler → plate sealer → storage."""
    intents = [Intent(
        intent="workflow_hint",
        params={
            "workflow": "pcr",
            "devices": [
                "opentrons_liquid_handler",
                "inheco_odtc_96xl",
                "agilent_plateloc",
                "thermo_orbitor_rs2_hotel",
            ],
        },
    )]
    result = interpret_intents(intents)
    assert len(result.constraints) == 3  # 4 devices → 3 consecutive pairs
    assert all(c.rule_name == "minimize_distance" for c in result.constraints)
    assert len(result.workflow_edges) == 3
    assert ["opentrons_liquid_handler", "inheco_odtc_96xl"] in result.workflow_edges
    assert result.translations[0]["confidence"] == "low"


def test_workflow_hint_single_device_error():
    result = interpret_intents([Intent(intent="workflow_hint", params={"workflow": "test", "devices": ["a"]})])
    assert len(result.errors) == 1


# --- unknown intent ---

def test_unknown_intent():
    result = interpret_intents([Intent(intent="nonexistent")])
    assert len(result.constraints) == 0
    assert len(result.errors) == 1
    assert "nonexistent" in result.errors[0]


# --- multi-intent combination ---

def test_full_pcr_scenario():
    """Arm reachability + close together for full PCR setup."""
    intents = [
        Intent(intent="reachable_by", params={
            "arm": "arm_slider",
            "targets": [
                "opentrons_liquid_handler", "inheco_odtc_96xl",
                "agilent_plateloc", "thermo_orbitor_rs2_hotel",
            ],
        }),
        Intent(intent="close_together", params={
            "devices": ["opentrons_liquid_handler", "inheco_odtc_96xl"],
            "priority": "high",
        }),
    ]
    result = interpret_intents(intents)
    assert len(result.constraints) == 5  # 4 reachability + 1 minimize_distance
    assert len(result.translations) == 2
    assert len(result.errors) == 0
