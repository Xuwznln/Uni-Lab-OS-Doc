from pathlib import Path
import unittest

from unilabos.hal.mock import MockHAL
from unilabos.queries import ActionSchemaRegistry, QueryEngine
from unilabos.queries.action_schema import validate_action_schema_mapping
from unilabos.queries.models import Pose


class ActionSchemaAndHALTest(unittest.TestCase):
    def test_builtin_action_schemas_are_available(self):
        registry = ActionSchemaRegistry.with_builtin_schemas()
        self.assertGreaterEqual(
            set(registry.names()),
            {"move_to", "open_lid", "pour", "press_button", "weigh"},
        )
        self.assertEqual("press_button", registry.get("press_button").action)
        self.assertEqual(
            {name: [] for name in registry.names()},
            registry.validate_all(),
        )

    def test_action_schema_validation_rejects_malformed_schema(self):
        errors = validate_action_schema_mapping({"action": "", "timeout_s": 0})

        self.assertIn("missing field: schema_version", errors)
        self.assertIn("action must be a non-empty string", errors)
        self.assertIn("timeout_s must be positive", errors)

    def test_mock_hal_answers_pose_and_joint_state_queries(self):
        engine = QueryEngine()
        mock = MockHAL(robot_id="arm_1", pose=Pose(xyz=[0.1, 0.2, 0.3]))
        engine.hal_registry.register("arm_1", mock)

        pose = engine.query_pose("arm_1.tool0")
        state = engine.query_state("arm_1")

        self.assertEqual([0.1, 0.2, 0.3], pose.xyz)
        self.assertEqual("tool0", pose.frame_id)
        self.assertEqual("mock_hal", pose.source)
        self.assertEqual(
            ["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6"],
            state.values["joint_state"]["names"],
        )
        self.assertEqual(["get_pose", "get_joint_state"], [call[0] for call in mock.calls])


if __name__ == "__main__":
    unittest.main()
