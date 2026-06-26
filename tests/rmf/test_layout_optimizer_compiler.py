"""layout-optimizer → RMF 编译单测（#18 §9 / #21）。"""

from pathlib import Path

import pytest

from unilabos.sim.fleet.rmf.compiler import compile_layout_optimizer_dir, load_layout_optimizer_dir
from unilabos.sim.fleet.rmf.layout_optimizer.slug import instance_to_waypoint_name
from unilabos.sim.fleet.rmf.layout_optimizer.transfer_plan_builder import build_transfer_plan
from unilabos.sim.fleet.rmf.transfer_dispatcher import build_delivery_envelopes, transfer_to_delivery_envelope

FIXTURE_MINI = Path(__file__).resolve().parent / "fixtures" / "layout_optimizer_mini"
EXAMPLE_SCENE = (
    Path(__file__).resolve().parents[3]
    / "uni-lab-designer"
    / "layout_optimizer"
    / "agv-only"
    / "examples"
    / "scene_2026-06-16_with_turn"
)


def test_slug_waypoint_name():
    name = instance_to_waypoint_name("自动液体工作站（96孔兼容）_0", "自动液体工作站（96孔兼容）")
    assert name.startswith("wp_")
    assert name.endswith("_0")


def test_build_transfer_plan_mini():
    artifacts = load_layout_optimizer_dir(FIXTURE_MINI)
    plan = build_transfer_plan(artifacts)
    assert plan["meta"]["source"] == "layout_optimizer"
    assert len(plan["deviceWaypoints"]) == 2
    assert len(plan["transfers"]) == 1
    t0 = plan["transfers"][0]
    assert t0["readyTimeMin"] == 35
    assert t0["fromWaypoint"] != t0["toWaypoint"]


def test_compile_layout_optimizer_mini():
    ir, building, semantic, plan = compile_layout_optimizer_dir(FIXTURE_MINI, lab_uuid="lab-test", scene_hash="h-mini")
    level = building["levels"]["L1"]
    names = {row[3] for row in level["vertices"]}
    assert any(n.startswith("wp_") for n in names)
    assert "nav_0" in names
    assert len(level["lanes"]) >= 1
    assert semantic.get("waypoint_to_instance")
    assert semantic.get("transfer_plan_ref", {}).get("transfer_count") == 1
    assert len(plan["transfers"]) == 1
    assert not ir.has_errors()


def test_finalize_building_for_dashboard_mini(tmp_path):
    from unilabos.sim.fleet.rmf.compiler.reference_image_export import finalize_building_for_dashboard
    from unilabos.sim.fleet.rmf.layout_optimizer.ingest import load_layout_optimizer_dir

    artifacts = load_layout_optimizer_dir(FIXTURE_MINI)
    ir, _, _, _ = compile_layout_optimizer_dir(FIXTURE_MINI, lab_uuid="lab-test")
    building, png_path, bounds = finalize_building_for_dashboard(
        ir, tmp_path, lab=artifacts.lab, placements=artifacts.placements, layout_dir=FIXTURE_MINI
    )
    assert building["coordinate_system"] == "reference_image"
    level = building["levels"]["L1"]
    assert level["drawing"]["filename"] == "L1_floorplan.png"
    assert len(level["measurements"]) >= 1
    assert png_path.is_file() and png_path.stat().st_size > 1000
    assert bounds.width_m > 0


def test_transfer_to_delivery_envelope():
    artifacts = load_layout_optimizer_dir(FIXTURE_MINI)
    plan = build_transfer_plan(artifacts)
    env = transfer_to_delivery_envelope(plan["transfers"][0], epoch_ms=1_000_000)
    req = env["request"]
    assert req["category"] == "delivery"
    assert req["unix_millis_earliest_start_time"] == 1_000_000 + 35 * 60_000
    assert req["description"]["pickup"]["place"] == plan["transfers"][0]["fromWaypoint"]


def test_delivery_envelope_window():
    artifacts = load_layout_optimizer_dir(FIXTURE_MINI)
    plan = build_transfer_plan(artifacts)
    envs = build_delivery_envelopes(plan, epoch_ms=0, ready_min_from=0, ready_min_to=100, max_count=10)
    assert len(envs) == 1


def _mini_ir(route_overrides=None):
    from unilabos.sim.fleet.rmf.compiler.layout_optimizer_to_rmf_ir import build_layout_optimizer_rmf_ir

    artifacts = load_layout_optimizer_dir(FIXTURE_MINI)
    return build_layout_optimizer_rmf_ir(artifacts, lab_uuid="lab-test", route_overrides=route_overrides)


def test_route_override_disable_lane():
    base_lanes = len(_mini_ir().levels[0].lanes)
    ir = _mini_ir({"disableLanes": [["nav_0", "nav_1"]]})
    level = ir.levels[0]
    assert len(level.lanes) == base_lanes - 1
    i0, i1 = level.index_of("nav_0"), level.index_of("nav_1")
    assert not any({ln.v1, ln.v2} == {i0, i1} for ln in level.lanes)
    assert any(d.code == "route_override_applied" for d in ir.diagnostics)


def test_route_override_add_and_set_speed():
    base = _mini_ir()
    wp_names = [v.name for v in base.levels[0].vertices if v.name.startswith("wp_")]
    assert len(wp_names) == 2
    overrides = {
        "addLanes": [{"v1": wp_names[0], "v2": wp_names[1], "bidirectional": True, "speedLimit": 0.3}],
        "setSpeedLimit": [{"v1": "nav_0", "v2": "nav_1", "speedLimit": 0.15}],
    }
    ir = _mini_ir(overrides)
    level = ir.levels[0]
    assert len(level.lanes) == len(base.levels[0].lanes) + 1
    ia, ib = level.index_of(wp_names[0]), level.index_of(wp_names[1])
    added = next(ln for ln in level.lanes if {ln.v1, ln.v2} == {ia, ib})
    assert abs(added.speed_limit - 0.3) < 1e-9
    i0, i1 = level.index_of("nav_0"), level.index_of("nav_1")
    nav_lane = next(ln for ln in level.lanes if {ln.v1, ln.v2} == {i0, i1})
    assert abs(nav_lane.speed_limit - 0.15) < 1e-9


def test_route_override_unknown_waypoint_warns():
    ir = _mini_ir({"disableLanes": [["nav_999", "nav_0"]]})
    assert any(d.code == "route_override_unknown_waypoint" for d in ir.diagnostics)


def test_compile_layout_optimizer_dir_with_route_overrides():
    ir, building, _semantic, _plan = compile_layout_optimizer_dir(
        FIXTURE_MINI,
        lab_uuid="lab-test",
        route_overrides={"disableLanes": [["nav_0", "nav_1"]]},
    )
    assert any(d["code"] == "route_override_applied" for d in ir.diagnostics_as_dicts())
    assert not ir.has_errors()
    assert len(building["levels"]["L1"]["lanes"]) == 0


@pytest.mark.skipif(not EXAMPLE_SCENE.is_dir(), reason="monorepo 示例目录不可用")
def test_compile_real_example_scene():
    ir, building, semantic, plan = compile_layout_optimizer_dir(
        EXAMPLE_SCENE, lab_uuid="lab-real", scene_hash="h-real", snap_devices_to_nav=True
    )
    assert len(plan["transfers"]) > 1000
    assert len(plan["deviceWaypoints"]) >= 50
    level = building["levels"]["L1"]
    assert len(level["vertices"]) > 100
    assert len(level["lanes"]) > 100
    assert semantic["transfer_plan_ref"]["makespan_min"] == 13020
