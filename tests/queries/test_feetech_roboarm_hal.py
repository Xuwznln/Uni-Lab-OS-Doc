import math
import unittest

from unilabos.hal.adapters import FeetechRoboArmHAL
from unilabos.queries import QueryEngine


def _leader_payload():
    return {
        "ok": True,
        "role": "leader",
        "robot_type": "feetech_isomorphic",
        "timestamp": 1779720000.0,
        "monotonic": 12.5,
        "sample_count": 42,
        "position": {
            "base": 2048,
            "s1": 3072,
            "s2": 1024,
            "e1": 2048,
            "e2": 2048,
            "wrist_p": 2048,
            "wrist_r": 2048,
            "gripper": 2000,
        },
        "speed": {"s1": 3},
        "current": {"s1": 12},
        "torque_enabled": False,
    }


class FeetechRoboArmHALTest(unittest.TestCase):
    def test_query_engine_reads_joint_state_from_feetech_endpoint(self):
        hal = FeetechRoboArmHAL(
            endpoint_url="http://example.invalid/api/leader/state",
            payload_provider=_leader_payload,
        )
        engine = QueryEngine()
        engine.hal_registry.register("roboarm_leader", hal)

        state = engine.query_state("roboarm_leader")
        joint_state = state.values["joint_state"]

        self.assertEqual(["base", "s1", "s2", "e1", "e2", "wrist_p", "wrist_r", "gripper"], joint_state["names"])
        self.assertAlmostEqual(0.0, joint_state["positions"][0])
        self.assertAlmostEqual(math.pi / 2.0, joint_state["positions"][1])
        self.assertAlmostEqual(-math.pi / 2.0, joint_state["positions"][2])
        self.assertEqual(42, state.values["sample_count"])
        self.assertFalse(state.values["torque_enabled"])
        self.assertFalse(state.values["motion_enabled"])
        self.assertEqual(3072, state.values["raw_position_ticks"]["s1"])

    def test_motion_methods_are_disabled_in_validation_adapter(self):
        hal = FeetechRoboArmHAL(
            endpoint_url="http://example.invalid/api/leader/state",
            payload_provider=_leader_payload,
        )

        with self.assertRaisesRegex(RuntimeError, "read-only"):
            hal.move_j([0.0] * 8)
        with self.assertRaisesRegex(RuntimeError, "read-only"):
            hal.open_gripper()


if __name__ == "__main__":
    unittest.main()
