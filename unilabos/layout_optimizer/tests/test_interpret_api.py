"""Tests for /interpret and /interpret/schema API endpoints."""
import pytest
from fastapi.testclient import TestClient

from ..server import app

client = TestClient(app)


def test_interpret_reachable_by():
    resp = client.post("/interpret", json={
        "intents": [
            {
                "intent": "reachable_by",
                "params": {
                    "arm": "arm_slider",
                    "targets": ["opentrons_liquid_handler", "inheco_odtc_96xl"],
                },
                "description": "Arm must reach liquid handler and thermal cycler",
            }
        ]
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["constraints"]) == 2
    assert all(c["rule_name"] == "reachability" for c in data["constraints"])
    assert len(data["translations"]) == 1
    assert data["translations"][0]["source_intent"] == "reachable_by"
    assert len(data["errors"]) == 0


def test_interpret_pcr_workflow():
    """Full PCR: reachability + workflow_hint + close_together."""
    resp = client.post("/interpret", json={
        "intents": [
            {
                "intent": "reachable_by",
                "params": {
                    "arm": "arm_slider",
                    "targets": [
                        "opentrons_liquid_handler",
                        "inheco_odtc_96xl",
                        "agilent_plateloc",
                        "thermo_orbitor_rs2_hotel",
                    ],
                },
            },
            {
                "intent": "workflow_hint",
                "params": {
                    "workflow": "pcr",
                    "devices": [
                        "opentrons_liquid_handler",
                        "inheco_odtc_96xl",
                        "agilent_plateloc",
                        "thermo_orbitor_rs2_hotel",
                    ],
                },
            },
            {
                "intent": "close_together",
                "params": {
                    "devices": ["opentrons_liquid_handler", "inheco_odtc_96xl"],
                    "priority": "high",
                },
            },
        ]
    })
    assert resp.status_code == 200
    data = resp.json()
    # 4 reachability + 3 workflow + 1 close = 8
    assert len(data["constraints"]) == 8
    assert len(data["workflow_edges"]) == 3
    assert len(data["translations"]) == 3
    assert len(data["errors"]) == 0


def test_interpret_returns_errors_for_bad_intents():
    resp = client.post("/interpret", json={
        "intents": [
            {"intent": "reachable_by", "params": {}},
            {"intent": "nonexistent_intent"},
        ]
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["errors"]) == 2
    assert len(data["constraints"]) == 0


def test_interpret_empty_intents():
    resp = client.post("/interpret", json={"intents": []})
    assert resp.status_code == 200
    data = resp.json()
    assert data["constraints"] == []
    assert data["translations"] == []
    assert data["errors"] == []


def test_interpret_schema_returns_all_intents():
    resp = client.get("/interpret/schema")
    assert resp.status_code == 200
    data = resp.json()
    intents = data["intents"]
    expected = {
        "reachable_by", "close_together", "far_apart",
        "max_distance", "min_distance", "min_spacing",
        "workflow_hint", "face_outward", "face_inward", "align_cardinal",
    }
    assert set(intents.keys()) == expected


def test_interpret_constraints_passable_to_optimize():
    """Constraints from /interpret should be directly usable in /optimize."""
    # Step 1: interpret
    interpret_resp = client.post("/interpret", json={
        "intents": [
            {"intent": "close_together", "params": {"devices": ["dev_a", "dev_b"]}},
        ]
    })
    constraints = interpret_resp.json()["constraints"]

    # Step 2: pass to optimize (verify it accepts the format)
    optimize_resp = client.post("/optimize", json={
        "devices": [
            {"id": "dev_a", "name": "Device A", "size": [0.5, 0.4]},
            {"id": "dev_b", "name": "Device B", "size": [0.5, 0.4]},
        ],
        "lab": {"width": 4.0, "depth": 3.0},
        "constraints": constraints,
        "run_de": False,
    })
    assert optimize_resp.status_code == 200
    assert len(optimize_resp.json()["placements"]) == 2
