from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import warnings

from unilabos.queries.engine import QueryEngine
from unilabos.queries.labutopia import (
    LabUtopiaAssetCardSource,
    LabUtopiaSceneSource,
    LabUtopiaTaskConfigSource,
)
from unilabos.queries.labutopia.asset_card_generator import (
    _source_mix_from_cards,
    generate_asset_cards,
    generate_asset_cards_to_directory,
)
from unilabos.queries.labutopia.action_smoke import build_action_smoke
from unilabos.queries.labutopia.scene_source import labutopia_asset_id_from_prim_path
from unilabos.queries.labutopia.task_configs import _target_entries_from_config
from unilabos.queries.labutopia.task_report import generate_task_report
from unilabos.queries.models import Pose, State


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "labutopia"


class FakeUsdSource:
    name = "fake_usd"

    def __init__(self, usd_path):
        self.usd_path = str(usd_path)

    def query_pose(self, target, frame=None):
        if target not in {"/World/target_button", "/World/target_button/button"}:
            return None
        xyz = [1.02, 2.0, 3.0] if target.endswith("/button") else [1.0, 2.0, 3.0]
        return Pose(
            xyz=xyz,
            frame_id=frame or "labutopia_world",
            source=self.name,
            metadata={"usd_path": self.usd_path, "prim_path": target},
        )

    def query_state(self, target):
        if target not in {"/World/target_button", "/World/target_button/button"}:
            return None
        center = [1.02, 2.0, 3.0] if target.endswith("/button") else [1.0, 2.0, 3.0]
        return State(
            name=target,
            source=self.name,
            values={
                "prim_path": target,
                "type_name": "Xform",
                "children": [] if target.endswith("/button") else ["/World/target_button/button"],
                "usd_path": self.usd_path,
                "bbox_min": [center[0] - 0.1, center[1] - 0.1, center[2] - 0.1],
                "bbox_max": [center[0] + 0.1, center[1] + 0.1, center[2] + 0.1],
                "bbox_size": [0.2, 0.2, 0.2],
            },
        )

    def query_affordance(self, target, kind=None):
        return []

    def query_action_schema(self, action):
        return None

    def query_safety_zones(self):
        return []


class LabUtopiaSourceTest(unittest.TestCase):
    def test_asset_cards_expose_pose_state_affordances_and_safety(self):
        source = LabUtopiaAssetCardSource.from_directory(FIXTURE_ROOT / "asset_cards")
        pose = source.query_pose("/World/beaker1")
        state = source.query_state("World__beaker1")
        affordances = source.query_affordance("World__beaker1")
        safety_zones = source.query_safety_zones()

        self.assertAlmostEqual(12.023525, pose.xyz[0], places=6)
        self.assertAlmostEqual(-0.16243, pose.xyz[1], places=6)
        self.assertAlmostEqual(0.2026, pose.xyz[2], places=6)
        self.assertEqual("labutopia_asset_cards", pose.source)
        self.assertIn("PourLiquid", state.values["candidate_tasks"])
        self.assertIn("pour", {primitive for item in affordances for primitive in item.action_primitives})
        self.assertIn("workspace", {zone.zone_type for zone in safety_zones})
        self.assertIn("collision", {zone.zone_type for zone in safety_zones})

    def test_task_configs_expose_labutopia_action_schema(self):
        source = LabUtopiaTaskConfigSource.from_directory(FIXTURE_ROOT / "config")
        schema = source.query_action_schema("press_button")
        state = source.query_state("Level1_press")

        self.assertEqual("press_button", schema.action)
        self.assertEqual("/World/target_button", schema.args["targets"]["target_button_path"])
        self.assertEqual("/World/target_button/button", schema.postconditions[0]["target"])
        self.assertEqual(0.405, schema.postconditions[0]["threshold"])
        self.assertEqual("assets/chemistry_lab/lab_003/lab_003.usd", state.values["usd_path"])

    def test_task_configs_expose_target_affordances(self):
        source = LabUtopiaTaskConfigSource.from_directory(FIXTURE_ROOT / "config")
        affordances = source.query_affordance("/World/target_button")

        self.assertEqual(1, len(affordances))
        self.assertEqual("button", affordances[0].kind)
        self.assertEqual(["press_button"], affordances[0].action_primitives)
        self.assertEqual("/World/target_button", affordances[0].target)
        self.assertEqual("Level1_press", affordances[0].metadata["labutopia_task_name"])

    def test_task_configs_extract_path_like_keys_task_lists_and_inferred_stir_targets(self):
        entries = _target_entries_from_config(
            {
                "name": "CleanBeaker",
                "task_type": "cleanbeaker",
                "target_beaker": "/World/target_beaker",
                "beaker_1": "/World/beaker_1",
                "camera_target": "/World/camera",
                "task": {
                    "door_paths": [
                        {"path": "/World/cabinet/door", "open_direction": "x"},
                    ],
                },
            }
        )
        roles_by_path = {entry["path"]: entry["role"] for entry in entries}

        self.assertEqual("target_beaker", roles_by_path["/World/target_beaker"])
        self.assertEqual("beaker_1", roles_by_path["/World/beaker_1"])
        self.assertEqual("task.door_paths[0]", roles_by_path["/World/cabinet/door"])
        self.assertNotIn("/World/camera", roles_by_path)

        inferred = _target_entries_from_config({"name": "LiquidMixing", "task_type": "LiquidMixing"})
        self.assertIn("/World/target_beaker", {entry["path"] for entry in inferred})
        self.assertIn("/World/glass_rod", {entry["path"] for entry in inferred})

    def test_scene_source_exposes_navigation_config_as_synthetic_goal(self):
        with TemporaryDirectory() as root:
            root_path = Path(root)
            config_dir = root_path / "config"
            navigation_dir = config_dir / "navigation"
            navigation_dir.mkdir(parents=True)
            (navigation_dir / "navigation_assets.yaml").write_text(
                """
assets:
  - name: lab_3
    scene_asset_path: assets/navigation_lab/navigation_lab_01/lab.usd
    barrier_image_path: assets/navigation/barrier/lab_1.png
    x_bounds: [-2.0, 6.0]
    y_bounds: [-1.0, 3.0]
    offset_radius: 0.6
""".lstrip(),
                encoding="utf-8",
            )
            (config_dir / "level5_Navigation.yaml").write_text(
                """
name: Level5_Navigation
task_type: navigation
usd_path: assets/navigation_lab/navigation_lab_01/lab.usd
task:
  navigation_config_path: config/navigation/navigation_assets.yaml
""".lstrip(),
                encoding="utf-8",
            )

            source = LabUtopiaSceneSource.from_directory(config_dir, labutopia_root=root_path, usd_source_factory=FakeUsdSource)
            pose = source.query_pose("navigation://lab_3")
            state = source.query_state("navigation://lab_3")
            affordance = source.query_affordance("navigation://lab_3")[0]

        self.assertEqual([2.0, 1.0, 0.0], pose.xyz)
        self.assertEqual("navigation_goal", state.values["target_type"])
        self.assertEqual("navigation_goal", affordance.kind)
        self.assertEqual([2.0, 1.0, 0.0], affordance.pose.xyz)
        self.assertEqual("navigation__lab_3", labutopia_asset_id_from_prim_path("navigation://lab_3"))

    def test_scene_source_falls_back_to_task_position_range_pose(self):
        with TemporaryDirectory() as root:
            config_dir = Path(root) / "config"
            config_dir.mkdir()
            (config_dir / "level_place.yaml").write_text(
                """
name: LevelPlace
task_type: place
usd_path: assets/chemistry_lab/lab_001/lab_001.usd
task:
  obj_paths:
    - path: /World/beaker2
      position_range:
        x: [0.2, 0.4]
        y: [-0.1, 0.1]
        z: [0.8, 0.8]
""".lstrip(),
                encoding="utf-8",
            )

            source = LabUtopiaSceneSource.from_directory(config_dir, usd_source_factory=FakeUsdSource)
            pose = source.query_pose("/World/beaker2")
            state = source.query_state("/World/beaker2")

        self.assertEqual([0.30000000000000004, 0.0, 0.8], pose.xyz)
        self.assertEqual("labutopia_task_config_position_range", pose.metadata["source_type"])
        self.assertEqual("task_config_target", state.values["target_type"])

    def test_scene_source_binds_task_to_usd_and_enriches_affordance_pose(self):
        source = LabUtopiaSceneSource.from_directory(FIXTURE_ROOT / "config", usd_source_factory=FakeUsdSource)
        affordance = source.query_affordance("/World/target_button")[0]

        self.assertEqual("button", affordance.kind)
        self.assertEqual([1.0, 2.0, 3.0], affordance.pose.xyz)
        self.assertTrue(affordance.metadata["scene_aware_pose_enriched"])
        self.assertEqual("fake_usd", affordance.pose.source)

    def test_generated_asset_cards_round_trip_through_asset_card_source(self):
        cards = generate_asset_cards(FIXTURE_ROOT / "config", usd_source_factory=FakeUsdSource)
        target_card = next(card for card in cards if card["source"]["prim_path"] == "/World/target_button")

        self.assertEqual("World__target_button", target_card["asset_id"])
        self.assertEqual(["button"], target_card["affordances"])
        self.assertEqual([0.2, 0.2, 0.2], target_card["geometry"]["bbox_size_m_approx"])
        self.assertEqual([1.0, 2.0, 3.0], target_card["operation_hints"]["details"]["nominal_target_pose_xyz"])
        self.assertEqual("x", target_card["operation_hints"]["details"]["press_button"]["axis"])

        with TemporaryDirectory() as output_dir:
            summary = generate_asset_cards_to_directory(
                FIXTURE_ROOT / "config",
                output_dir,
                usd_source_factory=FakeUsdSource,
            )
            source = LabUtopiaAssetCardSource.from_directory(output_dir)
            pose = source.query_pose("World__target_button")

        self.assertEqual(len(cards), summary["card_count"])
        self.assertEqual([1.0, 2.0, 3.0], pose.xyz)

    def test_press_button_action_smoke_builds_plan_and_verification(self):
        scene = LabUtopiaSceneSource.from_directory(FIXTURE_ROOT / "config", usd_source_factory=FakeUsdSource)
        engine = QueryEngine(sources=[scene])
        smoke = build_action_smoke(engine, "press_button", target="/World/target_button")

        self.assertEqual("press_button", smoke["action"])
        self.assertEqual("classical_press_button_contract", smoke["plan"]["controller"])
        self.assertTrue(smoke["verification"]["planned_contract"]["ok"])
        self.assertEqual("/World/target_button/button", smoke["plan"]["verification_target"])

    def test_task_report_summarizes_ready_tasks_and_action_smoke(self):
        report = generate_task_report(FIXTURE_ROOT / "config", usd_source_factory=FakeUsdSource)
        task = next(item for item in report["tasks"] if item["name"] == "Level1_press")

        self.assertTrue(report["ok"])
        self.assertEqual(1, report["summary"]["task_count"])
        self.assertEqual("ready_for_policy_or_controller", task["status"])
        self.assertEqual(["pose_axis_gt"], task["postcondition_types"])
        self.assertTrue(task["action_smoke"]["value"]["verification"]["planned_contract"]["ok"])

    def test_query_engine_prioritizes_labutopia_action_schemas_over_builtin_defaults(self):
        source = LabUtopiaTaskConfigSource.from_directory(FIXTURE_ROOT / "config")
        engine = QueryEngine(sources=[source])
        schema = engine.query_action_schema("press_button")

        self.assertEqual("labutopia_task_configs", schema.metadata["source"])

    def test_press_button_smoke_offsets_along_configured_axis_for_y_axis(self):
        # Bug 1 regression: pre_press / pressed offsets must follow the schema
        # axis, not silently fall back to z whenever axis != "x".
        scene = LabUtopiaSceneSource.from_directory(FIXTURE_ROOT / "config", usd_source_factory=FakeUsdSource)
        engine = QueryEngine(sources=[scene])
        synthetic_schema = {
            "action": "press_button",
            "args": {"targets": {"target_button_path": "/World/target_button"}},
            "preconditions": [],
            "postconditions": [
                {"type": "pose_axis_gt", "target": "/World/target_button/button", "axis": "y", "threshold": 0.1},
            ],
        }
        from unilabos.queries.labutopia.action_smoke import _press_button_smoke

        smoke = _press_button_smoke(engine, synthetic_schema, target="/World/target_button")
        waypoints = {wp["name"]: wp["xyz"] for wp in smoke["plan"]["waypoints"]}

        self.assertEqual([1.0, 1.94, 3.0], waypoints["pre_press"])
        self.assertEqual([1.0, 2.02, 3.0], waypoints["pressed"])

    def test_asset_card_source_does_not_skip_files_ending_in_summary_json(self):
        # Bug 2 regression: from_directory must only skip the literal
        # ``summary.json`` file, not anything matching ``*summary.json``.
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "summary.json").write_text("{}", encoding="utf-8")
            (root / "World__lab_summary.json").write_text(
                json.dumps(
                    {
                        "asset_id": "World__lab_summary",
                        "source": {"prim_path": "/World/lab_summary"},
                        "geometry": {"bbox_min": [0, 0, 0], "bbox_max": [1, 1, 1], "bbox_size_m_approx": [1, 1, 1]},
                        "affordances": ["bench"],
                    }
                ),
                encoding="utf-8",
            )
            source = LabUtopiaAssetCardSource.from_directory(root)

        self.assertIsNotNone(source.query_pose("World__lab_summary"))
        self.assertIsNotNone(source.query_pose("/World/lab_summary"))

    def test_asset_card_source_mix_counts_per_geometry_source_type(self):
        cards = [
            {"asset_id": "a", "geometry": {"source_type": "labutopia_usd"}},
            {"asset_id": "b", "geometry": {"source_type": "labutopia_usd"}},
            {"asset_id": "c", "geometry": {"source_type": "labutopia_navigation_config"}},
            {"asset_id": "d", "geometry": {}},
            {"asset_id": "e"},
        ]
        mix = _source_mix_from_cards(cards)

        self.assertEqual(2, mix["labutopia_usd"])
        self.assertEqual(1, mix["labutopia_navigation_config"])
        self.assertEqual(2, mix["unknown"])

    def test_generate_asset_cards_to_directory_writes_summary_with_source_mix_once(self):
        with TemporaryDirectory() as output_dir:
            summary = generate_asset_cards_to_directory(
                FIXTURE_ROOT / "config",
                output_dir,
                usd_source_factory=FakeUsdSource,
            )
            on_disk = json.loads((Path(output_dir) / "summary.json").read_text(encoding="utf-8"))

        self.assertIn("source_mix", summary)
        self.assertEqual(summary["source_mix"], on_disk["source_mix"])
        self.assertEqual(summary["card_count"], on_disk["card_count"])

    def test_generate_asset_cards_to_directory_clean_removes_stale_cards(self):
        with TemporaryDirectory() as output_dir:
            stale = Path(output_dir) / "World__should_be_removed.json"
            stale.write_text("{}", encoding="utf-8")
            summary = generate_asset_cards_to_directory(
                FIXTURE_ROOT / "config",
                output_dir,
                usd_source_factory=FakeUsdSource,
                clean_existing=True,
            )

        self.assertFalse(stale.exists())
        self.assertGreaterEqual(summary["card_count"], 1)

    def test_task_config_source_warns_on_duplicate_task_names(self):
        # Bug 4 regression: duplicate task names must surface as a warning and
        # only the first occurrence is kept so downstream lookups stay stable.
        with TemporaryDirectory() as raw:
            config_dir = Path(raw) / "config"
            config_dir.mkdir()
            (config_dir / "a.yaml").write_text(
                """
name: SamePress
task_type: press
target_button_path: /World/btn_a
sub_obj_path: /World/btn_a/button
""".lstrip(),
                encoding="utf-8",
            )
            (config_dir / "b.yaml").write_text(
                """
name: SamePress
task_type: press
target_button_path: /World/btn_b
sub_obj_path: /World/btn_b/button
""".lstrip(),
                encoding="utf-8",
            )

            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always", RuntimeWarning)
                source = LabUtopiaTaskConfigSource.from_directory(config_dir)

        self.assertEqual(["SamePress"], source.duplicate_task_names)
        self.assertEqual(1, len(source.configs))
        self.assertEqual("/World/btn_a", source.configs[0]["target_button_path"])
        self.assertTrue(any("SamePress" in str(item.message) for item in captured))

    def test_task_scoped_action_schema_returns_per_task_metadata(self):
        # Bug 5 regression: when multiple tasks share the same action name we
        # must still be able to get the per-task schema and not the first one.
        source = LabUtopiaTaskConfigSource.from_directory(FIXTURE_ROOT / "config_multi")
        first_press = source.query_task_schema("Level1_press_x")
        second_press = source.query_task_schema("Level2_press_extra")

        self.assertIsNotNone(first_press)
        self.assertIsNotNone(second_press)
        self.assertEqual("Level1_press_x", first_press.metadata["labutopia_task_name"])
        self.assertEqual("Level2_press_extra", second_press.metadata["labutopia_task_name"])
        self.assertEqual("/World/target_button", first_press.args["targets"]["target_button_path"])
        self.assertEqual("/World/lab2_button", second_press.args["targets"]["target_button_path"])

    def test_task_report_uses_task_scoped_schema_when_action_collides(self):
        # Bug 5 wired through task_report: each task entry should expose its
        # own schema metadata even when several tasks share an action.
        report = generate_task_report(
            FIXTURE_ROOT / "config_multi",
            usd_source_factory=FakeUsdSource,
            include_action_smoke=False,
        )
        per_task_schema = {
            task["name"]: task["schema"]["value"]["metadata"]["labutopia_task_name"]
            for task in report["tasks"]
            if task["schema"]["ok"]
        }

        self.assertEqual("Level1_press_x", per_task_schema["Level1_press_x"])
        self.assertEqual("Level2_press_extra", per_task_schema["Level2_press_extra"])
        self.assertIn("pose_source_mix", report["summary"])

    def test_task_report_summary_reports_pose_source_mix(self):
        report = generate_task_report(FIXTURE_ROOT / "config", usd_source_factory=FakeUsdSource)
        mix = report["summary"]["pose_source_mix"]

        self.assertGreaterEqual(sum(mix.values()), 1)
        self.assertIn("fake_usd", mix)


if __name__ == "__main__":
    unittest.main()
