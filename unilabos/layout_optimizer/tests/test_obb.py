"""Tests for OBB (Oriented Bounding Box) geometry utilities."""
import math
import pytest
from ..obb import obb_corners, obb_overlap, obb_min_distance, segment_obb_intersection_length


class TestObbCorners:
    """obb_corners(cx, cy, w, h, theta) → 4 corner points of the rotated rectangle."""

    def test_no_rotation(self):
        """Axis-aligned box at origin: corners at ±half extents."""
        corners = obb_corners(0, 0, 2.0, 1.0, 0.0)
        assert len(corners) == 4
        xs = sorted(c[0] for c in corners)
        ys = sorted(c[1] for c in corners)
        assert xs == pytest.approx([-1.0, -1.0, 1.0, 1.0])
        assert ys == pytest.approx([-0.5, -0.5, 0.5, 0.5])

    def test_90_degree_rotation(self):
        """90° rotation swaps width and height extents."""
        corners = obb_corners(0, 0, 2.0, 1.0, math.pi / 2)
        xs = sorted(c[0] for c in corners)
        ys = sorted(c[1] for c in corners)
        assert xs == pytest.approx([-0.5, -0.5, 0.5, 0.5])
        assert ys == pytest.approx([-1.0, -1.0, 1.0, 1.0])

    def test_offset_center(self):
        """Corners shift by (cx, cy)."""
        corners = obb_corners(3.0, 2.0, 2.0, 1.0, 0.0)
        xs = sorted(c[0] for c in corners)
        ys = sorted(c[1] for c in corners)
        assert xs == pytest.approx([2.0, 2.0, 4.0, 4.0])
        assert ys == pytest.approx([1.5, 1.5, 2.5, 2.5])

    def test_45_degree_rotation(self):
        """45° rotation: corners on diagonals."""
        corners = obb_corners(0, 0, 2.0, 2.0, math.pi / 4)
        for cx, cy in corners:
            dist = math.sqrt(cx**2 + cy**2)
            assert dist == pytest.approx(math.sqrt(2), abs=1e-9)


class TestObbOverlap:
    """obb_overlap(corners_a, corners_b) → True if the two OBBs overlap."""

    def test_separated_boxes(self):
        """Two boxes far apart: no overlap."""
        a = obb_corners(0, 0, 1.0, 1.0, 0.0)
        b = obb_corners(5, 0, 1.0, 1.0, 0.0)
        assert obb_overlap(a, b) is False

    def test_overlapping_boxes(self):
        """Two boxes sharing space: overlap."""
        a = obb_corners(0, 0, 2.0, 2.0, 0.0)
        b = obb_corners(1, 0, 2.0, 2.0, 0.0)
        assert obb_overlap(a, b) is True

    def test_touching_edges_no_overlap(self):
        """Boxes touching at edge: no overlap (strict <, not <=)."""
        a = obb_corners(0, 0, 2.0, 2.0, 0.0)
        b = obb_corners(2.0, 0, 2.0, 2.0, 0.0)
        assert obb_overlap(a, b) is False

    def test_rotated_overlap(self):
        """One box rotated 45°, overlapping the other."""
        a = obb_corners(0, 0, 2.0, 2.0, 0.0)
        b = obb_corners(1.0, 1.0, 2.0, 2.0, math.pi / 4)
        assert obb_overlap(a, b) is True

    def test_rotated_no_overlap(self):
        """One box rotated 45°, separated from the other."""
        a = obb_corners(0, 0, 1.0, 1.0, 0.0)
        b = obb_corners(3, 0, 1.0, 1.0, math.pi / 4)
        assert obb_overlap(a, b) is False

    def test_identical_boxes(self):
        """Same position and size: overlap."""
        a = obb_corners(1, 1, 1.0, 1.0, 0.0)
        b = obb_corners(1, 1, 1.0, 1.0, 0.0)
        assert obb_overlap(a, b) is True


class TestObbMinDistance:
    """obb_min_distance(corners_a, corners_b) → minimum edge-to-edge distance."""

    def test_overlapping_returns_zero(self):
        """Overlapping boxes: distance = 0."""
        a = obb_corners(0, 0, 2.0, 2.0, 0.0)
        b = obb_corners(1, 0, 2.0, 2.0, 0.0)
        assert obb_min_distance(a, b) == pytest.approx(0.0)

    def test_separated_axis_aligned(self):
        """Two axis-aligned boxes with 2m gap."""
        a = obb_corners(0, 0, 2.0, 2.0, 0.0)  # edges at x=±1
        b = obb_corners(4, 0, 2.0, 2.0, 0.0)  # edges at x=3,5
        # Gap = 3 - 1 = 2.0
        assert obb_min_distance(a, b) == pytest.approx(2.0)

    def test_diagonal_separation(self):
        """Boxes separated diagonally: distance to nearest corner."""
        a = obb_corners(0, 0, 2.0, 2.0, 0.0)  # corners at (±1, ±1)
        b = obb_corners(4, 4, 2.0, 2.0, 0.0)  # corners at (3..5, 3..5)
        # Nearest corners: (1,1) to (3,3) → sqrt(8) ≈ 2.828
        assert obb_min_distance(a, b) == pytest.approx(math.sqrt(8), abs=0.01)

    def test_rotated_separation(self):
        """One rotated box separated from axis-aligned box."""
        a = obb_corners(0, 0, 1.0, 1.0, 0.0)
        b = obb_corners(3, 0, 1.0, 1.0, math.pi / 4)
        dist = obb_min_distance(a, b)
        assert dist > 0

    def test_touching_returns_zero(self):
        """Touching edges: distance = 0."""
        a = obb_corners(0, 0, 2.0, 2.0, 0.0)
        b = obb_corners(2.0, 0, 2.0, 2.0, 0.0)
        assert obb_min_distance(a, b) == pytest.approx(0.0)


class TestSegmentOBBIntersectionLength:
    """segment_obb_intersection_length: Cyrus-Beck clipping."""

    def test_segment_fully_outside(self):
        corners = obb_corners(0, 0, 2, 2, 0)
        length = segment_obb_intersection_length((-5, 3), (5, 3), corners)
        assert length == 0.0

    def test_segment_fully_inside(self):
        corners = obb_corners(0, 0, 4, 4, 0)
        length = segment_obb_intersection_length((-0.5, 0), (0.5, 0), corners)
        assert abs(length - 1.0) < 1e-6

    def test_segment_crosses_through(self):
        corners = obb_corners(0, 0, 2, 2, 0)
        length = segment_obb_intersection_length((-5, 0), (5, 0), corners)
        assert abs(length - 2.0) < 1e-6

    def test_segment_partial_overlap(self):
        corners = obb_corners(0, 0, 2, 2, 0)
        length = segment_obb_intersection_length((0, 0), (5, 0), corners)
        assert abs(length - 1.0) < 1e-6

    def test_rotated_obb(self):
        corners = obb_corners(0, 0, 2, 2, math.pi / 4)
        length = segment_obb_intersection_length((-3, 0), (3, 0), corners)
        expected = 2 * math.sqrt(2)
        assert abs(length - expected) < 1e-4

    def test_zero_length_segment(self):
        corners = obb_corners(0, 0, 2, 2, 0)
        assert segment_obb_intersection_length((0, 0), (0, 0), corners) == 0.0

    def test_parallel_outside(self):
        corners = obb_corners(0, 0, 2, 2, 0)
        length = segment_obb_intersection_length((-5, 2), (5, 2), corners)
        assert length == 0.0
