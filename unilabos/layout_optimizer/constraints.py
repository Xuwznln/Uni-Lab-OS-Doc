"""约束体系：硬约束 / 软约束定义与统一评估。

硬约束违反 → cost = inf（方案直接淘汰）
软约束违反 → 加权 penalty 累加到 cost
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .broad_phase import sweep_and_prune_pairs
from .models import Constraint, Device, Lab, Placement
from .obb import (
    nearest_point_on_obb,
    obb_corners,
    obb_min_distance,
    obb_penetration_depth,
    segment_intersects_obb,
)

if TYPE_CHECKING:
    from typing import Any

    from .interfaces import CollisionChecker, ReachabilityChecker

# 归一化默认权重 — 1cm距离违规 ≈ 5°角度违规 的惩罚量级
DEFAULT_WEIGHT_DISTANCE: float = 100.0   # 1cm → penalty 1.0
DEFAULT_WEIGHT_ANGLE: float = 60.0       # 5° → penalty ~1.0

# 硬约束graduated模式下的惩罚倍数
HARD_MULTIPLIER: float = 5.0

# 优先级等级对应的权重乘数
PRIORITY_MULTIPLIERS: dict[str, float] = {
    "critical": 5.0,
    "high": 2.0,
    "normal": 1.0,
    "low": 0.5,
}


def evaluate_constraints(
    devices: list[Device],
    placements: list[Placement],
    lab: Lab,
    constraints: list[Constraint],
    collision_checker: CollisionChecker,
    reachability_checker: ReachabilityChecker | None = None,
    *,
    graduated: bool = True,
) -> float:
    """统一评估所有约束，返回总 cost。

    Args:
        devices: 设备列表（与 placements 一一对应）
        placements: 当前布局方案
        lab: 实验室平面图
        constraints: 约束规则列表
        collision_checker: 碰撞检测实例
        reachability_checker: 可达性检测实例（可选）
        graduated: True=比例惩罚（DE优化用），False=二值inf（最终pass/fail用）

    Returns:
        总 cost。硬约束违反在非graduated模式返回 inf，否则为加权 penalty 之和。
    """
    device_map = {d.id: d for d in devices}
    placement_map = {p.device_id: p for p in placements}

    total_cost = 0.0

    for c in constraints:
        cost = _evaluate_single(
            c, device_map, placement_map, lab, collision_checker, reachability_checker,
            graduated=graduated,
        )
        if math.isinf(cost):
            return math.inf
        total_cost += cost

    return total_cost


def evaluate_default_hard_constraints(
    devices: list[Device],
    placements: list[Placement],
    lab: Lab,
    collision_checker: CollisionChecker,
    *,
    graduated: bool = True,
    collision_weight: float = DEFAULT_WEIGHT_DISTANCE * HARD_MULTIPLIER,   # 500
    boundary_weight: float = DEFAULT_WEIGHT_DISTANCE * HARD_MULTIPLIER,    # 500
) -> float:
    """评估默认硬约束（碰撞 + 边界），无需显式声明约束列表。

    始终生效，用于 cost function 的基础检查。

    When graduated=True (default), returns a penalty proportional to the
    severity of each violation instead of binary inf.  This gives DE a
    smooth gradient so it can fix specific collision pairs instead of
    discarding near-optimal layouts entirely.

    When graduated=False, uses the legacy binary inf behaviour.
    """
    if not graduated:
        return _evaluate_hard_binary(devices, placements, lab, collision_checker)

    device_map = {d.id: d for d in devices}
    cost = 0.0

    # Graduated collision penalty: 2 轴 sweep-and-prune 宽相 + OBB SAT 精确检测
    candidate_pairs = sweep_and_prune_pairs(devices, placements)
    for i, j in candidate_pairs:
        di, dj = device_map[placements[i].device_id], device_map[placements[j].device_id]
        ci = obb_corners(placements[i].x, placements[i].y,
                         di.bbox[0], di.bbox[1], placements[i].theta)
        cj = obb_corners(placements[j].x, placements[j].y,
                         dj.bbox[0], dj.bbox[1], placements[j].theta)
        depth = obb_penetration_depth(ci, cj)
        if depth > 0:
            cost += collision_weight * depth

    # Graduated boundary penalty: sum of overshoot distances (rotation-aware)
    for p in placements:
        dev = device_map[p.device_id]
        hw, hd = p.rotated_bbox(dev)
        # How far each edge exceeds the lab boundary
        overshoot = 0.0
        overshoot += max(0.0, hw - p.x)                # left wall
        overshoot += max(0.0, (p.x + hw) - lab.width)  # right wall
        overshoot += max(0.0, hd - p.y)                # bottom wall
        overshoot += max(0.0, (p.y + hd) - lab.depth)  # top wall
        cost += boundary_weight * overshoot

    return cost


def _evaluate_hard_binary(
    devices: list[Device],
    placements: list[Placement],
    lab: Lab,
    collision_checker: CollisionChecker,
) -> float:
    """Legacy binary hard-constraint evaluation (inf or 0)."""
    checker_placements = _to_checker_format(devices, placements)

    collisions = collision_checker.check(checker_placements)
    if collisions:
        return math.inf

    if hasattr(collision_checker, "check_bounds"):
        oob = collision_checker.check_bounds(checker_placements, lab.width, lab.depth)
        if oob:
            return math.inf

    return 0.0


def _evaluate_single(
    constraint: Constraint,
    device_map: dict[str, Device],
    placement_map: dict[str, Placement],
    lab: Lab,
    collision_checker: CollisionChecker,
    reachability_checker: ReachabilityChecker | None,
    *,
    graduated: bool = True,
) -> float:
    """评估单条约束规则。

    graduated=True 时硬约束返回比例惩罚（DE用），
    graduated=False 时硬约束返回 inf（最终 pass/fail）。
    """
    rule = constraint.rule_name
    params = constraint.params
    is_hard = constraint.type == "hard"

    # 根据优先级等级计算有效权重
    effective_weight = constraint.weight
    if constraint.priority and constraint.priority in PRIORITY_MULTIPLIERS:
        effective_weight *= PRIORITY_MULTIPLIERS[constraint.priority]

    if rule == "no_collision":
        checker_placements = _to_checker_format_from_maps(device_map, placement_map)
        collisions = collision_checker.check(checker_placements)
        if collisions:
            if is_hard and not graduated:
                return math.inf
            w = effective_weight * (HARD_MULTIPLIER if is_hard else 1.0)
            return w * len(collisions)
        return 0.0

    if rule == "within_bounds":
        checker_placements = _to_checker_format_from_maps(device_map, placement_map)
        if hasattr(collision_checker, "check_bounds"):
            oob = collision_checker.check_bounds(
                checker_placements, lab.width, lab.depth
            )
            if oob:
                if is_hard and not graduated:
                    return math.inf
                w = effective_weight * (HARD_MULTIPLIER if is_hard else 1.0)
                return w * len(oob)
        return 0.0

    if rule == "distance_less_than":
        a_id, b_id = params["device_a"], params["device_b"]
        max_dist = params["distance"]
        da, db = device_map.get(a_id), device_map.get(b_id)
        pa, pb = placement_map.get(a_id), placement_map.get(b_id)
        if pa is None or pb is None:
            return 0.0
        if da and db:
            dist = _device_distance_obb(da, pa, db, pb)
        else:
            dist = _device_distance_center(pa, pb) or 0.0
        if dist > max_dist:
            if is_hard and not graduated:
                return math.inf
            w = effective_weight * (HARD_MULTIPLIER if is_hard else 1.0)
            return w * (dist - max_dist)
        return 0.0

    if rule == "distance_greater_than":
        a_id, b_id = params["device_a"], params["device_b"]
        min_dist = params["distance"]
        da, db = device_map.get(a_id), device_map.get(b_id)
        pa, pb = placement_map.get(a_id), placement_map.get(b_id)
        if pa is None or pb is None:
            return 0.0
        if da and db:
            dist = _device_distance_obb(da, pa, db, pb)
        else:
            dist = _device_distance_center(pa, pb) or 0.0
        if dist < min_dist:
            if is_hard and not graduated:
                return math.inf
            w = effective_weight * (HARD_MULTIPLIER if is_hard else 1.0)
            return w * (min_dist - dist)
        return 0.0

    if rule == "minimize_distance":
        a_id, b_id = params["device_a"], params["device_b"]
        da, db = device_map.get(a_id), device_map.get(b_id)
        pa, pb = placement_map.get(a_id), placement_map.get(b_id)
        if pa is None or pb is None:
            return 0.0
        if da and db:
            dist = _device_distance_obb(da, pa, db, pb)
        else:
            dist = _device_distance_center(pa, pb) or 0.0
        return effective_weight * dist

    if rule == "maximize_distance":
        a_id, b_id = params["device_a"], params["device_b"]
        da, db = device_map.get(a_id), device_map.get(b_id)
        pa, pb = placement_map.get(a_id), placement_map.get(b_id)
        if pa is None or pb is None:
            return 0.0
        if da and db:
            dist = _device_distance_obb(da, pa, db, pb)
        else:
            dist = _device_distance_center(pa, pb) or 0.0
        max_possible = math.sqrt(lab.width**2 + lab.depth**2)
        return effective_weight * (max_possible - dist)

    if rule == "min_spacing":
        min_gap = params.get("min_gap", 0.0)
        all_placements = list(placement_map.values())
        total_penalty = 0.0
        for i in range(len(all_placements)):
            for j in range(i + 1, len(all_placements)):
                pi, pj = all_placements[i], all_placements[j]
                di = device_map.get(pi.device_id)
                dj = device_map.get(pj.device_id)
                if di and dj:
                    dist = _device_distance_obb(di, pi, dj, pj)
                else:
                    dist = _device_distance_center(pi, pj) or 0.0
                if dist < min_gap:
                    total_penalty += (min_gap - dist)
        if total_penalty > 0:
            if is_hard and not graduated:
                return math.inf
            w = effective_weight * (HARD_MULTIPLIER if is_hard else 1.0)
            return w * total_penalty
        return 0.0

    if rule == "reachability":
        if reachability_checker is None:
            return 0.0
        arm_id = params["arm_id"]
        target_device_id = params["target_device_id"]
        arm_p = placement_map.get(arm_id)
        target_p = placement_map.get(target_device_id)
        if arm_p is None or target_p is None:
            return 0.0
        arm_dev = device_map.get(arm_id)
        target_dev = device_map.get(target_device_id)

        # Distance from target's opening surface center to nearest point on arm OBB.
        # This naturally enforces orientation: a device facing away has its opening
        # far from the arm, so it fails reachability without needing a separate
        # facing penalty.
        if arm_dev and target_dev:
            opening_pt = _opening_surface_center(target_dev, target_p)
            arm_corners = obb_corners(
                arm_p.x, arm_p.y, arm_dev.bbox[0], arm_dev.bbox[1], arm_p.theta,
            )
            nearest = nearest_point_on_obb(opening_pt[0], opening_pt[1], arm_corners)
            dist = math.sqrt((opening_pt[0] - nearest[0])**2 + (opening_pt[1] - nearest[1])**2)
        else:
            dist = _device_distance_center(arm_p, target_p) or 0.0

        arm_pose = {"x": arm_p.x, "y": arm_p.y, "theta": arm_p.theta}
        target_point = {"x": target_p.x, "y": target_p.y, "z": 0.0}
        target_point["_obb_dist"] = dist
        if not reachability_checker.is_reachable(arm_id, arm_pose, target_point):
            if is_hard and not graduated:
                return math.inf
            # Graduated: penalty proportional to overshoot
            max_reach = reachability_checker.arm_reach.get(arm_id, 2.0)
            overshoot = max(0.0, dist - max_reach)
            w = effective_weight * (HARD_MULTIPLIER if is_hard else 1.0)
            return w * overshoot * 10.0

        # Line-of-sight penalty: penalize if any other device OBB blocks
        # the path from opening to arm
        los_cost = _line_of_sight_penalty(
            arm_id, arm_p, target_device_id, target_p,
            device_map, placement_map, effective_weight,
        )
        return los_cost

    if rule == "prefer_aligned":
        alignment_cost = sum(
            (1 - math.cos(4 * p.theta)) / 2 for p in placement_map.values()
        )
        if is_hard:
            if not graduated:
                return math.inf if alignment_cost > 1e-6 else 0.0
            return HARD_MULTIPLIER * effective_weight * alignment_cost
        return effective_weight * alignment_cost

    if rule == "prefer_seeder_orientation":
        target_thetas = params.get("target_thetas", {})
        cost = 0.0
        for dev_id, target in target_thetas.items():
            p = placement_map.get(dev_id)
            if p is None:
                continue
            # Circular distance: (1 - cos(diff)) / 2 gives 0..1 range
            diff = p.theta - target
            cost += (1 - math.cos(diff)) / 2
        return effective_weight * cost

    if rule == "prefer_orientation_mode":
        mode = params.get("mode", "outward")
        center_x = lab.width / 2
        center_y = lab.depth / 2
        cost = 0.0
        for dev_id, p in placement_map.items():
            dev = device_map.get(dev_id)
            if dev is None:
                continue
            target = _desired_theta(
                p.x, p.y, center_x, center_y, dev, mode,
            )
            if target is None:
                continue
            diff = p.theta - target
            cost += (1 - math.cos(diff)) / 2
        return effective_weight * cost

    # 未知约束类型，忽略
    return 0.0


def _desired_theta(
    x: float, y: float,
    center_x: float, center_y: float,
    device: Device, mode: str,
) -> float | None:
    """Compute desired theta for outward/inward facing at the given position."""
    dx = x - center_x
    dy = y - center_y
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None  # At center, no preferred direction
    angle_to_device = math.atan2(dy, dx)
    front = device.openings[0].direction if device.openings else (0.0, -1.0)
    front_angle = math.atan2(front[1], front[0])
    if mode == "outward":
        target = angle_to_device
    elif mode == "inward":
        target = angle_to_device + math.pi
    else:
        return None
    return (target - front_angle) % (2 * math.pi)


def _device_distance_center(a: Placement | None, b: Placement | None) -> float | None:
    """计算两设备中心的欧几里得距离（后备方法）。"""
    if a is None or b is None:
        return None
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def _device_distance_obb(
    device_a: Device, placement_a: Placement,
    device_b: Device, placement_b: Placement,
) -> float:
    """Minimum edge-to-edge distance between two devices using OBB."""
    corners_a = obb_corners(
        placement_a.x, placement_a.y,
        device_a.bbox[0], device_a.bbox[1],
        placement_a.theta,
    )
    corners_b = obb_corners(
        placement_b.x, placement_b.y,
        device_b.bbox[0], device_b.bbox[1],
        placement_b.theta,
    )
    return obb_min_distance(corners_a, corners_b)


def _to_checker_format(
    devices: list[Device], placements: list[Placement]
) -> list[dict]:
    """转换为 CollisionChecker.check() 接受的格式。"""
    device_map = {d.id: d for d in devices}
    result = []
    for p in placements:
        dev = device_map.get(p.device_id)
        if dev is None:
            continue
        result.append({"id": p.device_id, "bbox": dev.bbox, "pos": (p.x, p.y, p.theta)})
    return result


def _to_checker_format_from_maps(
    device_map: dict[str, Device], placement_map: dict[str, Placement]
) -> list[dict]:
    """从 map 转换为 CollisionChecker.check() 接受的格式。"""
    result = []
    for dev_id, p in placement_map.items():
        dev = device_map.get(dev_id)
        if dev is None:
            continue
        result.append({"id": dev_id, "bbox": dev.bbox, "pos": (p.x, p.y, p.theta)})
    return result


def _opening_surface_center(
    device: Device, placement: Placement,
) -> tuple[float, float]:
    """Return the world-space center of the device's opening surface.

    Computes where the opening direction intersects the device's bbox boundary,
    then transforms to world coordinates. For a device facing away from the arm,
    this point is on the far side — making the distance to the arm larger,
    which naturally penalizes wrong orientation.
    """
    front = device.openings[0].direction if device.openings else (0.0, -1.0)
    dx, dy = front
    w, h = device.bbox

    # Scale factor to reach bbox edge in the opening direction
    scales = []
    if abs(dx) > 1e-9:
        scales.append((w / 2) / abs(dx))
    if abs(dy) > 1e-9:
        scales.append((h / 2) / abs(dy))
    scale = min(scales) if scales else 0.0

    # Opening center in local frame
    local_x = dx * scale
    local_y = dy * scale

    # Rotate to world frame and translate
    cos_t = math.cos(placement.theta)
    sin_t = math.sin(placement.theta)
    world_x = placement.x + local_x * cos_t - local_y * sin_t
    world_y = placement.y + local_x * sin_t + local_y * cos_t
    return (world_x, world_y)


def evaluate_default_hard_constraints_breakdown(
    devices: list[Device],
    placements: list[Placement],
    lab: Lab,
    collision_checker: CollisionChecker,
    *,
    collision_weight: float = DEFAULT_WEIGHT_DISTANCE * HARD_MULTIPLIER,
    boundary_weight: float = DEFAULT_WEIGHT_DISTANCE * HARD_MULTIPLIER,
) -> dict[str, float]:
    """与 evaluate_default_hard_constraints 逻辑相同，但返回分项明细。"""
    device_map = {d.id: d for d in devices}
    collision_cost = 0.0
    boundary_cost = 0.0

    candidate_pairs = sweep_and_prune_pairs(devices, placements)
    for i, j in candidate_pairs:
        di, dj = device_map[placements[i].device_id], device_map[placements[j].device_id]
        ci = obb_corners(placements[i].x, placements[i].y,
                         di.bbox[0], di.bbox[1], placements[i].theta)
        cj = obb_corners(placements[j].x, placements[j].y,
                         dj.bbox[0], dj.bbox[1], placements[j].theta)
        depth = obb_penetration_depth(ci, cj)
        if depth > 0:
            collision_cost += collision_weight * depth

    for p in placements:
        dev = device_map[p.device_id]
        hw, hd = p.rotated_bbox(dev)
        overshoot = 0.0
        overshoot += max(0.0, hw - p.x)
        overshoot += max(0.0, (p.x + hw) - lab.width)
        overshoot += max(0.0, hd - p.y)
        overshoot += max(0.0, (p.y + hd) - lab.depth)
        boundary_cost += boundary_weight * overshoot

    return {
        "collision": collision_cost,
        "boundary": boundary_cost,
        "total": collision_cost + boundary_cost,
        "collision_weight": collision_weight,
        "boundary_weight": boundary_weight,
    }


def evaluate_constraints_breakdown(
    devices: list[Device],
    placements: list[Placement],
    lab: Lab,
    constraints: list[Constraint],
    collision_checker: CollisionChecker,
    reachability_checker: ReachabilityChecker | None = None,
) -> list[dict[str, Any]]:
    """与 evaluate_constraints 逻辑相同，但返回每条约束的分项明细。"""
    device_map = {d.id: d for d in devices}
    placement_map = {p.device_id: p for p in placements}

    results = []
    for c in constraints:
        cost = _evaluate_single(
            c, device_map, placement_map, lab, collision_checker, reachability_checker,
            graduated=True,
        )
        ew = c.weight
        if c.priority and c.priority in PRIORITY_MULTIPLIERS:
            ew *= PRIORITY_MULTIPLIERS[c.priority]
        results.append({
            "name": _constraint_display_name(c),
            "rule": c.rule_name,
            "type": c.type,
            "cost": cost,
            "weight": ew,
        })
    return results


def _constraint_display_name(c: Constraint) -> str:
    """为约束生成可读的显示名称。"""
    params = c.params
    if c.rule_name in (
        "distance_less_than", "distance_greater_than",
        "minimize_distance", "maximize_distance",
    ):
        return f"{c.rule_name}({params.get('device_a', '?')}, {params.get('device_b', '?')})"
    if c.rule_name == "reachability":
        return f"reachability({params.get('arm_id', '?')}, {params.get('target_device_id', '?')})"
    if c.rule_name == "min_spacing":
        return f"min_spacing(gap={params.get('min_gap', '?')})"
    if c.rule_name == "prefer_orientation_mode":
        return f"prefer_orientation_mode({params.get('mode', '?')})"
    return c.rule_name


def _line_of_sight_penalty(
    arm_id: str,
    arm_p: Placement,
    target_id: str,
    target_p: Placement,
    device_map: dict[str, Device],
    placement_map: dict[str, Placement],
    weight: float,
) -> float:
    """Penalty for other devices blocking the line from target to arm center.

    For each other device whose OBB intersects the segment (target_center → arm_center),
    adds a penalty proportional to the weight. This encourages layouts where
    the arm has a clear path to each target.
    """
    p1 = (target_p.x, target_p.y)
    p2 = (arm_p.x, arm_p.y)
    cost = 0.0
    for dev_id, p in placement_map.items():
        if dev_id == arm_id or dev_id == target_id:
            continue
        dev = device_map.get(dev_id)
        if dev is None:
            continue
        corners = obb_corners(p.x, p.y, dev.bbox[0], dev.bbox[1], p.theta)
        if segment_intersects_obb(p1, p2, corners):
            cost += weight * 2.0  # penalty per blocking device
    return cost
