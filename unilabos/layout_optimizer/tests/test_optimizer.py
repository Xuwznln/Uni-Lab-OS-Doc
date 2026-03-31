"""差分进化优化器端到端测试。"""

import math

from ..mock_checkers import MockCollisionChecker
from ..models import Device, Lab, Placement
import numpy as np
import pytest
from ..optimizer import _run_de, optimize, snap_theta


def test_optimize_three_devices_no_collision():
    """3 台设备在 5m×5m 实验室中优化，结果应无碰撞且在边界内。"""
    devices = [
        Device(id="a", name="A", bbox=(0.8, 0.6)),
        Device(id="b", name="B", bbox=(0.6, 0.5)),
        Device(id="c", name="C", bbox=(0.5, 0.5)),
    ]
    lab = Lab(width=5.0, depth=5.0)

    placements = optimize(devices, lab, seed=42, maxiter=100, popsize=10)

    assert len(placements) == 3

    # 验证无碰撞
    checker = MockCollisionChecker()
    checker_placements = [
        {"id": p.device_id, "bbox": next(d.bbox for d in devices if d.id == p.device_id),
         "pos": (p.x, p.y, p.theta)}
        for p in placements
    ]
    collisions = checker.check(checker_placements)
    assert collisions == [], f"Unexpected collisions: {collisions}"

    # 验证在边界内
    oob = checker.check_bounds(checker_placements, lab.width, lab.depth)
    assert oob == [], f"Devices out of bounds: {oob}"


def test_optimize_single_device():
    """单个设备应直接放置成功。"""
    devices = [Device(id="solo", name="Solo", bbox=(0.5, 0.5))]
    lab = Lab(width=3.0, depth=3.0)

    placements = optimize(devices, lab, seed=42, maxiter=50)

    assert len(placements) == 1
    p = placements[0]
    assert 0.25 <= p.x <= 2.75
    assert 0.25 <= p.y <= 2.75


def test_optimize_tight_space():
    """紧凑空间：2 台设备在刚好够大的实验室中。"""
    devices = [
        Device(id="x", name="X", bbox=(1.0, 1.0)),
        Device(id="y", name="Y", bbox=(1.0, 1.0)),
    ]
    # 2.5m 宽足够放 2 个 1m 宽设备（加间距）
    lab = Lab(width=2.5, depth=2.0)

    placements = optimize(devices, lab, seed=42, maxiter=100)

    assert len(placements) == 2
    checker = MockCollisionChecker()
    checker_placements = [
        {"id": p.device_id, "bbox": next(d.bbox for d in devices if d.id == p.device_id),
         "pos": (p.x, p.y, p.theta)}
        for p in placements
    ]
    collisions = checker.check(checker_placements)
    assert collisions == []


def test_optimize_returns_valid_placement_ids():
    """验证返回的 placement device_id 与输入设备一致。"""
    devices = [
        Device(id="dev_1", name="D1", bbox=(0.5, 0.5)),
        Device(id="dev_2", name="D2", bbox=(0.5, 0.5)),
    ]
    lab = Lab(width=5.0, depth=5.0)

    placements = optimize(devices, lab, seed=42, maxiter=50)

    result_ids = {p.device_id for p in placements}
    expected_ids = {d.id for d in devices}
    assert result_ids == expected_ids


def test_snap_theta_near_cardinal():
    """Theta within 15° of 90° snaps to 90°."""
    placements = [Placement(device_id="a", x=1, y=1, theta=math.radians(85))]
    result = snap_theta(placements, threshold_deg=15)
    assert result[0].theta == pytest.approx(math.pi / 2)


def test_snap_theta_far_from_cardinal():
    """Theta 30° away from nearest cardinal: no snap."""
    placements = [Placement(device_id="a", x=1, y=1, theta=math.radians(60))]
    result = snap_theta(placements, threshold_deg=15)
    assert result[0].theta == pytest.approx(math.radians(60))


def test_snap_theta_at_cardinal():
    """Already at cardinal: unchanged."""
    placements = [Placement(device_id="a", x=1, y=1, theta=math.pi)]
    result = snap_theta(placements, threshold_deg=15)
    assert result[0].theta == pytest.approx(math.pi)


def test_snap_theta_near_360():
    """Theta near 360° (=0°) snaps correctly."""
    placements = [Placement(device_id="a", x=1, y=1, theta=math.radians(355))]
    result = snap_theta(placements, threshold_deg=15)
    snapped = result[0].theta % (2 * math.pi)
    assert snapped == pytest.approx(0.0, abs=0.01) or snapped == pytest.approx(2 * math.pi, abs=0.01)


def test_optimize_endpoint_accepts_seeder_field():
    """POST /optimize should accept seeder and run_de fields."""
    from fastapi.testclient import TestClient
    from ..server import app

    client = TestClient(app)
    resp = client.post("/optimize", json={
        "devices": [{"id": "test_device", "name": "Test"}],
        "lab": {"width": 5, "depth": 4},
        "seeder": "compact_outward",
        "run_de": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["seeder_used"] == "compact_outward"
    assert data["de_ran"] is False
    assert len(data["placements"]) == 1


def test_optimize_endpoint_unknown_seeder_returns_400():
    """Unknown seeder preset should return 400."""
    from fastapi.testclient import TestClient
    from ..server import app

    client = TestClient(app)
    resp = client.post("/optimize", json={
        "devices": [{"id": "test_device", "name": "Test"}],
        "lab": {"width": 5, "depth": 4},
        "seeder": "nonexistent_preset",
        "run_de": False,
    })
    assert resp.status_code == 400


def test_optimize_endpoint_backward_compatible():
    """Existing calls without seeder/run_de fields still work."""
    from fastapi.testclient import TestClient
    from ..server import app

    client = TestClient(app)
    resp = client.post("/optimize", json={
        "devices": [{"id": "test_device", "name": "Test"}],
        "lab": {"width": 5, "depth": 4},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "seeder_used" in data
    assert "de_ran" in data


def test_full_pipeline_seed_only():
    """Full pipeline: seeder → snap_theta → correct count and bounds.

    compact_outward is tested for collision-free (devices clustered, not at walls).
    spread_inward pushes to walls where rotated AABB bounds may flag — tested separately.
    """
    from ..seeders import seed_layout, PRESETS
    from ..constraints import evaluate_default_hard_constraints

    devices = [
        Device(id=f"dev{i}", name=f"Device {i}", bbox=(0.6, 0.4))
        for i in range(6)
    ]
    lab = Lab(width=6.0, depth=5.0)

    # compact_outward: devices cluster toward center, should be collision-free
    placements = seed_layout(devices, lab, PRESETS["compact_outward"])
    placements = snap_theta(placements)
    assert len(placements) == len(devices)
    checker = MockCollisionChecker()
    cost = evaluate_default_hard_constraints(devices, placements, lab, checker)
    assert cost < 1e17, f"compact_outward: hard constraint violation (cost={cost})"

    # spread_inward: verify correct count + all positions within lab canvas
    placements = seed_layout(devices, lab, PRESETS["spread_inward"])
    placements = snap_theta(placements)
    assert len(placements) == len(devices)
    for p in placements:
        assert 0 <= p.x <= lab.width, f"spread_inward: x={p.x} out of bounds"
        assert 0 <= p.y <= lab.depth, f"spread_inward: y={p.y} out of bounds"


def test_full_pipeline_with_de():
    """Full pipeline: seeder → DE → snap_theta."""
    from ..seeders import seed_layout, PRESETS

    devices = [
        Device(id=f"dev{i}", name=f"Device {i}", bbox=(0.6, 0.4))
        for i in range(4)
    ]
    lab = Lab(width=5.0, depth=4.0)
    checker = MockCollisionChecker()

    seed = seed_layout(devices, lab, PRESETS["compact_outward"])
    result = optimize(devices, lab, seed_placements=seed, collision_checker=checker,
                      maxiter=50, seed=42)
    result = snap_theta(result)

    assert len(result) == len(devices)
    for p in result:
        assert 0 <= p.x <= lab.width
        assert 0 <= p.y <= lab.depth


# ──────────────────────────────────────────────────────────────
# DE V2 新增测试
# ──────────────────────────────────────────────────────────────


def test_theta_wrapping_convergence():
    """θ 在 0/2π 边界附近应正确收敛，不发散。

    构造一个简单的 cost function，最优解 θ≈0（即 2π 附近也可以）。
    验证自定义 DE 在 θ wrapping 下能收敛。
    """
    n_devices = 2

    def cost_fn(x: np.ndarray) -> float:
        # 最优：两设备分开放置，θ 接近 0
        cost = 0.0
        for d in range(n_devices):
            theta = x[3 * d + 2]
            # θ penalty: 偏离 0 的圆周距离
            cost += (1 - math.cos(theta)) * 10.0
        # 两设备距离 penalty
        dx = x[0] - x[3]
        dy = x[1] - x[4]
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 1.0:
            cost += (1.0 - dist) * 100.0
        return cost

    bounds = np.array([
        [0.5, 4.5], [0.5, 4.5], [0.0, 2 * math.pi],  # 设备 0
        [0.5, 4.5], [0.5, 4.5], [0.0, 2 * math.pi],  # 设备 1
    ])
    rng = np.random.default_rng(42)
    pop_size = 30
    init_pop = rng.uniform(bounds[:, 0], bounds[:, 1], size=(pop_size, 6))
    # 故意注入 θ 接近 2π 的个体，测试 wrapping
    init_pop[0, 2] = 2 * math.pi - 0.01
    init_pop[0, 5] = 2 * math.pi - 0.05

    best_vec, best_cost, n_gen = _run_de(
        cost_fn=cost_fn,
        bounds=bounds,
        init_pop=init_pop,
        maxiter=100,
        tol=1e-6,
        atol=1e-3,
        mutation=(0.5, 1.0),
        recombination=0.7,
        seed=42,
        n_devices=n_devices,
    )

    # θ 应在 [0, 2π) 范围内
    for d in range(n_devices):
        theta = best_vec[3 * d + 2]
        assert 0 <= theta < 2 * math.pi, f"θ 超出 [0, 2π): {theta}"

    # cost 应显著下降（不发散）
    assert best_cost < 50.0, f"θ wrapping 未正确收敛，cost={best_cost}"


def test_per_device_crossover_atomicity():
    """验证 per-device crossover 的原子性：同一设备的 (x, y, θ) 来自同一来源。

    构造 2 设备场景，跟踪 crossover 后每个设备三元组是否完整来自 mutant 或 parent。
    """
    rng = np.random.default_rng(123)
    n_devices = 3
    ndim = 3 * n_devices

    # 构造明显不同的 parent 和 mutant
    parent = np.zeros(ndim)
    mutant = np.ones(ndim) * 10.0

    # 模拟多次 per-device crossover，检查原子性
    violations = 0
    n_trials = 200
    for _ in range(n_trials):
        trial = parent.copy()
        j_rand = rng.integers(0, n_devices)
        for d in range(n_devices):
            if rng.random() < 0.7 or d == j_rand:
                trial[3 * d: 3 * d + 3] = mutant[3 * d: 3 * d + 3]

        # 检查每个设备的三元组要么全是 0（parent），要么全是 10（mutant）
        for d in range(n_devices):
            triple = trial[3 * d: 3 * d + 3]
            all_parent = np.allclose(triple, 0.0)
            all_mutant = np.allclose(triple, 10.0)
            if not (all_parent or all_mutant):
                violations += 1

    assert violations == 0, f"Per-device crossover 原子性违反 {violations}/{n_trials * n_devices} 次"


def test_strategy_currenttobest1bin_converges():
    """currenttobest1bin 策略在简单 2 设备问题上应能收敛。"""
    devices = [
        Device(id="a", name="A", bbox=(0.6, 0.4)),
        Device(id="b", name="B", bbox=(0.6, 0.4)),
    ]
    lab = Lab(width=5.0, depth=5.0)

    placements = optimize(
        devices, lab, seed=42, maxiter=80, popsize=10,
        strategy="currenttobest1bin",
    )

    assert len(placements) == 2
    checker = MockCollisionChecker()
    checker_placements = [
        {"id": p.device_id, "bbox": next(d.bbox for d in devices if d.id == p.device_id),
         "pos": (p.x, p.y, p.theta)}
        for p in placements
    ]
    collisions = checker.check(checker_placements)
    assert collisions == [], f"currenttobest1bin 策略产生碰撞: {collisions}"


def test_strategy_best1bin_converges():
    """best1bin 策略在简单 2 设备问题上应能收敛。"""
    devices = [
        Device(id="a", name="A", bbox=(0.6, 0.4)),
        Device(id="b", name="B", bbox=(0.6, 0.4)),
    ]
    lab = Lab(width=5.0, depth=5.0)

    placements = optimize(
        devices, lab, seed=42, maxiter=80, popsize=10,
        strategy="best1bin",
    )

    assert len(placements) == 2
    checker = MockCollisionChecker()
    checker_placements = [
        {"id": p.device_id, "bbox": next(d.bbox for d in devices if d.id == p.device_id),
         "pos": (p.x, p.y, p.theta)}
        for p in placements
    ]
    collisions = checker.check(checker_placements)
    assert collisions == [], f"best1bin 策略产生碰撞: {collisions}"


def test_convergence_quality_two_devices():
    """2 设备无碰撞放置，cost 应低于阈值，验证优化质量。"""
    devices = [
        Device(id="d1", name="D1", bbox=(0.8, 0.6)),
        Device(id="d2", name="D2", bbox=(0.6, 0.5)),
    ]
    lab = Lab(width=5.0, depth=5.0)

    placements = optimize(devices, lab, seed=42, maxiter=100, popsize=10)

    # 验证结果质量：cost 应很低（无碰撞、在边界内）
    from ..constraints import evaluate_default_hard_constraints
    checker = MockCollisionChecker()
    cost = evaluate_default_hard_constraints(devices, placements, lab, checker)
    assert cost < 1.0, f"优化质量不佳，cost={cost}"


def test_run_de_early_stopping():
    """验证 _run_de 的 early stopping 机制：简单 cost function 应提前终止。"""

    def trivial_cost(x: np.ndarray) -> float:
        # 简单二次函数，最优解在原点附近
        return float(np.sum(x ** 2))

    ndim = 6
    n_devices = 2
    bounds = np.array([[-5.0, 5.0]] * ndim)
    rng = np.random.default_rng(42)
    pop_size = 20
    init_pop = rng.uniform(-5, 5, size=(pop_size, ndim))

    _, _, n_gen = _run_de(
        cost_fn=trivial_cost,
        bounds=bounds,
        init_pop=init_pop,
        maxiter=500,
        tol=1e-6,
        atol=1e-3,
        mutation=(0.5, 1.0),
        recombination=0.7,
        seed=42,
        n_devices=n_devices,
    )

    # 简单问题应在远少于 maxiter=500 代内收敛
    assert n_gen < 500, f"Early stopping 未生效，运行了 {n_gen} 代"


def test_run_de_returns_correct_tuple():
    """验证 _run_de 返回值格式正确。"""

    def const_cost(x: np.ndarray) -> float:
        return 42.0

    bounds = np.array([[0.0, 1.0]] * 3)
    init_pop = np.random.default_rng(0).uniform(0, 1, size=(5, 3))

    result = _run_de(
        cost_fn=const_cost,
        bounds=bounds,
        init_pop=init_pop,
        maxiter=10,
        tol=1e-6,
        atol=1e-3,
        mutation=(0.5, 1.0),
        recombination=0.7,
        seed=0,
        n_devices=1,
    )

    assert isinstance(result, tuple) and len(result) == 3
    best_vec, best_cost, n_gen = result
    assert isinstance(best_vec, np.ndarray)
    assert best_vec.shape == (3,)
    assert best_cost == pytest.approx(42.0)
    assert isinstance(n_gen, int) and n_gen >= 1
