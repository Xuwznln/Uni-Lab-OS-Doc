"""差分进化优化器端到端测试。"""

import math

from ..mock_checkers import MockCollisionChecker
from ..models import Device, Lab, Placement
import pytest
from ..optimizer import optimize, snap_theta


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
