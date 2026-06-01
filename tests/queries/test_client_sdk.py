from pathlib import Path
import unittest

from unilabos_client import RoboUniLabOS


EXAMPLE_GRAPH = (
    Path(__file__).parents[2]
    / "unilabos"
    / "robo_unilabos"
    / "examples"
    / "balance_weighing_resource_map.json"
)
LABUTOPIA_FIXTURES = Path(__file__).parents[1] / "fixtures" / "labutopia"


class QueryClientSDKTest(unittest.TestCase):
    def test_local_client_queries_resource_map(self):
        client = RoboUniLabOS.from_sources(graph=str(EXAMPLE_GRAPH), mock_hals=["arm_1"])

        pose = client.query_pose("balance_1.pan")
        arm_pose = client.query_pose("arm_1.tool0")
        schema = client.query_action_schema("weigh")

        self.assertEqual("balance_1/pan", pose["frame_id"])
        self.assertEqual("mock_hal", arm_pose["source"])
        self.assertEqual("weigh", schema["action"])

    def test_local_client_queries_labutopia_task_schema_and_verification(self):
        client = RoboUniLabOS.from_sources(labutopia_config=str(LABUTOPIA_FIXTURES / "config"))

        schema = client.query_action_schema("press_button")
        result = client.query_verification(
            "press_demo",
            action="press_button",
            context={"poses": {"/World/target_button/button": {"xyz": [0.5, 0.0, 0.0]}}},
        )

        self.assertEqual("/World/target_button/button", schema["postconditions"][0]["target"])
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
