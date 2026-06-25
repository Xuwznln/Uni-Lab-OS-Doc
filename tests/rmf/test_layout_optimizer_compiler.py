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
