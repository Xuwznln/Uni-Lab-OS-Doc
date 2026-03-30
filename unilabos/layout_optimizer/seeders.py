"""Force-directed seeder engine with named parameter presets.

Produces initial device placements for the layout optimizer.
Different layout strategies (compact, spread, workflow-aware) are
parameter configurations of the same force-directed simulation engine.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace

from .models import Device, Lab, Placement
from .obb import obb_corners, obb_overlap, obb_min_distance

logger = logging.getLogger(__name__)


@dataclass
class SeederParams:
    """Parameters for the force-directed seeder engine."""
    boundary_attraction: float = 0.0   # >0 push to walls, <0 push to center
    mutual_repulsion: float = 1.0      # inter-device repulsion strength
    edge_attraction: float = 0.0       # workflow edge attraction (Stage 2)
    orientation_mode: str = "none"     # "outward" | "inward" | "none"


PRESETS: dict[str, SeederParams | None] = {
    "compact_outward": SeederParams(
        boundary_attraction=-1.0, mutual_repulsion=0.5, orientation_mode="outward",
    ),
    "spread_inward": SeederParams(
        boundary_attraction=1.0, mutual_repulsion=1.0, orientation_mode="inward",
    ),
    "workflow_cluster": SeederParams(
        boundary_attraction=-0.5, mutual_repulsion=0.5,
        edge_attraction=1.0, orientation_mode="outward",
    ),
    "row_fallback": None,  # Delegates to generate_fallback()
}


def resolve_seeder_params(
    preset_name: str, overrides: dict | None = None,
) -> SeederParams | None:
    """Look up preset by name and apply overrides."""
    if preset_name not in PRESETS:
        raise ValueError(
            f"Unknown seeder preset '{preset_name}'. "
            f"Available: {list(PRESETS.keys())}"
        )
    params = PRESETS[preset_name]
    if params is None or not overrides:
        return params
    return replace(params, **{k: v for k, v in overrides.items() if hasattr(params, k)})


def seed_layout(
    devices: list[Device],
    lab: Lab,
    params: SeederParams | None,
    edges: list[list[str]] | None = None,
) -> list[Placement]:
    """Generate initial device placements using force-directed simulation.

    Args:
        devices: devices to place
        lab: lab dimensions
        params: seeder parameters (None = row_fallback)
        edges: workflow edges as [device_a_id, device_b_id] pairs (Stage 2)

    Returns:
        list of Placement objects, one per device
    """
    if not devices:
        return []

    if params is None:
        from .pencil_integration import generate_fallback
        return generate_fallback(devices, lab)

    return _force_simulation(devices, lab, params, edges)


def _force_simulation(
    devices: list[Device],
    lab: Lab,
    params: SeederParams,
    edges: list[list[str]] | None = None,
    max_iter: int = 80,
    dt: float = 0.05,
    damping: float = 0.8,
) -> list[Placement]:
    """Run force-directed simulation to produce initial placements."""
    n = len(devices)
    center_x, center_y = lab.width / 2, lab.depth / 2

    # Initialize positions: grid layout within lab bounds
    cols = max(1, int(math.ceil(math.sqrt(n))))
    rows_count = max(1, math.ceil(n / cols))
    positions = []  # (x, y) per device
    for i, dev in enumerate(devices):
        row, col = divmod(i, cols)
        margin = 0.3
        x = margin + (col + 0.5) * (lab.width - 2 * margin) / cols
        y = margin + (row + 0.5) * (lab.depth - 2 * margin) / rows_count
        x = min(max(x, dev.bbox[0] / 2), lab.width - dev.bbox[0] / 2)
        y = min(max(y, dev.bbox[1] / 2), lab.depth - dev.bbox[1] / 2)
        positions.append([x, y])

    # Initialize orientations
    thetas = [0.0] * n

    # Build edge lookup for Stage 2
    edge_set: set[tuple[int, int]] = set()
    if edges and params.edge_attraction > 0:
        id_to_idx = {d.id: i for i, d in enumerate(devices)}
        for e in edges:
            if len(e) == 2 and e[0] in id_to_idx and e[1] in id_to_idx:
                edge_set.add((id_to_idx[e[0]], id_to_idx[e[1]]))

    converged = False
    for iteration in range(max_iter):
        forces = [[0.0, 0.0] for _ in range(n)]
        total_force = 0.0

        # 1. Boundary force
        for i in range(n):
            dx = positions[i][0] - center_x
            dy = positions[i][1] - center_y
            dist_to_center = math.sqrt(dx * dx + dy * dy) + 1e-9
            f = params.boundary_attraction
            forces[i][0] += f * dx / dist_to_center
            forces[i][1] += f * dy / dist_to_center

        # 2. Mutual repulsion (OBB edge-to-edge)
        for i in range(n):
            for j in range(i + 1, n):
                ci = obb_corners(
                    positions[i][0], positions[i][1],
                    devices[i].bbox[0], devices[i].bbox[1], thetas[i],
                )
                cj = obb_corners(
                    positions[j][0], positions[j][1],
                    devices[j].bbox[0], devices[j].bbox[1], thetas[j],
                )
                dist = obb_min_distance(ci, cj)
                if dist < 1e-9:
                    dist = 0.01  # Prevent division by zero for overlapping
                dx = positions[i][0] - positions[j][0]
                dy = positions[i][1] - positions[j][1]
                d_center = math.sqrt(dx * dx + dy * dy) + 1e-9
                repulsion = params.mutual_repulsion / (dist * dist + 0.1)
                fx = repulsion * dx / d_center
                fy = repulsion * dy / d_center
                forces[i][0] += fx
                forces[i][1] += fy
                forces[j][0] -= fx
                forces[j][1] -= fy

        # 3. Edge attraction (Stage 2)
        if params.edge_attraction > 0:
            for i_idx, j_idx in edge_set:
                dx = positions[j_idx][0] - positions[i_idx][0]
                dy = positions[j_idx][1] - positions[i_idx][1]
                dist = math.sqrt(dx * dx + dy * dy) + 1e-9
                f = params.edge_attraction * dist * 0.1
                forces[i_idx][0] += f * dx / dist
                forces[i_idx][1] += f * dy / dist
                forces[j_idx][0] -= f * dx / dist
                forces[j_idx][1] -= f * dy / dist

        # 4. Update positions (Euler + damping)
        for i in range(n):
            positions[i][0] += forces[i][0] * dt * damping
            positions[i][1] += forces[i][1] * dt * damping
            total_force += math.sqrt(forces[i][0]**2 + forces[i][1]**2)

        # 5. Update orientations
        if params.orientation_mode != "none":
            for i in range(n):
                thetas[i] = _compute_orientation(
                    positions[i][0], positions[i][1],
                    center_x, center_y,
                    devices[i], params.orientation_mode,
                )

        # 6. Clamp to lab bounds
        for i in range(n):
            hw, hh = devices[i].bbox[0] / 2, devices[i].bbox[1] / 2
            positions[i][0] = max(hw, min(lab.width - hw, positions[i][0]))
            positions[i][1] = max(hh, min(lab.depth - hh, positions[i][1]))

        if total_force < 0.01 * n:
            converged = True
            logger.info("Force simulation converged at iteration %d", iteration)
            break

    if not converged:
        logger.info("Force simulation reached max iterations (%d)", max_iter)

    placements = [
        Placement(device_id=devices[i].id, x=positions[i][0], y=positions[i][1], theta=thetas[i])
        for i in range(n)
    ]

    # Log initial collision count
    initial_collisions = _count_collisions(devices, placements)
    logger.info("Seeder: %d initial collision pairs before resolution", initial_collisions)

    # Collision resolution pass
    placements = _resolve_collisions(devices, placements, lab, max_passes=5)

    # Log diagnostics
    final_collisions = _count_collisions(devices, placements)
    no_openings = sum(1 for d in devices if not d.openings)
    logger.info(
        "Seeder complete: %d devices, %d without openings, %d collision pairs remaining",
        n, no_openings, final_collisions,
    )

    return placements


def _compute_orientation(
    x: float, y: float,
    center_x: float, center_y: float,
    device: Device,
    mode: str,
) -> float:
    """Compute theta so the device's front faces outward or inward."""
    dx = x - center_x
    dy = y - center_y
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return 0.0

    angle_to_device = math.atan2(dy, dx)

    if device.openings:
        front = device.openings[0].direction
    else:
        front = (0.0, -1.0)  # Default: -Y is front

    front_angle = math.atan2(front[1], front[0])

    if mode == "outward":
        target = angle_to_device
    elif mode == "inward":
        target = angle_to_device + math.pi
    else:
        return 0.0

    return (target - front_angle) % (2 * math.pi)


def _count_collisions(devices: list[Device], placements: list[Placement]) -> int:
    """Count OBB collision pairs (for diagnostics logging)."""
    n = len(devices)
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            ci = obb_corners(placements[i].x, placements[i].y,
                             devices[i].bbox[0], devices[i].bbox[1], placements[i].theta)
            cj = obb_corners(placements[j].x, placements[j].y,
                             devices[j].bbox[0], devices[j].bbox[1], placements[j].theta)
            if obb_overlap(ci, cj):
                count += 1
    return count


def _resolve_collisions(
    devices: list[Device],
    placements: list[Placement],
    lab: Lab,
    max_passes: int = 5,
) -> list[Placement]:
    """Push overlapping devices apart. Returns new placement list."""
    positions = [[p.x, p.y] for p in placements]
    thetas = [p.theta for p in placements]
    n = len(devices)

    for pass_num in range(max_passes):
        has_collision = False
        for i in range(n):
            for j in range(i + 1, n):
                ci = obb_corners(
                    positions[i][0], positions[i][1],
                    devices[i].bbox[0], devices[i].bbox[1], thetas[i],
                )
                cj = obb_corners(
                    positions[j][0], positions[j][1],
                    devices[j].bbox[0], devices[j].bbox[1], thetas[j],
                )
                if obb_overlap(ci, cj):
                    has_collision = True
                    dx = positions[i][0] - positions[j][0]
                    dy = positions[i][1] - positions[j][1]
                    dist = math.sqrt(dx * dx + dy * dy) + 1e-9
                    push = 0.5 * (
                        max(devices[i].bbox[0], devices[i].bbox[1])
                        + max(devices[j].bbox[0], devices[j].bbox[1])
                    ) / 4
                    positions[i][0] += push * dx / dist
                    positions[i][1] += push * dy / dist
                    positions[j][0] -= push * dx / dist
                    positions[j][1] -= push * dy / dist

        # Clamp to bounds (rotation-aware AABB half-extents)
        for i in range(n):
            cos_t = abs(math.cos(thetas[i]))
            sin_t = abs(math.sin(thetas[i]))
            hw = (devices[i].bbox[0] * cos_t + devices[i].bbox[1] * sin_t) / 2
            hh = (devices[i].bbox[0] * sin_t + devices[i].bbox[1] * cos_t) / 2
            positions[i][0] = max(hw, min(lab.width - hw, positions[i][0]))
            positions[i][1] = max(hh, min(lab.depth - hh, positions[i][1]))

        if not has_collision:
            logger.info("Collision resolution complete after %d passes", pass_num + 1)
            break
    else:
        logger.warning(
            "Collision resolution: %d passes exhausted, collisions may remain",
            max_passes,
        )

    return [
        Placement(device_id=placements[i].device_id,
                  x=positions[i][0], y=positions[i][1],
                  theta=thetas[i], uuid=placements[i].uuid)
        for i in range(n)
    ]
