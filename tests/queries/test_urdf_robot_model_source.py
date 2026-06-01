from pathlib import Path
import tempfile
import unittest

from unilabos.queries import QueryEngine, URDFRobotModelSource


MINIMAL_URDF = """<?xml version="1.0"?>
<robot name="two_link">
  <link name="base_link"/>
  <link name="link_1"/>
  <link name="tool_link"/>
  <joint name="joint_1" type="revolute">
    <parent link="base_link"/>
    <child link="link_1"/>
    <origin xyz="1 0 0" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
  </joint>
  <joint name="joint_2" type="fixed">
    <parent link="link_1"/>
    <child link="tool_link"/>
    <origin xyz="1 0 0" rpy="0 0 0"/>
  </joint>
</robot>
"""


class URDFRobotModelSourceTest(unittest.TestCase):
    def test_query_pose_uses_urdf_fk_and_synthetic_tool0(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "robot.urdf"
            path.write_text(MINIMAL_URDF, encoding="utf-8")
            source = URDFRobotModelSource.from_file(
                path,
                robot_id="arm",
                joint_positions={"joint_1": 1.5707963267948966},
                tool_link="tool_link",
                tool_offset_xyz=[0.0, 0.0, 0.1],
            )
            engine = QueryEngine([source])

            pose = engine.query_pose("arm.tool0")
            state = engine.query_state("arm")
            affordance = engine.query_affordance("arm", kind="end_effector")[0]
            zones = engine.query_safety_zones()

            self.assertAlmostEqual(1.0, pose.xyz[0], places=6)
            self.assertAlmostEqual(1.0, pose.xyz[1], places=6)
            self.assertAlmostEqual(0.1, pose.xyz[2], places=6)
            self.assertEqual("tool0", pose.metadata["link"])
            self.assertEqual(["base_link", "link_1", "tool_link"], state.values["links"])
            self.assertEqual("arm.tool0", affordance.target)
            self.assertEqual("arm.rough_workspace", zones[0].id)


if __name__ == "__main__":
    unittest.main()
