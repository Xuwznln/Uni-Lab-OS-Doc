from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from unilabos.queries import QueryEngine
from unilabos.queries.action_catalog_source import ActionCatalogSource
from unilabos.queries.resource_map_source import ResourceMapSource
from unilabos.robo_unilabos.asset_pack import build_asset_pack
from unilabos.robo_unilabos.resource_map import ResourceMap
from unilabos_client import RoboUniLabOS


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "labutopia"


MINIMAL_URDF = """<?xml version="1.0"?>
<robot name="asset_arm">
  <link name="base_link"/>
  <link name="tool_link"/>
  <joint name="tool_joint" type="fixed">
    <parent link="base_link"/>
    <child link="tool_link"/>
    <origin xyz="0.2 0.0 0.3" rpy="0 0 0"/>
  </joint>
</robot>
"""


def _write_task_report(path: Path) -> None:
    schema = {
        "schema_version": "0.1",
        "action": "press_button",
        "args": {
            "task_type": "press",
            "controller_type": "press",
            "targets": {
                "target_button_path": "/World/target_button",
                "sub_obj_path": "/World/target_button/button",
            },
        },
        "preconditions": [
            {"type": "scene_loaded", "usd_path": "assets/chemistry_lab/lab_003/lab_003.usd"},
        ],
        "postconditions": [
            {"type": "pose_axis_gt", "target": "/World/target_button/button", "axis": "x", "threshold": 0.405},
        ],
        "policy_preference": ["classical", "DiffusionPolicy"],
        "timeout_s": 30.0,
        "metadata": {
            "source": "labutopia_task_configs",
            "labutopia_task_name": "Level1_press",
        },
    }
    payload = {
        "ok": True,
        "summary": {"task_count": 1, "by_status": {"ready_for_policy_or_controller": 1}},
        "tasks": [
            {
                "name": "Level1_press",
                "action": "press_button",
                "status": "ready_for_policy_or_controller",
                "source_file": "level1_press.yaml",
                "usd_path": "assets/chemistry_lab/lab_003/lab_003.usd",
                "schema": {"ok": True, "value": schema},
                "postcondition_types": ["pose_axis_gt"],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_robot_asset(root: Path) -> Path:
    asset_dir = root / "robot_asset"
    (asset_dir / "urdf").mkdir(parents=True)
    (asset_dir / "urdf" / "arm.urdf").write_text(MINIMAL_URDF, encoding="utf-8")
    (asset_dir / "asset_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "robo-unilabos.robot_asset.v1",
                "robot_id": "asset_arm",
                "display_name": "Asset Arm",
                "urdf": "urdf/arm.urdf",
                "base_frame": "base_link",
                "tool_link": "tool_link",
                "tool_offset_xyz": [0.0, 0.0, 0.1],
                "workspace": {"frame_id": "base_link", "center": [0, 0, 0.2], "size": [1, 1, 0.6]},
                "validation": {"maturity": "test_asset"},
            }
        ),
        encoding="utf-8",
    )
    return asset_dir


def _write_real_asset(root: Path) -> Path:
    package = root / "real_asset" / "asset_package"
    (package / "asset_card").mkdir(parents=True)
    (package / "driver_schema").mkdir()
    card_path = package / "asset_card" / "asset_card.json"
    card_path.write_text(
        json.dumps(
            {
                "asset_id": "instrument_001",
                "asset_name": "Instrument",
                "geometry": {
                    "camera_frame_bbox_m_all_visible_depth": {
                        "min_xyz": [0.0, 0.0, 0.0],
                        "max_xyz": [1.0, 1.0, 1.0],
                    }
                },
                "semantic_layer": {},
                "visual_layer": {},
                "quality_notes": ["visual MVP"],
            }
        ),
        encoding="utf-8",
    )
    (package / "driver_schema" / "driver_schema.json").write_text(
        json.dumps(
            {
                "asset_type": "benchtop_instrument",
                "evidence_level": "visual_only",
                "actions": [
                    {"name": "read_display", "kind": "perception", "target_mask": "display.png"},
                    {"name": "rotate_or_press_control", "kind": "manual_or_robotic_manipulation"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return card_path


def _write_startup_config(root: Path) -> Path:
    path = root / "startup_config.json"
    path.write_text(
        json.dumps(
            {
                "code": 0,
                "data": {
                    "nodes": [
                        {
                            "id": "liquid_handler",
                            "uuid": "lh-uuid",
                            "parent_uuid": "",
                            "name": "liquid_handler",
                            "type": "device",
                            "class": "liquid_handler",
                            "position": {"x": 100, "y": 200, "z": 0},
                            "pose": {
                                "position": {"x": 100, "y": 200, "z": 0},
                                "position_3d": {"x": 0, "y": 0, "z": 0},
                                "rotation": {"x": 0, "y": 0, "z": 0},
                            },
                            "config": {"backend": {"type": "UniLiquidHandlerRvizBackend"}, "simulator": True},
                        },
                        {
                            "id": "deck",
                            "uuid": "deck-uuid",
                            "parent_uuid": "lh-uuid",
                            "name": "deck",
                            "type": "deck",
                            "class": "OTDeck",
                            "position": {"x": 0, "y": 0, "z": 0},
                            "pose": {"position": {"x": 0, "y": 0, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}},
                            "config": {"type": "OTDeck", "size_x": 624.3, "size_y": 565.2, "size_z": 900},
                        },
                        {
                            "id": "tip_rack",
                            "uuid": "tip-rack-uuid",
                            "parent_uuid": "deck-uuid",
                            "name": "tip_rack",
                            "type": "tip_rack",
                            "class": "opentrons_96_filtertiprack_1000ul",
                            "position": {"x": 265, "y": 0, "z": 69},
                            "pose": {"position": {"x": 265, "y": 0, "z": 69}, "rotation": {"x": 0, "y": 0, "z": 0}},
                            "config": {"type": "TipRack", "category": "tip_rack"},
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_horizon_root(root: Path) -> Path:
    horizon = root / "horizon_v2_import"
    for path in [
        horizon / "isaac" / "horizon_v2_with_arm7_scene.usd",
        horizon / "isaac" / "HOR_Horizon_V2_1_2508_basepython.usd",
        horizon / "isaac" / "horizon_arm7.usd",
        horizon / "isaac" / "configuration" / "horizon_arm7_robot.usd",
        horizon / "source" / "7" / "urdf" / "7.urdf",
        horizon / "isaac" / "horizon_v2_with_arm7_scene.png",
        horizon / "real_arm_link" / "isaac_shadow_sync.py",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
    return horizon


class AssetPackTest(unittest.TestCase):
    def test_build_asset_pack_exposes_resource_map_action_catalog_robot_and_real_asset(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            report_path = root / "task_report.json"
            output_dir = root / "pack"
            _write_task_report(report_path)
            robot_asset = _write_robot_asset(root)
            real_asset = _write_real_asset(root)

            result = build_asset_pack(
                asset_card_dir=FIXTURE_ROOT / "asset_cards",
                task_report_path=report_path,
                output_dir=output_dir,
                robot_assets=[robot_asset],
                real_asset_cards=[real_asset],
                sim_robot_id="labutopia_franka",
            )

            self.assertTrue(result["ok"])
            resource_map = ResourceMap.from_file(output_dir / "resource_map.json")
            self.assertIsNotNone(resource_map.get_resource("World__beaker1"))
            self.assertTrue(resource_map.reachable("World__beaker1.container", "labutopia_franka")["reachable"])
            self.assertEqual("asset_arm", resource_map.get_resource("asset_arm").id)
            self.assertEqual("instrument_001", resource_map.get_resource("instrument_001").id)

            engine = QueryEngine(
                [
                    ResourceMapSource.from_file(output_dir / "resource_map.json"),
                    ActionCatalogSource.from_file(output_dir / "action_catalog.json"),
                ]
            )
            pose = engine.query_pose("World__beaker1")
            by_task = engine.query_action_schema("Level1_press")
            by_action = engine.query_action_schema("press_button")

            self.assertAlmostEqual(12.023525, pose.xyz[0], places=6)
            self.assertEqual("Level1_press", by_task.metadata["labutopia_task_name"])
            self.assertEqual("press_button", by_action.action)

    def test_client_loads_action_catalog_and_robot_asset(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            report_path = root / "task_report.json"
            output_dir = root / "pack"
            _write_task_report(report_path)
            robot_asset = _write_robot_asset(root)
            build_asset_pack(
                asset_card_dir=FIXTURE_ROOT / "asset_cards",
                task_report_path=report_path,
                output_dir=output_dir,
                robot_assets=[robot_asset],
            )

            client = RoboUniLabOS.from_sources(
                graph=str(output_dir / "resource_map.json"),
                action_catalog=str(output_dir / "action_catalog.json"),
                robot_assets=[str(robot_asset)],
            )

            self.assertEqual("press_button", client.query_action_schema("Level1_press")["action"])
            self.assertEqual("urdf_robot_model", client.query_pose("asset_arm.tool0")["source"])

    def test_build_asset_pack_imports_unilabos_startup_config_resource_tree(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            report_path = root / "task_report.json"
            output_dir = root / "pack"
            _write_task_report(report_path)
            startup_config = _write_startup_config(root)

            build_asset_pack(
                asset_card_dir=FIXTURE_ROOT / "asset_cards",
                task_report_path=report_path,
                output_dir=output_dir,
                startup_configs=[startup_config],
            )

            graph = json.loads((output_dir / "resource_map.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [{"source": "liquid_handler", "target": "deck"}, {"source": "deck", "target": "tip_rack"}],
                [{"source": link["source"], "target": link["target"]} for link in graph["links"]],
            )
            resource_map = ResourceMap.from_file(output_dir / "resource_map.json")
            liquid_handler = resource_map.get_resource("liquid_handler")
            deck = resource_map.get_resource("deck")
            tip_rack = resource_map.get_resource("tip_rack")

            self.assertEqual("liquid_handler", liquid_handler.resource_type)
            self.assertEqual("deck_workspace", deck.resource_type)
            self.assertEqual("liquid_handler_frame", deck.pose.frame_id)
            self.assertEqual("mm", deck.pose.unit)
            self.assertEqual("tip_rack", tip_rack.resource_type)
            self.assertTrue(resource_map.reachable("tip_rack.tips", "liquid_handler")["reachable"])
            self.assertEqual({"type": "UniLiquidHandlerRvizBackend"}, liquid_handler.device_endpoints["backend"])

    def test_build_asset_pack_imports_horizon_arm7_digital_twin_assets(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            report_path = root / "task_report.json"
            output_dir = root / "pack"
            _write_task_report(report_path)
            horizon_root = _write_horizon_root(root)

            build_asset_pack(
                asset_card_dir=FIXTURE_ROOT / "asset_cards",
                task_report_path=report_path,
                output_dir=output_dir,
                horizon_roots=[horizon_root],
            )

            graph = json.loads((output_dir / "resource_map.json").read_text(encoding="utf-8"))
            self.assertIn({"source": "horizon_v2", "target": "horizon_arm7", "kind": "hosts_robot", "source_file": str(horizon_root)}, graph["links"])
            resource_map = ResourceMap.from_file(output_dir / "resource_map.json")
            platform = resource_map.get_resource("horizon_v2")
            arm = resource_map.get_resource("horizon_arm7")

            self.assertEqual("automation_platform", platform.resource_type)
            self.assertEqual("robot_arm", arm.resource_type)
            self.assertIn("tool0", [affordance.id for affordance in arm.affordances])
            self.assertTrue(arm.robot_bindings["urdf"].endswith("source/7/urdf/7.urdf"))
            self.assertTrue(arm.robot_bindings["scene_usd"].endswith("isaac/horizon_v2_with_arm7_scene.usd"))
            safety_zones = QueryEngine([ResourceMapSource.from_file(output_dir / "resource_map.json")]).query_safety_zones()
            self.assertIn("horizon_arm7.nominal_workspace", [zone.id for zone in safety_zones])


if __name__ == "__main__":
    unittest.main()
