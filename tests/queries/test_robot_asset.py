from pathlib import Path
import tempfile
import unittest

from unilabos.queries import load_robot_asset_manifest, logical_joints_from_mapping, resolve_asset_path


class RobotAssetTest(unittest.TestCase):
    def test_load_manifest_and_resolve_relative_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            asset_dir = Path(tmpdir) / "robot"
            asset_dir.mkdir()
            manifest_path = asset_dir / "asset_manifest.json"
            manifest_path.write_text('{"robot_id":"arm","urdf":"urdf/arm.urdf"}', encoding="utf-8")

            manifest, root = load_robot_asset_manifest(asset_dir)

            self.assertEqual("arm", manifest["robot_id"])
            self.assertEqual(asset_dir, root)
            self.assertEqual(asset_dir / "urdf" / "arm.urdf", resolve_asset_path(root, manifest["urdf"]))

    def test_logical_joint_mapping_supports_single_and_pair_terms(self):
        manifest = {
            "servo": {
                "ticks_per_rev": 4096,
                "logical_joint_mapping": {
                    "J1": {"formula": "single", "servo": "base", "center": 2048},
                    "J2": {
                        "formula": "linear_combination",
                        "divisor": 2,
                        "terms": [
                            {"servo": "s1", "center": 2048, "sign": 1},
                            {"servo": "s2", "center": 2048, "sign": -1},
                        ],
                    },
                },
            }
        }

        joints = logical_joints_from_mapping({"base": 3072, "s1": 2560, "s2": 1536}, manifest)

        self.assertAlmostEqual(1.57079632679, joints["J1"], places=6)
        self.assertAlmostEqual(0.78539816339, joints["J2"], places=6)


if __name__ == "__main__":
    unittest.main()
