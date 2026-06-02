import unittest

from unilabos.queries import QueryEngine, VerificationEngine


class VerificationTest(unittest.TestCase):
    def test_mass_range_passes_and_fails_with_evidence(self):
        engine = VerificationEngine()
        ok_result = engine.verify(
            "weigh_ok",
            context={"states": {"balance_1": {"mass_g": 5.0}}},
            postconditions=[{"type": "mass_in_range", "device": "balance_1", "min": 4.95, "max": 5.05}],
        )
        fail_result = engine.verify(
            "weigh_fail",
            context={"states": {"balance_1": {"mass_g": 4.8}}},
            postconditions=[{"type": "mass_in_range", "device": "balance_1", "min": 4.95, "max": 5.05}],
        )

        self.assertTrue(ok_result.ok)
        self.assertFalse(fail_result.ok)
        self.assertEqual(4.8, fail_result.failures[0]["evidence"]["actual_mass_g"])

    def test_query_verification_uses_action_schema_postconditions(self):
        engine = QueryEngine()
        result = engine.query_verification(
            "press_demo",
            action="press_button",
            context={"poses": {"button": {"xyz": [0.5, 0.0, 0.0]}}},
        )

        self.assertTrue(result.ok)
        self.assertEqual(0.5, result.evidence["0:pose_axis_gt"]["actual"])


if __name__ == "__main__":
    unittest.main()
