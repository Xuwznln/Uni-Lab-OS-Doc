"""差分进化布局优化器。

编码：N 个设备 → 3N 维向量 [x0, y0, θ0, x1, y1, θ1, ...]
使用自定义差分进化循环（per-device crossover + θ wrapping）进行全局优化。
初始布局（Pencil/回退）注入为种群种子个体加速收敛。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

import numpy as np

from .constraints import (
    evaluate_constraints,
    evaluate_constraints_breakdown,
    evaluate_default_hard_constraints,
    evaluate_default_hard_constraints_breakdown,
)
from .mock_checkers import MockCollisionChecker, MockReachabilityChecker
from .models import Constraint, Device, Lab, Placement
from .pencil_integration import generate_initial_layout
from .seeders import resolve_seeder_params, seed_layout

logger = logging.getLogger(__name__)


def _run_de(
    cost_fn: Callable[[np.ndarray], float],
    bounds: np.ndarray,
    init_pop: np.ndarray,
    maxiter: int,
    tol: float,
    atol: float,
    mutation: tuple[float, float],
    recombination: float,
    seed: int | None,
    n_devices: int,
    strategy: str = "currenttobest1bin",
    progress_callback: Callable[[int, np.ndarray, float], None] | None = None,
) -> tuple[np.ndarray, float, int]:
    """自定义差分进化循环。

    特性：
    - 支持 currenttobest1bin / best1bin 两种策略
    - Per-device crossover：以设备 (x, y, θ) 三元组为原子单元进行交叉
    - θ wrapping：交叉后对角度取模 [0, 2π)
    - Early stopping：最近 20 代改善 < 0.1% 时提前终止
    - scipy 风格收敛判断：std(costs) <= atol + tol * |best_cost|

    Args:
        cost_fn: 目标函数 f(x) → float
        bounds: 边界数组 shape=(ndim, 2)，每行 [low, high]
        init_pop: 初始种群 shape=(pop_size, ndim)
        maxiter: 最大迭代代数
        tol: 相对收敛容差
        atol: 绝对收敛容差
        mutation: 变异因子范围 (F_min, F_max)
        recombination: 交叉概率 CR
        seed: 随机种子
        n_devices: 设备数量（用于 per-device crossover）
        strategy: 变异策略，"currenttobest1bin" 或 "best1bin"
        progress_callback: 每 10 代调用一次 (gen, best_vector, best_cost)

    Returns:
        (best_vector, best_cost, n_generations)
    """
    rng = np.random.default_rng(seed)
    pop_size, ndim = init_pop.shape
    lower = bounds[:, 0]
    upper = bounds[:, 1]
    f_min, f_max = mutation

    # 评估初始种群适应度
    costs = np.array([cost_fn(ind) for ind in init_pop])
    best_idx = int(np.argmin(costs))
    best_cost = costs[best_idx]
    best_vector = init_pop[best_idx].copy()

    # Early stopping 跟踪
    patience = 200
    best_cost_history: list[float] = [best_cost]

    for gen in range(1, maxiter + 1):
        for i in range(pop_size):
            # 选择变异因子 F（每个个体独立采样）
            f_val = rng.uniform(f_min, f_max)

            # 选择两个不同于 i 和 best_idx 的个体索引
            candidates = list(range(pop_size))
            candidates.remove(i)
            chosen = rng.choice(candidates, size=2, replace=False)
            r1, r2 = int(chosen[0]), int(chosen[1])

            # 变异向量
            if strategy == "best1bin":
                # Turbo 模式：mutant = best + F*(r1 - r2)
                mutant = best_vector + f_val * (init_pop[r1] - init_pop[r2])
            else:
                # 默认 currenttobest1bin：mutant = target + F*(best - target) + F*(r1 - r2)
                mutant = (
                    init_pop[i]
                    # add a scaled minimum to encourage exploration
                    + f_val * 0.1 * (upper - lower) * rng.uniform(-1, 1, size=ndim)
                    + f_val * (best_vector - init_pop[i])
                    + f_val * (init_pop[r1] - init_pop[r2])
                )

            # Per-device crossover：以 (x, y, θ) 三元组为原子单元
            trial = init_pop[i].copy()
            j_rand = rng.integers(0, n_devices)  # 保证至少一个设备来自 mutant
            for d in range(n_devices):
                if rng.random() < recombination or d == j_rand:
                    trial[3 * d: 3 * d + 3] = mutant[3 * d: 3 * d + 3]

            # θ wrapping：角度取模 [0, 2π)
            for d in range(n_devices):
                trial[3 * d + 2] %= 2 * math.pi

            # 钳位到边界内
            trial = np.clip(trial, lower, upper)

            # 贪心选择：trial 不比当前差则替换
            trial_cost = cost_fn(trial)
            if trial_cost <= costs[i]:
                init_pop[i] = trial
                costs[i] = trial_cost
                if trial_cost < best_cost:
                    best_cost = trial_cost
                    best_vector = trial.copy()

        # 更新 best_idx（种群可能整体更新）
        best_idx = int(np.argmin(costs))

        # 进度回调：每 10 代报告最优个体状态
        if progress_callback and gen % 10 == 0:
            progress_callback(gen, best_vector, best_cost)

        # Early stopping：最近 patience 代改善 < 0.1%
        best_cost_history.append(best_cost)
        if len(best_cost_history) >= patience:
            old_cost = best_cost_history[-patience]
            if old_cost > 0:
                improvement = (old_cost - best_cost) / old_cost
            else:
                improvement = 0.0
            if improvement < 0.001:
                logger.info(
                    "Early stop: cost 在 %d 代内稳定在 %.4f（改善 < 0.1%%）",
                    patience, best_cost,
                )
                return best_vector, best_cost, gen

        # scipy 风格收敛判断
        if np.std(costs) <= atol + tol * abs(best_cost):
            logger.info(
                "收敛终止：std(costs)=%.6f <= atol+tol*|best|=%.6f，第 %d 代",
                np.std(costs), atol + tol * abs(best_cost), gen,
            )
            return best_vector, best_cost, gen

    return best_vector, best_cost, maxiter


def _generate_seeds(
    devices: list[Device],
    lab: Lab,
    rng: np.random.Generator,
    workflow_edges: list[list[str]] | None = None,
    n_variants: int = 3,
    sigma_pos_frac: float = 0.05,
    sigma_theta: float = math.pi / 6,
) -> list[np.ndarray]:
    """从多个 seeder preset 生成多样性种子个体 + 变异版本。"""
    seeds: list[np.ndarray] = []
    presets = ["compact_outward", "spread_inward"]
    if workflow_edges:
        presets.append("workflow_cluster")

    for preset_name in presets:
        try:
            params = resolve_seeder_params(preset_name)
        except ValueError:
            continue
        if params is None:
            continue
        base_placements = seed_layout(devices, lab, params, workflow_edges)
        base_vec = _placements_to_vector(base_placements, devices)
        seeds.append(base_vec)

        # 变异版本：对 (x,y) 加高斯噪声 σ=5% lab 尺寸，θ 加 σ=π/6
        for _ in range(n_variants):
            variant = base_vec.copy()
            for d in range(len(devices)):
                variant[3 * d] += rng.normal(0, sigma_pos_frac * lab.width)
                variant[3 * d + 1] += rng.normal(0, sigma_pos_frac * lab.depth)
                variant[3 * d + 2] += rng.normal(0, sigma_theta)
                variant[3 * d + 2] %= 2 * math.pi
            seeds.append(variant)

    return seeds


def optimize(
    devices: list[Device],
    lab: Lab,
    constraints: list[Constraint] | None = None,
    collision_checker: Any | None = None,
    reachability_checker: Any | None = None,
    seed_placements: list[Placement] | None = None,
    maxiter: int = 200,
    popsize: int = 15,
    tol: float = 1e-6,
    seed: int | None = None,
    strategy: str = "currenttobest1bin",
    workflow_edges: list[list[str]] | None = None,
) -> list[Placement]:
    """运行差分进化优化，返回最优布局。

    Args:
        devices: 待排布的设备列表
        lab: 实验室平面图
        constraints: 用户自定义约束列表（可选）
        collision_checker: 碰撞检测实例（默认使用 MockCollisionChecker）
        reachability_checker: 可达性检测实例（默认使用 MockReachabilityChecker）
        seed_placements: 种子布局（若为 None 则自动生成）
        maxiter: 最大迭代次数
        popsize: 种群大小倍数
        tol: 收敛容差
        seed: 随机种子（用于可复现性）
        strategy: DE 变异策略（"currenttobest1bin" 或 "best1bin"）

    Returns:
        最优布局 Placement 列表
    """
    if not devices:
        return []

    if collision_checker is None:
        collision_checker = MockCollisionChecker()
    if reachability_checker is None:
        reachability_checker = MockReachabilityChecker()
    if constraints is None:
        constraints = []

    n = len(devices)

    # 构建边界：每个设备 (x, y, θ)
    # 使用较小半径作为搜索边界，让 graduated boundary penalty 处理实际越界
    # 对角线半径过于保守，会阻止长设备贴边对齐
    bounds = []
    for dev in devices:
        half_min = min(dev.bbox[0], dev.bbox[1]) / 2
        bounds.append((half_min, lab.width - half_min))   # x
        bounds.append((half_min, lab.depth - half_min))   # y
        bounds.append((0, 2 * math.pi))                     # θ
    bounds_array = np.array(bounds)

    # 生成种子个体
    if seed_placements is None:
        seed_placements = generate_initial_layout(devices, lab)

    seed_vector = _placements_to_vector(seed_placements, devices)

    # 将种子钳位到边界内
    seed_vector = np.clip(seed_vector, bounds_array[:, 0], bounds_array[:, 1])

    def cost_function(x: np.ndarray) -> float:
        placements = _vector_to_placements(x, devices)

        # 默认硬约束（碰撞 + 边界）
        hard_cost = evaluate_default_hard_constraints(
            devices, placements, lab, collision_checker
        )
        if math.isinf(hard_cost):
            return 1e18  # DE 不接受 inf，用大数替代

        # 用户自定义约束
        if constraints:
            user_cost = evaluate_constraints(
                devices, placements, lab, constraints,
                collision_checker, reachability_checker,
            )
            if math.isinf(user_cost):
                return 1e18
            return hard_cost + user_cost

        return hard_cost

    # 构建初始种群：种子个体 + 多样性种子 + 随机个体
    rng = np.random.default_rng(seed)
    pop_count = popsize * 3 * n  # scipy 默认 popsize * dim
    init_pop = rng.uniform(
        bounds_array[:, 0], bounds_array[:, 1], size=(pop_count, 3 * n)
    )
    init_pop[0] = seed_vector  # 注入原始种子

    # 多样性种子注入（多 preset + 变异版本）
    extra_seeds = _generate_seeds(devices, lab, rng, workflow_edges)
    for i, s in enumerate(extra_seeds):
        idx = i + 1  # 原始种子占 [0]
        if idx < pop_count:
            init_pop[idx] = np.clip(s, bounds_array[:, 0], bounds_array[:, 1])

    logger.info(
        "Starting DE optimization: %d devices, %d-dim, popsize=%d, maxiter=%d, strategy=%s",
        n, 3 * n, pop_count, maxiter, strategy,
    )

    # DEBUG 模式进度回调：每 10 代输出完整约束分项表格
    def _progress_cb(gen: int, best_vec: np.ndarray, best_cost_val: float) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        pls = _vector_to_placements(best_vec, devices)
        hard_bd = evaluate_default_hard_constraints_breakdown(
            devices, pls, lab, collision_checker,
        )
        lines = [f"=== DE Gen {gen} | best_cost={best_cost_val:.4f} ==="]
        lines.append(f"  {'Constraint':<45} {'Type':<6} {'Weight':>8} {'Cost':>10}")
        lines.append(f"  {'─' * 71}")
        lines.append(
            f"  {'[predefined] collision':<45} {'hard':<6} {hard_bd['collision_weight']:>8.0f} {hard_bd['collision']:>10.4f}"
        )
        lines.append(
            f"  {'[predefined] boundary':<45} {'hard':<6} {hard_bd['boundary_weight']:>8.0f} {hard_bd['boundary']:>10.4f}"
        )
        if constraints:
            user_bd = evaluate_constraints_breakdown(
                devices, pls, lab, constraints,
                collision_checker, reachability_checker,
            )
            for item in user_bd:
                lines.append(
                    f"  {item['name']:<45} {item['type']:<6} {item['weight']:>8.1f} {item['cost']:>10.4f}"
                )
        lines.append(f"  {'─' * 71}")
        lines.append(f"  {'TOTAL':<45} {'':6} {'':>8} {best_cost_val:>10.4f}")
        logger.debug("\n".join(lines))

    best_vector, best_cost, n_generations = _run_de(
        cost_fn=cost_function,
        bounds=bounds_array,
        init_pop=init_pop,
        maxiter=maxiter,
        tol=tol,
        atol=1e-3,
        mutation=(0.5, 1.0),
        recombination=0.7,
        seed=seed,
        n_devices=n,
        strategy=strategy,
        progress_callback=_progress_cb,
    )

    # 评估次数估算：每代 pop_count 次（初始 + 每代 trial）
    n_evaluations = pop_count + n_generations * pop_count

    # 最终布局分项明细（INFO 级别）
    final_placements = _vector_to_placements(best_vector, devices)
    hard_bd = evaluate_default_hard_constraints_breakdown(
        devices, final_placements, lab, collision_checker,
    )
    # success = 所有 hard 约束均满足（predefined + 用户 hard）
    all_hard_met = hard_bd["total"] == 0.0
    # 所有约束的 top violators 候选池（predefined + user）
    all_violators: list[dict] = [
        {"name": "[predefined] collision", "cost": hard_bd["collision"]},
        {"name": "[predefined] boundary", "cost": hard_bd["boundary"]},
    ]
    if constraints:
        user_bd = evaluate_constraints_breakdown(
            devices, final_placements, lab, constraints,
            collision_checker, reachability_checker,
        )
        user_total = sum(item["cost"] for item in user_bd)
        for c_item in user_bd:
            all_violators.append({"name": c_item["name"], "cost": c_item["cost"]})
            if c_item["type"] == "hard" and c_item["cost"] > 0:
                all_hard_met = False
    else:
        user_bd = []
        user_total = 0.0

    summary = [
        "DE complete: success=%s, cost=%.4f, %d gens, %d evals"
        % (all_hard_met, best_cost, n_generations, n_evaluations),
        "  Predefined: subtotal=%.4f" % hard_bd["total"],
    ]
    if constraints:
        summary.append(f"  User: subtotal={user_total:.4f}")
    top_violators = sorted(all_violators, key=lambda x: x["cost"], reverse=True)[:3]
    top_violators = [v for v in top_violators if v["cost"] > 0]
    if top_violators:
        summary.append("  Top violators:")
        for v in top_violators:
            summary.append(f"    {v['name']} = {v['cost']:.4f}")
    logger.info("\n".join(summary))

    return _vector_to_placements(best_vector, devices)


def snap_theta(placements: list[Placement], threshold_deg: float = 15.0) -> list[Placement]:
    """Snap each placement's theta to nearest 90° if within threshold.

    Returns new Placement list (does not mutate input).
    """
    threshold_rad = math.radians(threshold_deg)
    cardinals = [0, math.pi / 2, math.pi, 3 * math.pi / 2, 2 * math.pi]
    result = []
    for p in placements:
        theta_mod = p.theta % (2 * math.pi)
        best_cardinal = min(cardinals, key=lambda c: abs(theta_mod - c))
        if abs(theta_mod - best_cardinal) <= threshold_rad:
            snapped = best_cardinal % (2 * math.pi)
        else:
            snapped = p.theta
        result.append(Placement(
            device_id=p.device_id, x=p.x, y=p.y, theta=snapped, uuid=p.uuid,
        ))
    return result


def snap_theta_safe(
    placements: list[Placement],
    devices: list[Device],
    lab: Lab,
    collision_checker: Any,
    threshold_deg: float = 15.0,
) -> list[Placement]:
    """Snap theta 到基数方向，但碰撞时回退到原始角度。

    逐设备检查：snap 后如果产生碰撞或越界，则该设备保留原始 theta。
    """
    snapped = snap_theta(placements, threshold_deg)

    result = list(snapped)
    for idx, (orig, snap) in enumerate(zip(placements, snapped)):
        if abs(orig.theta - snap.theta) < 1e-9:
            continue  # 未 snap，跳过
        # 检查 snap 版本是否导致新碰撞
        test_placements = result.copy()
        test_placements[idx] = snap
        cost = evaluate_default_hard_constraints(
            devices, test_placements, lab, collision_checker, graduated=False,
        )
        if math.isinf(cost):
            result[idx] = orig  # 回退到未 snap 的角度
            logger.info(
                "snap_theta_safe: 设备 %s snap θ=%.2f→%.2f 导致碰撞，已回退",
                snap.device_id, orig.theta, snap.theta,
            )
    return result


def _placements_to_vector(
    placements: list[Placement], devices: list[Device]
) -> np.ndarray:
    """将 Placement 列表编码为 3N 维向量。

    按 devices 列表的顺序排列。若某设备在 placements 中缺失，用 (0, 0, 0) 填充。
    """
    placement_map = {p.device_id: p for p in placements}
    vec = np.zeros(3 * len(devices))
    for i, dev in enumerate(devices):
        p = placement_map.get(dev.id)
        if p is not None:
            vec[3 * i] = p.x
            vec[3 * i + 1] = p.y
            vec[3 * i + 2] = p.theta
    return vec


def _vector_to_placements(
    x: np.ndarray, devices: list[Device]
) -> list[Placement]:
    """将 3N 维向量解码为 Placement 列表。"""
    placements = []
    for i, dev in enumerate(devices):
        placements.append(
            Placement(
                device_id=dev.id,
                x=float(x[3 * i]),
                y=float(x[3 * i + 1]),
                theta=float(x[3 * i + 2] % (2 * math.pi)),
            )
        )
    return placements
