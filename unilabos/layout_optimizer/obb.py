"""OBB (Oriented Bounding Box) geometry: corners, overlap (SAT), minimum distance."""
from __future__ import annotations
import math


def obb_corners(
    cx: float, cy: float, w: float, h: float, theta: float
) -> list[tuple[float, float]]:
    """Return 4 corners of the OBB as (x, y) tuples.

    Args:
        cx, cy: center position
        w, h: full width and height (not half-extents)
        theta: rotation angle in radians
    """
    hw, hh = w / 2, h / 2
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    dx_w, dy_w = hw * cos_t, hw * sin_t   # half-width vector
    dx_h, dy_h = -hh * sin_t, hh * cos_t  # half-height vector
    return [
        (cx + dx_w + dx_h, cy + dy_w + dy_h),
        (cx - dx_w + dx_h, cy - dy_w + dy_h),
        (cx - dx_w - dx_h, cy - dy_w - dy_h),
        (cx + dx_w - dx_h, cy + dy_w - dy_h),
    ]


def _get_axes(corners: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return 2 edge-normal axes for a rectangle (4 corners)."""
    axes = []
    for i in range(2):  # Only need 2 axes for a rectangle
        edge_x = corners[i + 1][0] - corners[i][0]
        edge_y = corners[i + 1][1] - corners[i][1]
        length = math.sqrt(edge_x**2 + edge_y**2)
        if length > 0:
            axes.append((-edge_y / length, edge_x / length))
    return axes


def _project(corners: list[tuple[float, float]], axis: tuple[float, float]) -> tuple[float, float]:
    """Project all corners onto axis, return (min, max) scalar projections."""
    dots = [c[0] * axis[0] + c[1] * axis[1] for c in corners]
    return min(dots), max(dots)


def obb_overlap(corners_a: list[tuple[float, float]], corners_b: list[tuple[float, float]]) -> bool:
    """Return True if two OBBs overlap using Separating Axis Theorem.

    Uses strict inequality (touching edges = no overlap).
    """
    for axis in _get_axes(corners_a) + _get_axes(corners_b):
        min_a, max_a = _project(corners_a, axis)
        min_b, max_b = _project(corners_b, axis)
        if max_a <= min_b or max_b <= min_a:
            return False
    return True


def _point_to_segment_dist_sq(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """Squared distance from point (px,py) to line segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    proj_x, proj_y = ax + t * dx, ay + t * dy
    return (px - proj_x) ** 2 + (py - proj_y) ** 2


def obb_penetration_depth(
    corners_a: list[tuple[float, float]],
    corners_b: list[tuple[float, float]],
) -> float:
    """Minimum penetration depth between two OBBs (SAT-based).

    Returns 0.0 if not overlapping. Otherwise returns the minimum overlap
    along any separating axis — the smallest push needed to separate them.
    """
    min_overlap = float("inf")
    for axis in _get_axes(corners_a) + _get_axes(corners_b):
        min_a, max_a = _project(corners_a, axis)
        min_b, max_b = _project(corners_b, axis)
        overlap = min(max_a - min_b, max_b - min_a)
        if overlap <= 0:
            return 0.0  # Separated on this axis
        if overlap < min_overlap:
            min_overlap = overlap
    return min_overlap


def nearest_point_on_obb(
    px: float, py: float,
    corners: list[tuple[float, float]],
) -> tuple[float, float]:
    """Return the nearest point on an OBB's boundary to point (px, py).

    If the point is inside the OBB, returns the nearest edge point.
    """
    best_x, best_y = corners[0]
    best_dist_sq = float("inf")
    n = len(corners)
    for i in range(n):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        len_sq = dx * dx + dy * dy
        if len_sq == 0:
            proj_x, proj_y = ax, ay
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
            proj_x, proj_y = ax + t * dx, ay + t * dy
        d_sq = (px - proj_x) ** 2 + (py - proj_y) ** 2
        if d_sq < best_dist_sq:
            best_dist_sq = d_sq
            best_x, best_y = proj_x, proj_y
    return best_x, best_y


def segment_intersects_obb(
    p1: tuple[float, float],
    p2: tuple[float, float],
    corners: list[tuple[float, float]],
) -> bool:
    """Return True if line segment p1-p2 intersects the OBB (convex polygon).

    Uses separating axis theorem on the Minkowski difference:
    test segment against each edge of the polygon + segment normal.
    """
    # Quick: test each edge of the OBB against the segment
    n = len(corners)
    for i in range(n):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % n]
        if _segments_intersect(p1[0], p1[1], p2[0], p2[1], ax, ay, bx, by):
            return True
    # Also check if segment is fully inside the OBB
    if _point_in_convex(p1, corners) or _point_in_convex(p2, corners):
        return True
    return False


def _segments_intersect(
    ax: float, ay: float, bx: float, by: float,
    cx: float, cy: float, dx: float, dy: float,
) -> bool:
    """Return True if segment AB intersects segment CD (proper or endpoint)."""
    def cross(ox: float, oy: float, px: float, py: float, qx: float, qy: float) -> float:
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)

    d1 = cross(cx, cy, dx, dy, ax, ay)
    d2 = cross(cx, cy, dx, dy, bx, by)
    d3 = cross(ax, ay, bx, by, cx, cy)
    d4 = cross(ax, ay, bx, by, dx, dy)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    # Collinear cases — skip for simplicity (near-zero probability in DE)
    return False


def _point_in_convex(
    p: tuple[float, float], corners: list[tuple[float, float]]
) -> bool:
    """Return True if point is inside a convex polygon (corners in order)."""
    n = len(corners)
    sign = None
    for i in range(n):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % n]
        cross = (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
        if abs(cross) < 1e-12:
            continue
        s = cross > 0
        if sign is None:
            sign = s
        elif s != sign:
            return False
    return True


def obb_min_distance(
    corners_a: list[tuple[float, float]],
    corners_b: list[tuple[float, float]],
) -> float:
    """Minimum distance between two OBBs (convex polygons).

    Returns 0.0 if overlapping or touching.
    """
    if obb_overlap(corners_a, corners_b):
        return 0.0

    min_dist_sq = float("inf")
    for poly, other in [(corners_a, corners_b), (corners_b, corners_a)]:
        n = len(other)
        for px, py in poly:
            for i in range(n):
                ax, ay = other[i]
                bx, by = other[(i + 1) % n]
                d_sq = _point_to_segment_dist_sq(px, py, ax, ay, bx, by)
                if d_sq < min_dist_sq:
                    min_dist_sq = d_sq
    return math.sqrt(min_dist_sq)
