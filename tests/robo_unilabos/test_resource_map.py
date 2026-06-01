from pathlib import Path
import unittest

from unilabos.robo_unilabos.resource_map import ResourceMap


EXAMPLE_GRAPH = (
    Path(__file__).parents[2]
    / "unilabos"
    / "robo_unilabos"
    / "examples"
    / "balance_weighing_resource_map.json"
)


class ResourceMapTest(unittest.TestCase):
    def test_loads_robot_operable_resources(self):
        resource_map = ResourceMap.from_file(EXAMPLE_GRAPH)
        resources = resource_map.list_resources()
        self.assertEqual(
            ["balance_1", "rack_1", "vial_A"],
            [resource.id for resource in resources],
        )

    def test_resolves_affordance_target(self):
        resource_map = ResourceMap.from_file(EXAMPLE_GRAPH)
        target = resource_map.resolve_target("balance_1.pan")
        self.assertEqual("balance_1.pan", target.target_id)
        self.assertEqual("place_surface", target.affordance.kind)

    def test_reachability_uses_explicit_contract(self):
        resource_map = ResourceMap.from_file(EXAMPLE_GRAPH)
        reachable = resource_map.reachable("balance_1.pan", robot_id="roboarm_1")
        self.assertTrue(reachable["reachable"])
        self.assertEqual("explicit_reachable_by_match", reachable["reason"])

    def test_unknown_robot_is_not_reachable(self):
        resource_map = ResourceMap.from_file(EXAMPLE_GRAPH)
        reachable = resource_map.reachable("balance_1.pan", robot_id="robot_missing")
        self.assertFalse(reachable["reachable"])
        self.assertEqual("unknown_robot", reachable["reason"])


if __name__ == "__main__":
    unittest.main()

