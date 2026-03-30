"""差分进化布局优化器。

编码：N 个设备 → 3N 维向量 [x0, y0, θ0, x1, y1, θ1, ...]
使用 scipy.optimize.differential_evolution 进行全局优化。
初始布局（Pencil/回退）注入为种群种子个体加速收敛。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from scipy.optimize import differential_evolution

from .constraints import evaluate_constraints, evaluate_default_hard_constraints
from .mock_checkers import MockCollisionChecker, MockReachabilityChecker
from .models import Constraint, Device, Lab, Placement
from .pencil_integration import generate_initial_layout

logger = logging.getLogger(__name__)


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

    # 构建初始种群：种子个体 + 随机个体
    rng = np.random.default_rng(seed)
    pop_count = popsize * 3 * n  # scipy 默认 popsize * dim
    init_pop = rng.uniform(
        bounds_array[:, 0], bounds_array[:, 1], size=(pop_count, 3 * n)
    )
    init_pop[0] = seed_vector  # 注入种子

    logger.info(
        "Starting DE optimization: %d devices, %d-dim, popsize=%d, maxiter=%d",
        n, 3 * n, pop_count, maxiter,
    )

    # Early stopping: stop when cost hasn't improved by >0.1% for 20 generations
    _best_costs: list[float] = []
    _patience = 20

    def _early_stop_callback(xk, convergence=0):
        cost = cost_function(xk)
        _best_costs.append(cost)
        if len(_best_costs) >= _patience:
            recent = _best_costs[-_patience:]
            if recent[0] > 0:
                improvement = (recent[0] - recent[-1]) / recent[0]
            else:
                improvement = 0.0
            if improvement < 0.001:  # < 0.1% improvement over last 20 gens
                logger.info("Early stop: cost stable at %.4f for %d generations", cost, _patience)
                return True
        return False

    result = differential_evolution(
        cost_function,
        bounds=list(bounds),
        init=init_pop,
        maxiter=maxiter,
        tol=tol,
        atol=1e-3,
        mutation=(0.5, 1.0),
        recombination=0.7,
        seed=seed,
        disp=False,
        callback=_early_stop_callback,
    )

    logger.info(
        "DE optimization complete: success=%s, cost=%.4f, iterations=%d, evaluations=%d",
        result.success, result.fun, result.nit, result.nfev,
    )

    return _vector_to_placements(result.x, devices)


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
                theta=float(x[3 * i + 2]),
            )
        )
    return placements
