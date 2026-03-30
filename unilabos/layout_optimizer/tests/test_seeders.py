"""Tests for the force-directed seeder engine."""
import math
import pytest
from ..seeders import SeederParams, PRESETS, seed_layout
from ..models import Device, Lab, Placement


class TestSeederParams:
    def test_presets_exist(self):
        assert "compact_outward" in PRESETS
        assert "spread_inward" in PRESETS
        assert "row_fallback" in PRESETS

    def test_compact_has_negative_boundary(self):
        assert PRESETS["compact_outward"].boundary_attraction < 0

    def test_spread_has_positive_boundary(self):
        assert PRESETS["spread_inward"].boundary_attraction > 0


class TestSeedLayout:
    """seed_layout must return valid placements: within bounds, one per device."""

    def _make_devices(self, n: int) -> list[Device]:
        return [Device(id=f"d{i}", name=f"Device {i}", bbox=(0.6, 0.4)) for i in range(n)]

    def test_returns_one_placement_per_device(self):
        devices = self._make_devices(5)
        lab = Lab(width=5.0, depth=4.0)
        result = seed_layout(devices, lab, PRESETS["compact_outward"])
        assert len(result) == 5
        ids = {p.device_id for p in result}
        assert ids == {f"d{i}" for i in range(5)}

    def test_placements_within_bounds(self):
        devices = self._make_devices(5)
        lab = Lab(width=5.0, depth=4.0)
        for preset_name in ["compact_outward", "spread_inward"]:
            result = seed_layout(devices, lab, PRESETS[preset_name])
            for p in result:
                assert 0 <= p.x <= lab.width, f"{preset_name}: x={p.x} out of bounds"
                assert 0 <= p.y <= lab.depth, f"{preset_name}: y={p.y} out of bounds"

    def test_empty_devices(self):
        result = seed_layout([], Lab(width=5, depth=4), PRESETS["compact_outward"])
        assert result == []

    def test_single_device(self):
        devices = self._make_devices(1)
        lab = Lab(width=5.0, depth=4.0)
        result = seed_layout(devices, lab, PRESETS["compact_outward"])
        assert len(result) == 1
        assert 0 <= result[0].x <= lab.width
        assert 0 <= result[0].y <= lab.depth

    def test_row_fallback_delegates(self):
        """row_fallback preset uses generate_fallback, not force engine."""
        devices = self._make_devices(3)
        lab = Lab(width=5.0, depth=4.0)
        # row_fallback is None in PRESETS; seed_layout detects and delegates
        result = seed_layout(devices, lab, None)  # None = row_fallback
        assert len(result) == 3

    def test_lab_too_small_returns_results_not_crash(self):
        """When space is insufficient, seeder still returns placements (may have collisions)."""
        devices = [Device(id=f"d{i}", name=f"D{i}", bbox=(1.0, 1.0)) for i in range(20)]
        lab = Lab(width=2.0, depth=2.0)  # Way too small for 20 1m×1m devices
        result = seed_layout(devices, lab, PRESETS["compact_outward"])
        assert len(result) == 20  # All placed, even if overlapping
        for p in result:
            assert 0 <= p.x <= lab.width
            assert 0 <= p.y <= lab.depth

    def test_compact_clusters_toward_center(self):
        """compact_outward should place devices closer to center than spread_inward."""
        devices = self._make_devices(4)
        lab = Lab(width=8.0, depth=8.0)
        center_x, center_y = lab.width / 2, lab.depth / 2

        compact = seed_layout(devices, lab, PRESETS["compact_outward"])
        spread = seed_layout(devices, lab, PRESETS["spread_inward"])

        avg_dist_compact = sum(
            math.sqrt((p.x - center_x)**2 + (p.y - center_y)**2) for p in compact
        ) / len(compact)
        avg_dist_spread = sum(
            math.sqrt((p.x - center_x)**2 + (p.y - center_y)**2) for p in spread
        ) / len(spread)

        assert avg_dist_compact < avg_dist_spread


class TestOrientation:
    """Orientation modes should set theta based on position relative to center."""

    def test_outward_orientation_sets_theta(self):
        """compact_outward: devices should have non-zero theta."""
        devices = [
            Device(id="a", name="A", bbox=(0.6, 0.4)),
            Device(id="b", name="B", bbox=(0.6, 0.4)),
        ]
        lab = Lab(width=5.0, depth=4.0)
        result = seed_layout(devices, lab, PRESETS["compact_outward"])
        thetas = [p.theta for p in result]
        assert any(t != 0.0 for t in thetas) or len(devices) == 1

    def test_none_orientation_keeps_zero(self):
        """orientation_mode='none': all thetas stay 0."""
        devices = [Device(id="a", name="A", bbox=(0.6, 0.4))]
        lab = Lab(width=5.0, depth=4.0)
        params = SeederParams(boundary_attraction=0.0, orientation_mode="none")
        result = seed_layout(devices, lab, params)
        assert result[0].theta == 0.0
