from pathlib import Path
import unittest

from unilabos.queries import QueryEngine
from unilabos.queries.models import Pose, QueryAffordance
from unilabos.queries.resource_map_source import ResourceMapSource
from unilabos.queries.sources import EmptyQuerySource


EXAMPLE_GRAPH = (
    Path(__file__).parents[2]
    / "unilabos"
    / "robo_unilabos"
    / "examples"
    / "balance_weighing_resource_map.json"
)


class ResourceMapQueryEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = QueryEngine([ResourceMapSource.from_file(EXAMPLE_GRAPH)])

    def test_query_pose_resolves_resource_affordance_target(self):
        pose = self.engine.query_pose("balance_1.pan")

        self.assertEqual([0.0, 0.0, 82.0], pose.xyz)
        self.assertEqual("balance_1/pan", pose.frame_id)
        self.assertEqual("resource_map", pose.source)

    def test_query_state_returns_robot_operable_resource_contract(self):
        state = self.engine.query_state("balance_1")

        self.assertFalse(state.values["state_variables"]["stable"])
        self.assertEqual("device.balance_1.read.mass_g", state.values["device_endpoints"]["read_mass"])

    def test_query_affordance_and_safety_zones(self):
        affordances = self.engine.query_affordance("balance_1")
        safety_zones = self.engine.query_safety_zones()

        self.assertEqual(["place_surface"], [item.kind for item in affordances])
        self.assertEqual(["place", "pick"], affordances[0].action_primitives)
        self.assertEqual("balance_1.access_front", safety_zones[0].id)
        self.assertEqual("workspace", safety_zones[0].zone_type)

    def test_query_affordance_enriches_pose_from_other_sources(self):
        class AffordanceOnlySource(EmptyQuerySource):
            name = "affordance_only"

            def query_affordance(self, target, kind=None):
                return [
                    QueryAffordance(
                        id="button",
                        kind="button",
                        target="/World/button",
                        action_primitives=["press_button"],
                    )
                ]

        class PoseOnlySource(EmptyQuerySource):
            name = "pose_only"

            def query_pose(self, target, frame=None):
                if target == "/World/button":
                    return Pose(xyz=[1.0, 2.0, 3.0], frame_id=frame or "world", source=self.name)
                return None

        engine = QueryEngine([AffordanceOnlySource(), PoseOnlySource()])
        affordance = engine.query_affordance("/World/button")[0]

        self.assertEqual([1.0, 2.0, 3.0], affordance.pose.xyz)
        self.assertTrue(affordance.metadata["pose_enriched"])


if __name__ == "__main__":
    unittest.main()
