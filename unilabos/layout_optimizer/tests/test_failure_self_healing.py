"""失败自愈逻辑测试：解析冲突预检、breakdown 提取、聚合 violators、/optimize/auto。"""

import asyncio
import math

import httpx

from ..feasibility import compute_breakdown, precheck_conflicts
from ..mock_checkers import MockCollisionChecker, MockReachabilityChecker
from ..models import Constraint, Device, Lab, Placement
from ..parallel_optimize import _aggregate_violations


def _post_app(path: str, payload: dict) -> httpx.Response:
    """通过 ASGITransport 调用应用，避免 TestClient 在沙箱中卡住。"""
    from ..server import app

    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(path, json=payload)

    return asyncio.run(_run())


# ---------- 预检：情况 A ----------


def test_precheck_area_conflict():
    """设备总面积超过实验室面积 → area 冲突。"""
    devices = [
        Device(id="a", name="A", bbox=(4.0, 4.0)),
        Device(id="b", name="B", bbox=(4.0, 4.0)),
    ]
    lab = Lab(width=5.0, depth=5.0)
    conflicts = precheck_conflicts(devices, lab, [])
    kinds = {c.kind for c in conflicts}
    assert "area" in kinds


def test_precheck_device_too_large():
    """单台设备任意朝向都装不进实验室 → device_too_large。"""
    devices = [Device(id="big", name="Big", bbox=(6.0, 6.0))]
    lab = Lab(width=5.0, depth=10.0)  # 面积 50 > 36，排除 area 干扰
    conflicts = precheck_conflicts(devices, lab, [])
    kinds = {c.kind for c in conflicts}
    assert "device_too_large" in kinds
    assert "area" not in kinds


def test_precheck_distance_contradiction():
    """同一对 max_distance < min_distance → 直接矛盾。"""
    devices = [
        Device(id="a", name="A", bbox=(0.4, 0.4)),
        Device(id="b", name="B", bbox=(0.4, 0.4)),
    ]
    lab = Lab(width=5.0, depth=5.0)
    constraints = [
        Constraint(type="hard", rule_name="distance_less_than", params={"device_a": "a", "device_b": "b", "distance": 0.5}),
        Constraint(type="hard", rule_name="distance_greater_than", params={"device_a": "a", "device_b": "b", "distance": 1.0}),
    ]
    conflicts = precheck_conflicts(devices, lab, constraints)
    assert any(c.kind == "distance_contradiction" for c in conflicts)


def test_precheck_min_distance_exceeds_lab():
    """min_distance 大于实验室对角线 → 分不了那么远。"""
    devices = [
        Device(id="a", name="A", bbox=(0.4, 0.4)),
        Device(id="b", name="B", bbox=(0.4, 0.4)),
    ]
    lab = Lab(width=5.0, depth=5.0)  # 对角线 ≈ 7.07
    constraints = [
        Constraint(type="hard", rule_name="distance_greater_than", params={"device_a": "a", "device_b": "b", "distance": 100.0}),
    ]
    conflicts = precheck_conflicts(devices, lab, constraints)
    assert any(c.kind == "min_distance_exceeds_lab" for c in conflicts)


def test_precheck_max_below_min_spacing():
    """max_distance 小于全局 min_spacing → 边到边矛盾。"""
    devices = [
        Device(id="a", name="A", bbox=(0.4, 0.4)),
        Device(id="b", name="B", bbox=(0.4, 0.4)),
    ]
    lab = Lab(width=5.0, depth=5.0)
    constraints = [
        Constraint(type="hard", rule_name="distance_less_than", params={"device_a": "a", "device_b": "b", "distance": 0.5}),
        Constraint(type="hard", rule_name="min_spacing", params={"min_gap": 2.0}),
    ]
    conflicts = precheck_conflicts(devices, lab, constraints)
    assert any(c.kind == "max_distance_below_min_spacing" for c in conflicts)


def test_precheck_feasible_no_conflict():
    """合理输入不应误报冲突（零假阳性）。"""
    devices = [
        Device(id="a", name="A", bbox=(0.6, 0.5)),
        Device(id="b", name="B", bbox=(0.5, 0.5)),
        Device(id="c", name="C", bbox=(0.4, 0.4)),
    ]
    lab = Lab(width=5.0, depth=5.0)
    constraints = [
        Constraint(type="hard", rule_name="min_spacing", params={"min_gap": 0.1}),
        Constraint(type="soft", rule_name="minimize_distance", params={"device_a": "a", "device_b": "b"}, weight=3.0),
    ]
    assert precheck_conflicts(devices, lab, constraints) == []


# ---------- breakdown 提取 ----------


def test_compute_breakdown_detects_collision():
    """重叠布局应在 violations 中体现碰撞。"""
    devices = [
        Device(id="a", name="A", bbox=(0.8, 0.6)),
        Device(id="b", name="B", bbox=(0.8, 0.6)),
    ]
    lab = Lab(width=5.0, depth=5.0)
    placements = [
        Placement(device_id="a", x=2.5, y=2.5, theta=0.0),
        Placement(device_id="b", x=2.5, y=2.5, theta=0.0),  # 完全重叠
    ]
    checker = MockCollisionChecker()
    breakdown, violations = compute_breakdown(devices, placements, lab, [], checker)
    assert any(b["name"] == "[predefined] collision" for b in breakdown)
    assert any(v["rule"] == "no_collision" and v["cost"] > 0 for v in violations)


def test_compute_breakdown_clean_layout_no_violations():
    """分开摆放的布局没有硬约束违反。"""
    devices = [
        Device(id="a", name="A", bbox=(0.6, 0.5)),
        Device(id="b", name="B", bbox=(0.6, 0.5)),
    ]
    lab = Lab(width=5.0, depth=5.0)
    placements = [
        Placement(device_id="a", x=1.0, y=1.0, theta=0.0),
        Placement(device_id="b", x=4.0, y=4.0, theta=0.0),
    ]
    checker = MockCollisionChecker()
    _, violations = compute_breakdown(devices, placements, lab, [], checker)
    assert violations == []


# ---------- 聚合 violators ----------


def test_aggregate_violations_persistent():
    """在所有 run 都违反的约束应标记 persistent 并排在最前。"""
    results = [
        {"violations": [
            {"name": "reachability(arm, pcr)", "rule": "reachability", "type": "hard", "cost": 3.0, "weight": 5.0},
            {"name": "[predefined] collision", "rule": "no_collision", "type": "hard", "cost": 1.0, "weight": 500},
        ]},
        {"violations": [
            {"name": "reachability(arm, pcr)", "rule": "reachability", "type": "hard", "cost": 5.0, "weight": 5.0},
        ]},
    ]
    agg = _aggregate_violations(results)
    assert agg[0]["name"] == "reachability(arm, pcr)"
    assert agg[0]["persistent"] is True
    assert agg[0]["runs_violated"] == 2
    # collision 只在 1 个 run 出现 → 非 persistent
    collision = next(a for a in agg if a["rule"] == "no_collision")
    assert collision["persistent"] is False


def test_aggregate_violations_empty():
    assert _aggregate_violations([]) == []


# ---------- /optimize 响应新增字段 ----------


def test_optimize_response_has_diagnostics():
    """/optimize 响应应包含 breakdown / violations / conflicts。"""
    resp = _post_app("/optimize", {
        "devices": [
            {"id": "a", "name": "A", "size": [0.6, 0.5]},
            {"id": "b", "name": "B", "size": [0.5, 0.5]},
        ],
        "lab": {"width": 5.0, "depth": 5.0},
        "constraints": [],
        "maxiter": 30,
        "seed": 42,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "breakdown" in data
    assert "violations" in data
    assert "conflicts" in data
    assert isinstance(data["breakdown"], list)


# ---------- /optimize/auto ----------


def test_auto_precheck_short_circuit():
    """面积冲突应在预检阶段短路：de_ran False, total 0, conflicts 非空。"""
    resp = _post_app("/optimize/auto", {
        "devices": [
            {"id": "a", "name": "A", "size": [4.0, 4.0]},
            {"id": "b", "name": "B", "size": [4.0, 4.0]},
        ],
        "lab": {"width": 5.0, "depth": 5.0},
        "constraints": [],
        "seeds": [1, 2],
        "seeders": ["compact_outward"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["de_ran"] is False
    assert data["total"] == 0
    assert len(data["conflicts"]) >= 1
    assert any(c["kind"] == "area" for c in data["conflicts"])


def test_auto_feasible_multistart_succeeds():
    """可行的小实例应通过多起点并行 DE 成功。"""
    resp = _post_app("/optimize/auto", {
        "devices": [
            {"id": "a", "name": "A", "size": [0.6, 0.5]},
            {"id": "b", "name": "B", "size": [0.5, 0.5]},
            {"id": "c", "name": "C", "size": [0.4, 0.4]},
        ],
        "lab": {"width": 5.0, "depth": 5.0},
        "constraints": [],
        "seeds": [1, 2],
        "seeders": ["compact_outward"],
        "maxiter": 40,
        "max_workers": 2,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["tried"] >= 1
    assert len(data["placements"]) == 3
