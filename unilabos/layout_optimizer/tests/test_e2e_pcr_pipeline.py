"""End-to-end pipeline test: intents → interpret → optimize → verify.

Tests each stage boundary independently so failures are easy to localize.
Uses real PCR workflow devices with footprints from the catalog.
"""
import math

import pytest
from fastapi.testclient import TestClient

from ..server import app

client = TestClient(app)

# -- Scene: 5 PCR devices the user has already placed in the scene --

PCR_DEVICES = [
    {"id": "thermo_orbitor_rs2_hotel", "name": "Plate Hotel", "device_type": "static"},
    {"id": "arm_slider", "name": "Robot Arm", "device_type": "articulation"},
    {"id": "opentrons_liquid_handler", "name": "Liquid Handler", "device_type": "static"},
    {"id": "agilent_plateloc", "name": "Plate Sealer", "device_type": "static"},
    {"id": "inheco_odtc_96xl", "name": "Thermal Cycler", "device_type": "static"},
]

PCR_LAB = {"width": 6.0, "depth": 4.0}

# -- Stage 1: simulated LLM output (what the LLM would produce from NL) --
# User said: "take plate from hotel, prepare sample in opentrons,
#             seal plate then pcr cycle, arm_slider handles transfers"

LLM_INTENTS = [
    {
        "intent": "reachable_by",
        "params": {
            "arm": "arm_slider",
            "targets": [
                "thermo_orbitor_rs2_hotel",
                "opentrons_liquid_handler",
                "agilent_plateloc",
                "inheco_odtc_96xl",
            ],
        },
        "description": "arm_slider must reach all workflow devices",
    },
    {
        "intent": "workflow_hint",
        "params": {
            "workflow": "pcr",
            "devices": [
                "thermo_orbitor_rs2_hotel",
                "opentrons_liquid_handler",
                "agilent_plateloc",
                "inheco_odtc_96xl",
            ],
        },
        "description": "PCR order: hotel → liquid handler → sealer → thermal cycler",
    },
    {
        "intent": "close_together",
        "params": {
            "devices": ["opentrons_liquid_handler", "agilent_plateloc"],
            "priority": "high",
        },
        "description": "Seal immediately after sample prep",
    },
    {
        "intent": "min_spacing",
        "params": {"min_gap": 0.15},
        "description": "Minimum 15cm gap for accessibility",
    },
]


class TestStage1Interpret:
    """Stage 1: /interpret translates intents → constraints."""

    def test_interpret_returns_correct_constraint_count(self):
        resp = client.post("/interpret", json={"intents": LLM_INTENTS})
        assert resp.status_code == 200
        data = resp.json()
        # 4 reachability + 3 workflow minimize + 1 close minimize + 1 min_spacing = 9
        assert len(data["constraints"]) == 9
        assert len(data["errors"]) == 0

    def test_interpret_has_translations_for_each_intent(self):
        resp = client.post("/interpret", json={"intents": LLM_INTENTS})
        data = resp.json()
        assert len(data["translations"]) == len(LLM_INTENTS)
        # 每个 translation 都有 explanation
        for t in data["translations"]:
            assert t["explanation"] != ""

    def test_interpret_extracts_workflow_edges(self):
        resp = client.post("/interpret", json={"intents": LLM_INTENTS})
        data = resp.json()
        assert len(data["workflow_edges"]) == 3
        assert ["thermo_orbitor_rs2_hotel", "opentrons_liquid_handler"] in data["workflow_edges"]
        assert ["opentrons_liquid_handler", "agilent_plateloc"] in data["workflow_edges"]
        assert ["agilent_plateloc", "inheco_odtc_96xl"] in data["workflow_edges"]

    def test_interpret_constraint_types_correct(self):
        resp = client.post("/interpret", json={"intents": LLM_INTENTS})
        data = resp.json()
        constraints = data["constraints"]
        by_rule = {}
        for c in constraints:
            by_rule.setdefault(c["rule_name"], []).append(c)
        assert len(by_rule["reachability"]) == 4
        assert all(c["type"] == "hard" for c in by_rule["reachability"])
        assert len(by_rule["minimize_distance"]) == 4  # 3 workflow + 1 close
        assert all(c["type"] == "soft" for c in by_rule["minimize_distance"])
        assert len(by_rule["min_spacing"]) == 1
        assert by_rule["min_spacing"][0]["type"] == "hard"


class TestStage2Optimize:
    """Stage 2: pipe /interpret output into /optimize → placements."""

    @pytest.fixture()
    def interpret_result(self):
        resp = client.post("/interpret", json={"intents": LLM_INTENTS})
        return resp.json()

    def test_optimize_accepts_interpret_output(self, interpret_result):
        """Constraints + workflow_edges from /interpret are valid /optimize input."""
        resp = client.post("/optimize", json={
            "devices": PCR_DEVICES,
            "lab": PCR_LAB,
            "constraints": interpret_result["constraints"],
            "workflow_edges": interpret_result["workflow_edges"],
            "run_de": False,  # seeder only — fast
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["placements"]) == 5
        assert data["success"] is True

    def test_optimize_with_de(self, interpret_result):
        """Full DE optimization completes without error."""
        resp = client.post("/optimize", json={
            "devices": PCR_DEVICES,
            "lab": PCR_LAB,
            "constraints": interpret_result["constraints"],
            "workflow_edges": interpret_result["workflow_edges"],
            "run_de": True,
            "maxiter": 50,  # reduced for test speed
            "seed": 42,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["placements"]) == 5
        assert data["de_ran"] is True


class TestStage3VerifyPlacements:
    """Stage 3: verify optimized placements satisfy constraint intent."""

    @pytest.fixture()
    def placements(self):
        # Full pipeline: interpret → optimize (with DE), all intents including reachability
        # MockReachabilityChecker uses large fallback reach for unknown arms like arm_slider
        interpret_resp = client.post("/interpret", json={"intents": LLM_INTENTS})
        interpret_data = interpret_resp.json()

        optimize_resp = client.post("/optimize", json={
            "devices": PCR_DEVICES,
            "lab": PCR_LAB,
            "constraints": interpret_data["constraints"],
            "workflow_edges": interpret_data["workflow_edges"],
            "run_de": True,
            "maxiter": 50,
            "seed": 42,
        })
        return {p["device_id"]: p for p in optimize_resp.json()["placements"]}

    def test_all_devices_placed(self, placements):
        expected_ids = {d["id"] for d in PCR_DEVICES}
        assert set(placements.keys()) == expected_ids

    def test_all_within_lab_bounds(self, placements):
        for dev_id, p in placements.items():
            assert 0 <= p["position"]["x"] <= PCR_LAB["width"], f"{dev_id} x out of bounds"
            assert 0 <= p["position"]["y"] <= PCR_LAB["depth"], f"{dev_id} y out of bounds"

    def test_no_hard_constraint_violation(self):
        """Full pipeline with all intents including reachability converges cleanly.

        MockReachabilityChecker uses large fallback reach for unknown arms,
        so arm_slider reachability constraints are satisfied in mock mode.
        When real ROS checkers replace mock, this test validates the same pipeline.
        """
        interpret_data = client.post("/interpret", json={"intents": LLM_INTENTS}).json()

        optimize_resp = client.post("/optimize", json={
            "devices": PCR_DEVICES,
            "lab": PCR_LAB,
            "constraints": interpret_data["constraints"],
            "workflow_edges": interpret_data["workflow_edges"],
            "run_de": True,
            "maxiter": 50,
            "seed": 42,
        })
        data = optimize_resp.json()
        assert data["success"] is True
        assert not math.isinf(data["cost"])

    def test_workflow_neighbors_closer_than_diagonal(self, placements):
        """Workflow-adjacent devices should be closer than lab diagonal (basic sanity)."""
        max_diagonal = math.sqrt(PCR_LAB["width"] ** 2 + PCR_LAB["depth"] ** 2)
        workflow_pairs = [
            ("thermo_orbitor_rs2_hotel", "opentrons_liquid_handler"),
            ("opentrons_liquid_handler", "agilent_plateloc"),
            ("agilent_plateloc", "inheco_odtc_96xl"),
        ]
        for a_id, b_id in workflow_pairs:
            a, b = placements[a_id], placements[b_id]
            dist = math.sqrt(
                (a["position"]["x"] - b["position"]["x"]) ** 2
                + (a["position"]["y"] - b["position"]["y"]) ** 2
            )
            # 应该远小于对角线（workflow minimize_distance 约束）
            assert dist < max_diagonal * 0.8, (
                f"Workflow pair {a_id}↔{b_id} distance {dist:.2f}m "
                f"exceeds 80% of diagonal {max_diagonal:.2f}m"
            )


class TestPipelineStageIsolation:
    """Verify each stage's output format is valid input for the next stage."""

    def test_interpret_output_schema_matches_optimize_input(self):
        """constraints from /interpret have all fields /optimize expects."""
        resp = client.post("/interpret", json={"intents": LLM_INTENTS})
        data = resp.json()

        for c in data["constraints"]:
            assert "type" in c
            assert "rule_name" in c
            assert "params" in c
            assert "weight" in c
            assert c["type"] in ("hard", "soft")

        for edge in data["workflow_edges"]:
            assert isinstance(edge, list)
            assert len(edge) == 2

    def test_round_trip_no_data_loss(self):
        """Interpret → optimize → check that all device IDs survive the pipeline."""
        interpret_resp = client.post("/interpret", json={"intents": LLM_INTENTS})
        interpret_data = interpret_resp.json()

        optimize_resp = client.post("/optimize", json={
            "devices": PCR_DEVICES,
            "lab": PCR_LAB,
            "constraints": interpret_data["constraints"],
            "workflow_edges": interpret_data["workflow_edges"],
            "run_de": False,
        })
        result_ids = {p["device_id"] for p in optimize_resp.json()["placements"]}
        input_ids = {d["id"] for d in PCR_DEVICES}
        assert result_ids == input_ids
