"""布局可行性诊断：解析冲突预检 + 约束违反明细提取。

本模块服务于失败自愈逻辑，用来区分两类"求解失败"：

- 情况 A（真不可行 / 可行域为空）：用确定性的几何/算术判据**提前**检测出来，
  零假阳性——只报告"任何算法都无解"的硬冲突。命中后无需再跑 DE，直接据此
  向用户建议放宽哪条约束。
- 情况 B（可行但 DE 没搜到）：由多起点并行 DE 处理，不在本模块范围内。

另外提供 ``compute_breakdown``，把最终布局的逐条约束（含默认碰撞/边界硬约束）
惩罚明细抽取出来，供 API 返回和"哪条硬约束一直违反"的聚合判断使用。

本模块刻意保持**无重副作用的导入**（不创建日志文件、不构建 FastAPI app），
以便被多进程 worker 安全导入。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from .constraints import (
    evaluate_constraints_breakdown,
    evaluate_default_hard_constraints_breakdown,
)
from .models import Constraint, Device, Lab, Placement

if TYPE_CHECKING:
    from typing import Any

    from .interfaces import CollisionChecker, ReachabilityChecker

_EPS = 1e-9


@dataclass
class Conflict:
    """一条确定性的硬约束冲突（情况 A）。"""

    kind: str  # 冲突类别，如 "area" / "distance_contradiction"
    message: str  # 人类可读的解释
    devices: list[str] = field(default_factory=list)  # 涉及的设备 id
    rules: list[str] = field(default_factory=list)  # 涉及的约束 rule_name
    suggestion: str = ""  # 放宽建议


def precheck_conflicts(
    devices: list[Device],
    lab: Lab,
    constraints: list[Constraint],
    arm_reach: dict[str, float] | None = None,
) -> list[Conflict]:
    """跑 DE 之前的解析冲突预检。

    只检测可以**确定性证明无解**的冲突（零假阳性）：
    1. 设备总面积 > 实验室面积（互不重叠矩形的面积下界）
    2. 单台设备本身放不进实验室（任意正交朝向都装不下）
    3. 同一对设备 max_distance < min_distance（直接矛盾）
    4. max_distance < 全局 min_spacing（边到边距离矛盾）
    5. min_distance > 实验室对角线（物理上分不了那么远）

    Args:
        devices: 设备列表
        lab: 实验室平面图
        constraints: 已展开（重复实例 fan-out 后）的约束列表
        arm_reach: 机械臂臂展表（保留参数，当前不做可达性硬判定以免假阳性）

    Returns:
        Conflict 列表。为空表示预检未发现确定性冲突（可能仍是情况 B）。
    """
    conflicts: list[Conflict] = []
    device_map = {d.id: d for d in devices}

    def _name(dev_id: str) -> str:
        dev = device_map.get(dev_id)
        return dev.name if dev and dev.name else dev_id

    # --- 1 & 2. 面积 / 单设备尺寸 ---
    lab_area = lab.width * lab.depth
    lab_min = min(lab.width, lab.depth)
    lab_diag = math.sqrt(lab.width**2 + lab.depth**2)

    total_area = sum(d.bbox[0] * d.bbox[1] for d in devices)
    if total_area > lab_area + _EPS:
        conflicts.append(
            Conflict(
                kind="area",
                message=(
                    f"设备占地总面积 {total_area:.3f}㎡ 超过实验室面积 "
                    f"{lab_area:.3f}㎡（{lab.width:.2f}×{lab.depth:.2f}），"
                    "互不重叠地摆放在物理上不可能。"
                ),
                devices=[d.id for d in devices],
                rules=["no_collision", "within_bounds"],
                suggestion="扩大实验室尺寸、减少设备数量，或更换更小占地的设备。",
            )
        )

    for d in devices:
        w, h = d.bbox
        if min(w, h) > lab_min + _EPS or max(w, h) > lab_diag + _EPS:
            conflicts.append(
                Conflict(
                    kind="device_too_large",
                    message=(
                        f"设备 '{_name(d.id)}' 尺寸 {w:.2f}×{h:.2f}m 放不进实验室 "
                        f"{lab.width:.2f}×{lab.depth:.2f}m（任意朝向都装不下）。"
                    ),
                    devices=[d.id],
                    rules=["within_bounds"],
                    suggestion="扩大实验室尺寸或更换更小占地的设备。",
                )
            )

    # --- 3/4/5. 成对距离约束矛盾 ---
    max_dist: dict[tuple[str, str], float] = {}
    min_dist: dict[tuple[str, str], float] = {}
    min_gap_global: float | None = None

    for c in constraints:
        if c.type != "hard":
            continue
        if c.rule_name == "distance_less_than":
            key = _pair_key(c.params.get("device_a"), c.params.get("device_b"))
            if key is None:
                continue
            v = float(c.params["distance"])
            max_dist[key] = min(max_dist.get(key, math.inf), v)
        elif c.rule_name == "distance_greater_than":
            key = _pair_key(c.params.get("device_a"), c.params.get("device_b"))
            if key is None:
                continue
            v = float(c.params["distance"])
            min_dist[key] = max(min_dist.get(key, 0.0), v)
        elif c.rule_name == "min_spacing":
            g = float(c.params.get("min_gap", 0.0))
            min_gap_global = max(min_gap_global or 0.0, g)

    for key, mx in max_dist.items():
        a, b = key
        # 3. max < min（同一对）
        if key in min_dist and mx < min_dist[key] - _EPS:
            conflicts.append(
                Conflict(
                    kind="distance_contradiction",
                    message=(
                        f"'{_name(a)}' 与 '{_name(b)}' 同时被要求间距 ≤ {mx:.2f}m "
                        f"且 ≥ {min_dist[key]:.2f}m，二者直接矛盾。"
                    ),
                    devices=[a, b],
                    rules=["distance_less_than", "distance_greater_than"],
                    suggestion="调整 max_distance / min_distance 数值使区间不为空，或删除其中一条。",
                )
            )
        # 4. max < 全局 min_spacing（边到边）
        if min_gap_global is not None and mx < min_gap_global - _EPS:
            conflicts.append(
                Conflict(
                    kind="max_distance_below_min_spacing",
                    message=(
                        f"'{_name(a)}' 与 '{_name(b)}' 要求间距 ≤ {mx:.2f}m，"
                        f"但全局最小间隙 min_spacing={min_gap_global:.2f}m 要求所有设备"
                        "边到边至少这么远，二者矛盾。"
                    ),
                    devices=[a, b],
                    rules=["distance_less_than", "min_spacing"],
                    suggestion="增大该对的 max_distance，或减小 min_spacing。",
                )
            )

    for key, mn in min_dist.items():
        a, b = key
        # 5. min > 实验室对角线
        if mn > lab_diag + _EPS:
            conflicts.append(
                Conflict(
                    kind="min_distance_exceeds_lab",
                    message=(
                        f"'{_name(a)}' 与 '{_name(b)}' 要求间距 ≥ {mn:.2f}m，"
                        f"但实验室对角线只有 {lab_diag:.2f}m，物理上分不了那么远。"
                    ),
                    devices=[a, b],
                    rules=["distance_greater_than"],
                    suggestion="减小该对的 min_distance，或扩大实验室尺寸。",
                )
            )

    return conflicts


def _pair_key(a: str | None, b: str | None) -> tuple[str, str] | None:
    """归一化设备对的键（无序）。"""
    if a is None or b is None:
        return None
    return tuple(sorted([a, b]))  # type: ignore[return-value]


def compute_breakdown(
    devices: list[Device],
    placements: list[Placement],
    lab: Lab,
    constraints: list[Constraint],
    collision_checker: CollisionChecker,
    reachability_checker: ReachabilityChecker | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """计算最终布局的逐条约束惩罚明细及硬约束违反项。

    Returns:
        (breakdown, violations)
        - breakdown：每条约束（含默认碰撞/边界）的 {name, rule, type, weight, cost}
        - violations：其中 type == "hard" 且 cost > 0 的子集，按 cost 降序排列，
          即"导致求解失败"的罪魁候选。
    """
    hard_bd = evaluate_default_hard_constraints_breakdown(
        devices, placements, lab, collision_checker,
    )
    breakdown: list[dict[str, Any]] = [
        {
            "name": "[predefined] collision",
            "rule": "no_collision",
            "type": "hard",
            "weight": hard_bd["collision_weight"],
            "cost": hard_bd["collision"],
        },
        {
            "name": "[predefined] boundary",
            "rule": "within_bounds",
            "type": "hard",
            "weight": hard_bd["boundary_weight"],
            "cost": hard_bd["boundary"],
        },
    ]

    if constraints:
        user_bd = evaluate_constraints_breakdown(
            devices, placements, lab, constraints,
            collision_checker, reachability_checker,
        )
        for item in user_bd:
            breakdown.append(
                {
                    "name": item["name"],
                    "rule": item["rule"],
                    "type": item["type"],
                    "weight": item["weight"],
                    "cost": item["cost"],
                }
            )

    violations = [
        {
            "name": b["name"],
            "rule": b["rule"],
            "type": b["type"],
            "weight": b["weight"],
            "cost": b["cost"],
        }
        for b in breakdown
        if b["type"] == "hard" and b["cost"] > _EPS
    ]
    violations.sort(key=lambda v: v["cost"], reverse=True)
    return breakdown, violations


def conflicts_to_dicts(conflicts: list[Conflict]) -> list[dict[str, Any]]:
    """Conflict 列表 → 可 JSON 序列化的 dict 列表。"""
    return [asdict(c) for c in conflicts]
