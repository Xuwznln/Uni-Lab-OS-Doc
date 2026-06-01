from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import unittest

from unilabos.robo_unilabos.cli import main


EXAMPLE_GRAPH = (
    Path(__file__).parents[2]
    / "unilabos"
    / "robo_unilabos"
    / "examples"
    / "balance_weighing_resource_map.json"
)
LABUTOPIA_FIXTURES = Path(__file__).parents[1] / "fixtures" / "labutopia"


class RoboUniLabOSCliTest(unittest.TestCase):
    def run_cli_raw(self, *args):
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(list(args))
        self.assertEqual(0, exit_code)
        return json.loads(stdout.getvalue())

    def run_cli(self, *args):
        return self.run_cli_raw("--graph", str(EXAMPLE_GRAPH), *args)

    def test_lab_list_resources_outputs_command_result(self):
        payload = self.run_cli("lab", "list", "resources")
        self.assertTrue(payload["ok"])
        self.assertEqual("lab.list.resources", payload["command"])
        self.assertEqual(3, payload["observations"]["count"])

    def test_lab_where_affordance_outputs_pose(self):
        payload = self.run_cli("lab", "where", "balance_1.pan")
        self.assertTrue(payload["ok"])
        self.assertEqual("balance_1.pan", payload["observations"]["target"])
        self.assertEqual("balance_1/pan", payload["observations"]["pose"]["frame_id"])

    def test_lab_reachable_outputs_machine_readable_contract(self):
        payload = self.run_cli("lab", "reachable", "balance_1.pan", "--robot", "roboarm_1")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["observations"]["reachable"])
        self.assertEqual(["roboarm_1"], payload["observations"]["reachable_by"])

    def test_query_action_schema_uses_labutopia_task_config(self):
        payload = self.run_cli_raw(
            "--labutopia-config",
            str(LABUTOPIA_FIXTURES / "config"),
            "query",
            "action-schema",
            "press_button",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual("query.action-schema", payload["command"])
        self.assertEqual("/World/target_button/button", payload["observations"]["postconditions"][0]["target"])
        self.assertEqual("labutopia_task_configs", payload["observations"]["metadata"]["source"])


if __name__ == "__main__":
    unittest.main()
