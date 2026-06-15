"""多起点并行 DE 编排：失败自愈逻辑的"情况 B"处理层。

设计动机（见 skill 文档"失败自愈"）：单进程 ``optimize`` 是 CPU 密集的纯
Python/numpy 循环，靠并发 HTTP 打同一个端点拿不到真并行。这里用 **多进程**
（``multiprocessing`` spawn）把多个 (seed, seeder) 起点真正并行跑，并实现：

- **首成功即终止**：任一 worker 返回可行布局（success=True）立即 ``terminate``
  掉其余进程并返回。
- **全败则聚合罪魁**：所有起点都失败时，跨 run 聚合每条硬约束的残余违反，
  找出"在所有 run 里都违反"的约束（persistent），供 agent 做针对性的放宽建议。

网格按"铺垫 (c)"收敛为 **多 seed × 少量 seeder**，``maxiter`` 固定取较大值并
依赖 DE 内置 early-stopping，不再作为独立网格维度。

本模块**不导入 server**，且依赖的 optimizer/constraints/seeders 等导入无重副
作用，因此可被 spawn 出来的子进程安全导入。
"""

from __future__ import annotations

import math
import multiprocessing as mp
import os
from collections import defaultdict
from typing import Any

from .models import Device, Lab, Opening, Placement

# ---------- 设备 / 约束的可序列化打包 ----------


def device_to_payload(dev: Device) -> dict[str, Any]:
    """Device → 可 pickle 的 dict（跨进程传递）。"""
    return {
        "id": dev.id,
        "name": dev.name,
        "bbox": list(dev.bbox),
        "device_type": dev.device_type,
        "height": dev.height,
        "origin_offset": list(dev.origin_offset),
        "openings": [
            {"direction": list(o.direction), "label": o.label} for o in dev.openings
        ],
        "uuid": dev.uuid,
    }


def _payload_to_device(d: dict[str, Any]) -> Device:
    return Device(
        id=d["id"],
        name=d.get("name", d["id"]),
        bbox=tuple(d["bbox"]),
        device_type=d.get("device_type", "static"),
        height=d.get("height", 0.4),
        origin_offset=tuple(d.get("origin_offset", (0.0, 0.0))),
        openings=[
            Opening(direction=tuple(o["direction"]), label=o.get("label", ""))
            for o in d.get("openings", [])
        ],
        uuid=d.get("uuid", ""),
    )


# ---------- 单起点 worker（在子进程中执行） ----------


def _solve_one(task: dict[str, Any]) -> dict[str, Any]:
    """在子进程中跑一个 (seed, seeder) 起点的完整优化 + 评估。

    返回可 pickle 的结果 dict：
        {seed, seeder, success, cost, placements, violations}
    """
    from .constraints import evaluate_constraints, evaluate_default_hard_constraints
    from .feasibility import compute_breakdown
    from .mock_checkers import MockCollisionChecker, MockReachabilityChecker
    from .models import Constraint
    from .optimizer import optimize, snap_theta_safe
    from .seeders import resolve_seeder_params, seed_layout

    devices = [_payload_to_device(d) for d in task["devices"]]
    lab = Lab(width=task["lab"]["width"], depth=task["lab"]["depth"])
    constraints = [
        Constraint(
            type=c["type"],
            rule_name=c["rule_name"],
            params=c["params"],
            weight=c["weight"],
        )
        for c in task["constraints"]
    ]
    workflow_edges = task["workflow_edges"] or None

    checker = MockCollisionChecker()
    reach = MockReachabilityChecker(task["arm_reach"] or None)

    # 用指定 seeder preset 生成种子布局（C 轴）
    params = resolve_seeder_params(task["seeder"], task["seeder_overrides"] or None)
    seed_placements = seed_layout(devices, lab, params, workflow_edges)

    result_placements = optimize(
        devices=devices,
        lab=lab,
        constraints=constraints,
        collision_checker=checker,
        reachability_checker=reach,
        seed_placements=seed_placements,
        maxiter=task["maxiter"],
        seed=task["seed"],
        strategy=task["strategy"],
        workflow_edges=workflow_edges,
        angle_granularity=task["angle_granularity"],
        angle_mode=task["angle_mode"],
        mutation=tuple(task["mutation"]),
        theta_mutation=tuple(task["theta_mutation"]) if task["theta_mutation"] else None,
        recombination=task["recombination"],
        crossover_mode=task["crossover_mode"],
    )

    if task["snap_cardinal"] and task["angle_granularity"] is None:
        result_placements = snap_theta_safe(result_placements, devices, lab, checker)

    # 二值模式复核 pass/fail
    final_cost = evaluate_default_hard_constraints(
        devices, result_placements, lab, checker, graduated=False,
    )
    if constraints and not math.isinf(final_cost):
        user_hard = evaluate_constraints(
            devices, result_placements, lab, constraints, checker, reach,
            graduated=False,
        )
        if math.isinf(user_hard):
            final_cost = math.inf

    _, violations = compute_breakdown(
        devices, result_placements, lab, constraints, checker, reach,
    )

    return {
        "seed": task["seed"],
        "seeder": task["seeder"],
        "success": not math.isinf(final_cost),
        "cost": final_cost,
        "placements": [
            {"device_id": p.device_id, "x": p.x, "y": p.y, "theta": p.theta}
            for p in result_placements
        ],
        "violations": violations,
    }


# ---------- 多起点编排 ----------


def _aggregate_violations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨所有失败 run 聚合硬约束违反，找出 persistent（每个 run 都违反）的罪魁。"""
    if not results:
        return []

    n = len(results)
    per_name_costs: dict[str, list[float]] = defaultdict(list)
    per_name_meta: dict[str, dict[str, Any]] = {}
    name_run_count: dict[str, int] = defaultdict(int)

    for r in results:
        seen: set[str] = set()
        for v in r["violations"]:
            name = v["name"]
            per_name_costs[name].append(v["cost"])
            per_name_meta[name] = {"rule": v["rule"], "type": v["type"]}
            seen.add(name)
        for name in seen:
            name_run_count[name] += 1

    agg: list[dict[str, Any]] = []
    for name, count in name_run_count.items():
        costs = per_name_costs[name]
        agg.append(
            {
                "name": name,
                "rule": per_name_meta[name]["rule"],
                "type": per_name_meta[name]["type"],
                "runs_violated": count,
                "total_runs": n,
                "mean_residual": sum(costs) / len(costs),
                "persistent": count == n,
            }
        )

    # persistent 优先，其次按平均残余降序
    agg.sort(key=lambda a: (not a["persistent"], -a["mean_residual"]))
    return agg


def run_multistart(
    devices: list[Device],
    lab: Lab,
    constraints: list,
    *,
    seeds: list[int],
    seeders: list[str],
    maxiter: int,
    workflow_edges: list[list[str]] | None,
    angle_granularity: int | None,
    angle_mode: str,
    strategy: str,
    mutation: list[float],
    theta_mutation: list[float] | None,
    recombination: float,
    crossover_mode: str,
    snap_cardinal: bool,
    arm_reach: dict[str, float] | None,
    seeder_overrides: dict | None,
    max_workers: int | None = None,
) -> dict[str, Any]:
    """并行跑 seeds × seeders 个起点，首成功即终止其余。

    Returns:
        {
          "success": bool,
          "winner": result dict | None,   # 成功的起点；全败时为 cost 最低者
          "tried": int,                   # 实际完成的 run 数
          "total": int,                   # 计划的起点总数
          "violations": [...],            # 全败时的聚合罪魁（persistent 优先）
        }
    """
    device_payloads = [device_to_payload(d) for d in devices]
    constraint_payloads = [
        {"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight}
        for c in constraints
    ]
    lab_payload = {"width": lab.width, "depth": lab.depth}

    base = {
        "devices": device_payloads,
        "lab": lab_payload,
        "constraints": constraint_payloads,
        "maxiter": maxiter,
        "workflow_edges": workflow_edges or [],
        "angle_granularity": angle_granularity,
        "angle_mode": angle_mode,
        "strategy": strategy,
        "mutation": list(mutation),
        "theta_mutation": list(theta_mutation) if theta_mutation else None,
        "recombination": recombination,
        "crossover_mode": crossover_mode,
        "snap_cardinal": snap_cardinal,
        "arm_reach": arm_reach or {},
        "seeder_overrides": seeder_overrides or {},
    }

    tasks = [
        {**base, "seed": s, "seeder": sd}
        for sd in seeders
        for s in seeds
    ]
    total = len(tasks)
    if total == 0:
        return {"success": False, "winner": None, "tried": 0, "total": 0, "violations": []}

    n_workers = max_workers or min(total, os.cpu_count() or 1)
    n_workers = max(1, n_workers)

    results: list[dict[str, Any]] = []
    winner: dict[str, Any] | None = None

    # spawn：避免从（asyncio.to_thread 的）线程 fork 带来的死锁，且跨平台一致
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=n_workers) as pool:
        for res in pool.imap_unordered(_solve_one, tasks):
            results.append(res)
            if res["success"]:
                winner = res
                pool.terminate()  # 杀掉其余仍在跑的起点
                break

    if winner is not None:
        return {
            "success": True,
            "winner": winner,
            "tried": len(results),
            "total": total,
            "violations": [],
        }

    best = min(results, key=lambda r: r["cost"]) if results else None
    return {
        "success": False,
        "winner": best,
        "tried": len(results),
        "total": total,
        "violations": _aggregate_violations(results),
    }
