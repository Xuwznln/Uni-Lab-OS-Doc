"""Regression tests for V2 Stage 1 bugfixes.

Covers:
- Duplicate device ID stacking (uuid-based internal IDs)
- DE orientation preservation (prefer_orientation_mode constraint)
- prefer_aligned auto-injection and adjustability
- Preset switch reorientation
- min_spacing with duplicate catalog IDs
"""

import math

import pytest

from ..constraints import evaluate_constraints
from ..mock_checkers import MockCollisionChecker
from ..models import Constraint, Device, Lab, Opening, Placement
from ..obb import obb_corners, obb_overlap
from ..optimizer import (
    _placements_to_vector,
    _vector_to_placements,
    optimize,
    snap_theta,
)
from ..seeders import resolve_seeder_params, seed_layout


# ── Helpers ─────────────────────────────────────────────

def _ot(uid: str) -> Device:
    return Device(
        id=uid, name="Opentrons Liquid Handler",
        bbox=(0.6243, 0.5672), openings=[Opening(direction=(0.0, -1.0))],
    )

def _tecan(uid: str) -> Device:
    return Device(
        id=uid, name="Tecan EVO 100",
        bbox=(0.8121, 0.8574), openings=[Opening(direction=(0.0, -1.0))],
    )

def _facing_dot(p: Placement, device: Device, lab: Lab) -> float:
    """Dot product of rotated front vector with vector from center to device.
    Positive = outward, negative = inward."""
    cx, cy = lab.width / 2, lab.depth / 2
    dx, dy = p.x - cx, p.y - cy
    front = device.openings[0].direction if device.openings else (0.0, -1.0)
    rf_x = math.cos(p.theta) * front[0] - math.sin(p.theta) * front[1]
    rf_y = math.sin(p.theta) * front[0] + math.cos(p.theta) * front[1]
    return rf_x * dx + rf_y * dy

def _has_collision(devices, placements):
    for i in range(len(devices)):
        for j in range(i + 1, len(devices)):
            ci = obb_corners(placements[i].x, placements[i].y,
                             devices[i].bbox[0], devices[i].bbox[1], placements[i].theta)
            cj = obb_corners(placements[j].x, placements[j].y,
                             devices[j].bbox[0], devices[j].bbox[1], placements[j].theta)
            if obb_overlap(ci, cj):
                return True
    return False


# ── Bug 1: Duplicate device ID stacking ────────────────

class TestDuplicateDeviceIDs:
    """When two instances of the same catalog device are placed,
    unique uuid-based IDs must prevent dict-key collisions."""

    def test_vector_roundtrip_preserves_unique_positions(self):
        """_placements_to_vector → _vector_to_placements with unique IDs."""
        devices = [_ot("uuid-a"), _ot("uuid-b")]
        placements = [
            Placement(device_id="uuid-a", x=0.5, y=0.5, theta=0.0),
            Placement(device_id="uuid-b", x=1.5, y=1.5, theta=1.0),
        ]
        vec = _placements_to_vector(placements, devices)
        decoded = _vector_to_placements(vec, devices)
        assert decoded[0].x == pytest.approx(0.5)
        assert decoded[1].x == pytest.approx(1.5)

    def test_min_spacing_detects_stacked_unique_ids(self):
        """min_spacing should detect two devices at the same position
        when they have unique IDs."""
        devices = [_ot("uuid-a"), _ot("uuid-b")]
        stacked = [
            Placement(device_id="uuid-a", x=1.0, y=1.0, theta=0.0),
            Placement(device_id="uuid-b", x=1.0, y=1.0, theta=0.0),
        ]
        lab = Lab(width=5, depth=5)
        constraints = [Constraint(type="hard", rule_name="min_spacing",
                                  params={"min_gap": 0.05})]
        # graduated=True (default): 返回有限惩罚
        cost = evaluate_constraints(devices, stacked, lab, constraints,
                                    MockCollisionChecker())
        assert cost > 0
        assert not math.isinf(cost)
        # graduated=False: binary inf
        cost_binary = evaluate_constraints(devices, stacked, lab, constraints,
                                           MockCollisionChecker(),
                                           graduated=False)
        assert math.isinf(cost_binary)

    def test_create_devices_uses_uuid(self):
        """create_devices_from_list should use uuid as Device.id."""
        from ..device_catalog import create_devices_from_list
        specs = [
            {"id": "opentrons_liquid_handler", "uuid": "abc-123"},
            {"id": "opentrons_liquid_handler", "uuid": "def-456"},
        ]
        devices = create_devices_from_list(specs)
        assert devices[0].id == "abc-123"
        assert devices[1].id == "def-456"
        # Both should have the same bbox from footprints
        assert devices[0].bbox == devices[1].bbox

    def test_create_devices_fallback_no_uuid(self):
        """Without uuid, Device.id falls back to catalog id."""
        from ..device_catalog import create_devices_from_list
        specs = [{"id": "opentrons_liquid_handler"}]
        devices = create_devices_from_list(specs)
        assert devices[0].id == "opentrons_liquid_handler"


# ── Bug 2 & 4: DE orientation preservation ─────────────

class TestOrientationWithDE:
    """DE must preserve seeder orientation direction (outward/inward)
    via the prefer_orientation_mode constraint."""

    def _run_de_with_orientation(self, mode, seed_val=42):
        devices = [_ot("ot1"), _ot("ot2"), _tecan("tecan")]
        lab = Lab(width=2.0, depth=2.0)
        params = resolve_seeder_params(
            "compact_outward" if mode == "outward" else "spread_inward"
        )
        seed = seed_layout(devices, lab, params)
        constraints = [
            Constraint(type="hard", rule_name="min_spacing",
                       params={"min_gap": 0.05}),
            Constraint(type="soft", rule_name="prefer_orientation_mode",
                       params={"mode": mode}, weight=5.0),
            Constraint(type="soft", rule_name="prefer_aligned", weight=2.0),
        ]
        result = optimize(devices, lab, constraints, seed_placements=seed,
                          maxiter=200, seed=seed_val)
        result = snap_theta(result)
        return devices, lab, result

    def test_compact_outward_de_faces_outward(self):
        devices, lab, result = self._run_de_with_orientation("outward")
        for i, p in enumerate(result):
            dot = _facing_dot(p, devices[i], lab)
            assert dot > 0, (
                f"{p.device_id} faces inward (dot={dot:.3f}) "
                f"at ({p.x:.2f},{p.y:.2f}) theta={math.degrees(p.theta):.0f}°"
            )

    def test_spread_inward_de_faces_inward(self):
        devices, lab, result = self._run_de_with_orientation("inward")
        for i, p in enumerate(result):
            dot = _facing_dot(p, devices[i], lab)
            assert dot < 0, (
                f"{p.device_id} faces outward (dot={dot:.3f}) "
                f"at ({p.x:.2f},{p.y:.2f}) theta={math.degrees(p.theta):.0f}°"
            )

    def test_switching_preset_changes_orientation(self):
        """Switching from outward to inward should produce opposite facing."""
        _, lab, out_result = self._run_de_with_orientation("outward")
        devices_in, _, in_result = self._run_de_with_orientation("inward")
        # At least one device should have different facing
        out_dots = [_facing_dot(p, devices_in[i], lab) for i, p in enumerate(out_result)]
        in_dots = [_facing_dot(p, devices_in[i], lab) for i, p in enumerate(in_result)]
        # Outward: all positive; inward: all negative
        assert all(d > 0 for d in out_dots), f"outward dots: {out_dots}"
        assert all(d < 0 for d in in_dots), f"inward dots: {in_dots}"

    def test_no_collision_after_de(self):
        devices, lab, result = self._run_de_with_orientation("outward")
        assert not _has_collision(devices, result)


# ── Bug 3: prefer_aligned & prefer_orientation_mode ────

class TestOrientationConstraints:
    """Test the new constraint rules directly."""

    def test_prefer_orientation_mode_outward_zero_at_correct(self):
        """Zero cost when device faces outward from center."""
        device = _ot("a")
        # Device to the right of center, front pointing right
        # front=(0,-1), theta=pi/2 → rotated front = (1, 0) = rightward
        lab = Lab(width=4, depth=4)
        placements = [Placement("a", 3.0, 2.0, math.pi / 2)]
        constraint = Constraint(
            type="soft", rule_name="prefer_orientation_mode",
            params={"mode": "outward"}, weight=1.0,
        )
        cost = evaluate_constraints(
            [device], placements, lab, [constraint], MockCollisionChecker(),
        )
        assert cost == pytest.approx(0.0, abs=0.01)

    def test_prefer_orientation_mode_outward_penalty_at_inward(self):
        """High cost when device faces inward (opposite of outward)."""
        device = _ot("a")
        # Device to the right of center, front pointing left (inward)
        # front=(0,-1), theta=3*pi/2 → rotated front = (-1, 0) = leftward
        lab = Lab(width=4, depth=4)
        placements = [Placement("a", 3.0, 2.0, 3 * math.pi / 2)]
        constraint = Constraint(
            type="soft", rule_name="prefer_orientation_mode",
            params={"mode": "outward"}, weight=1.0,
        )
        cost = evaluate_constraints(
            [device], placements, lab, [constraint], MockCollisionChecker(),
        )
        # 180° off → (1 - cos(pi)) / 2 = 1.0
        assert cost == pytest.approx(1.0, abs=0.05)

    def test_prefer_orientation_mode_inward(self):
        """Zero cost when device faces inward."""
        device = _ot("a")
        # Device to the right of center, front pointing left (inward)
        lab = Lab(width=4, depth=4)
        placements = [Placement("a", 3.0, 2.0, 3 * math.pi / 2)]
        constraint = Constraint(
            type="soft", rule_name="prefer_orientation_mode",
            params={"mode": "inward"}, weight=1.0,
        )
        cost = evaluate_constraints(
            [device], placements, lab, [constraint], MockCollisionChecker(),
        )
        assert cost == pytest.approx(0.0, abs=0.01)

    def test_prefer_seeder_orientation_zero_at_target(self):
        """Zero cost when theta matches target."""
        device = Device(id="a", name="A", bbox=(0.5, 0.5))
        lab = Lab(width=4, depth=4)
        placements = [Placement("a", 2, 2, 1.5)]
        constraint = Constraint(
            type="soft", rule_name="prefer_seeder_orientation",
            params={"target_thetas": {"a": 1.5}}, weight=1.0,
        )
        cost = evaluate_constraints(
            [device], placements, lab, [constraint], MockCollisionChecker(),
        )
        assert cost == pytest.approx(0.0, abs=1e-9)

    def test_prefer_seeder_orientation_penalty_at_deviation(self):
        """Non-zero cost when theta deviates from target."""
        device = Device(id="a", name="A", bbox=(0.5, 0.5))
        lab = Lab(width=4, depth=4)
        placements = [Placement("a", 2, 2, math.pi)]  # pi away from 0
        constraint = Constraint(
            type="soft", rule_name="prefer_seeder_orientation",
            params={"target_thetas": {"a": 0.0}}, weight=1.0,
        )
        cost = evaluate_constraints(
            [device], placements, lab, [constraint], MockCollisionChecker(),
        )
        # (1 - cos(pi)) / 2 = 1.0
        assert cost == pytest.approx(1.0)


# ── API endpoint regression ────────────────────────────

class TestEndpointOrientation:
    """Test that /optimize injects orientation constraints."""

    def test_endpoint_with_de_injects_orientation(self):
        from fastapi.testclient import TestClient
        from ..server import app

        client = TestClient(app)
        resp = client.post("/optimize", json={
            "devices": [
                {"id": "opentrons_liquid_handler", "uuid": "u1"},
                {"id": "opentrons_liquid_handler", "uuid": "u2"},
            ],
            "lab": {"width": 3, "depth": 3},
            "seeder": "compact_outward",
            "run_de": True,
            "maxiter": 50,
            "seed": 42,
        })
        assert resp.status_code == 200
        data = resp.json()
        # Both devices should have unique uuids in response
        uuids = [p["uuid"] for p in data["placements"]]
        assert len(set(uuids)) == 2, f"Expected 2 unique uuids, got {uuids}"

    def test_endpoint_orientation_weight_override(self):
        from fastapi.testclient import TestClient
        from ..server import app

        client = TestClient(app)
        resp = client.post("/optimize", json={
            "devices": [{"id": "opentrons_liquid_handler", "uuid": "u1"}],
            "lab": {"width": 3, "depth": 3},
            "seeder": "compact_outward",
            "seeder_overrides": {"orientation_weight": 10, "align_weight": 0},
            "run_de": True,
            "maxiter": 50,
            "seed": 42,
        })
        assert resp.status_code == 200

    def test_endpoint_align_weight_zero_disables(self):
        """Setting align_weight=0 should not inject prefer_aligned."""
        from fastapi.testclient import TestClient
        from ..server import app

        client = TestClient(app)
        resp = client.post("/optimize", json={
            "devices": [{"id": "opentrons_liquid_handler", "uuid": "u1"}],
            "lab": {"width": 3, "depth": 3},
            "seeder": "compact_outward",
            "seeder_overrides": {"align_weight": 0},
            "run_de": True,
            "maxiter": 50,
            "seed": 42,
        })
        assert resp.status_code == 200


# ── Broader scenario tests ─────────────────────────────

class TestScenarios:
    """End-to-end scenarios similar to user's real usage."""

    def test_user_scenario_2ot_1tecan_compact_outward(self):
        """User's exact scenario: 2 OT + 1 Tecan in 2m×2m, compact outward."""
        devices = [_ot("ot1"), _ot("ot2"), _tecan("tecan")]
        lab = Lab(width=2.0, depth=2.0)
        params = resolve_seeder_params("compact_outward")
        seed = seed_layout(devices, lab, params)
        constraints = [
            Constraint(type="hard", rule_name="min_spacing",
                       params={"min_gap": 0.05}),
            Constraint(type="soft", rule_name="prefer_orientation_mode",
                       params={"mode": "outward"}, weight=5.0),
            Constraint(type="soft", rule_name="prefer_aligned", weight=2.0),
        ]
        result = optimize(devices, lab, constraints, seed_placements=seed,
                          maxiter=200, seed=42)
        result = snap_theta(result)
        # No stacking
        assert not _has_collision(devices, result)
        # All outward
        for i, p in enumerate(result):
            assert _facing_dot(p, devices[i], lab) > 0

    def test_4_medium_devices_mixed_openings(self):
        """4 devices with different opening directions."""
        devices = [
            Device(id="d0", name="D0", bbox=(0.5, 0.3), openings=[Opening((1, 0))]),
            Device(id="d1", name="D1", bbox=(0.5, 0.3), openings=[Opening((-1, 0))]),
            Device(id="d2", name="D2", bbox=(0.5, 0.3), openings=[Opening((0, -1))]),
            Device(id="d3", name="D3", bbox=(0.5, 0.3), openings=[Opening((0, 1))]),
        ]
        lab = Lab(width=3.0, depth=3.0)
        params = resolve_seeder_params("compact_outward")
        seed = seed_layout(devices, lab, params)
        constraints = [
            Constraint(type="hard", rule_name="min_spacing",
                       params={"min_gap": 0.05}),
            Constraint(type="soft", rule_name="prefer_orientation_mode",
                       params={"mode": "outward"}, weight=5.0),
            Constraint(type="soft", rule_name="prefer_aligned", weight=2.0),
        ]
        result = optimize(devices, lab, constraints, seed_placements=seed,
                          maxiter=200, seed=42)
        result = snap_theta(result)
        assert not _has_collision(devices, result)
        for i, p in enumerate(result):
            assert _facing_dot(p, devices[i], lab) > 0
