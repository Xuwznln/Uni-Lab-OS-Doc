import asyncio
import traceback
import unittest

from unilabos.registry.decorators import action, get_action_error_policy, get_action_meta
from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode
from unilabos.utils.action_decision import (
    PendingDecisionRegistry,
    SkippedActionResult,
    run_action_with_decisions,
)
from unilabos.utils.exception import (
    DeviceException,
    EmergencyStopError,
    UserAction,
    apply_builtin_error_policy_to_alarm,
)


class DeviceExceptionTest(unittest.TestCase):
    def test_action_exposes_builtin_recovery_limits_in_registry_metadata(self):
        @action(
            error_policy={
                "max_retries": 2,
                "decision_timeout_seconds": 30,
                "default_on_decision_timeout": "skip",
            }
        )
        def recoverable_action():
            return None

        expected_policy = {
            "allow_retry": True,
            "allow_skip": True,
            "max_retries": 2,
            "decision_timeout_seconds": 30.0,
            "default_on_decision_timeout": "skip",
        }
        self.assertEqual(get_action_error_policy(recoverable_action), expected_policy)
        self.assertEqual(get_action_meta(recoverable_action)["error_policy"], expected_policy)

    def test_action_rejects_invalid_builtin_recovery_limits(self):
        invalid_policies = [
            {"max_retries": -1},
            {"decision_timeout_seconds": 0},
            {"default_on_decision_timeout": "manual_fix"},
        ]

        for policy in invalid_policies:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                @action(error_policy=policy)
                def invalid_action():
                    return None

    def test_alarm_contains_correlation_and_builtin_actions(self):
        alarm = DeviceException("motor jammed").to_alarm_dict(
            device_id="robot-1",
            device_uuid="device-uuid",
            action_name="move",
            task_id="task-1",
            job_id="job-1",
            traceback_text="traceback",
        )

        self.assertEqual(alarm["task_id"], "task-1")
        self.assertEqual(alarm["job_id"], "job-1")
        self.assertEqual(alarm["device_id"], "robot-1")
        self.assertEqual(
            [item["action"] for item in alarm["suggested_actions"]],
            ["retry", "skip", "abort"],
        )

    def test_critical_exception_can_restrict_actions(self):
        alarm = EmergencyStopError("emergency stop").to_alarm_dict(
            device_id="robot-1",
            device_uuid="device-uuid",
            action_name="move",
            task_id="task-1",
            job_id="job-1",
            traceback_text="traceback",
        )

        self.assertEqual(alarm["severity"], "critical")
        self.assertEqual(
            [item["action"] for item in alarm["suggested_actions"]],
            ["retry", "abort"],
        )

    def test_custom_action_is_rejected_in_phase_one(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            UserAction("manual_fix", "人工修复")

    def test_action_error_policy_overrides_plain_exception_alarm(self):
        @action(error_policy={"allow_retry": True, "allow_skip": False})
        def fail_with_name_error():
            raise NameError("missing_parameter")

        try:
            fail_with_name_error()
        except NameError as exc:
            traceback_text = "".join(
                traceback.format_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                )
            )
            alarm = {
                "exception_type": type(exc).__name__,
                "error_message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback_text,
                "suggested_actions": [
                    {"action": "retry", "label": "重试", "description": "重新执行"},
                    {"action": "skip", "label": "跳过", "description": "跳过操作"},
                    {"action": "abort", "label": "终止任务", "description": "停止任务"},
                ],
            }
        else:
            self.fail("fail_with_name_error should raise NameError")

        result = apply_builtin_error_policy_to_alarm(
            alarm,
            get_action_error_policy(fail_with_name_error),
        )

        self.assertEqual(result["exception_type"], "NameError")
        self.assertEqual(result["error_message"], "NameError: missing_parameter")
        self.assertEqual(result["traceback"], traceback_text)
        self.assertIn("fail_with_name_error", result["traceback"])
        self.assertEqual([item["action"] for item in result["suggested_actions"]], ["retry", "abort"])


class ActionErrorPolicyAlarmTest(unittest.IsolatedAsyncioTestCase):
    async def test_plain_exception_policy_is_applied_at_alarm_boundary(self):
        node = BaseROS2DeviceNode.__new__(BaseROS2DeviceNode)
        node.device_id = "robot-1"
        node.uuid = "device-uuid"
        published_alarms = []

        class Logger:
            def error(self, _message):
                return None

        node.lab_logger = lambda: Logger()

        async def publish_and_abort(*, alarm_data, task_id, job_id):
            published_alarms.append(alarm_data)
            self.assertEqual((task_id, job_id), ("task-1", "job-1"))
            return {"action": "abort"}

        node._publish_and_wait_for_decision = publish_and_abort

        async def fail_with_name_error(**_kwargs):
            raise NameError("missing_parameter")

        with self.assertRaisesRegex(NameError, "missing_parameter"):
            await node._run_action_with_decision_loop(
                action_func=fail_with_name_error,
                action_name="fail_with_name_error",
                task_id="task-1",
                job_id="job-1",
                action_kwargs={"preserved": 42},
                error_policy={"allow_retry": True, "allow_skip": False},
            )

        self.assertEqual(len(published_alarms), 1)
        alarm = published_alarms[0]
        self.assertEqual(alarm["exception_type"], "NameError")
        self.assertEqual(alarm["error_message"], "NameError: missing_parameter")
        self.assertIn("fail_with_name_error", alarm["traceback"])
        self.assertEqual(
            [item["action"] for item in alarm["suggested_actions"]],
            ["retry", "abort"],
        )


class PendingDecisionRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_uses_configured_default_action_and_cleans_waiter(self):
        registry = PendingDecisionRegistry()

        decision = await registry.publish_and_wait(
            task_id="task-1",
            job_id="job-1",
            device_id="robot-1",
            publish=lambda: None,
            timeout=0.01,
            default_action="skip",
        )

        self.assertEqual(
            decision,
            {"action": "skip", "reason": "user_decision_timeout"},
        )
        self.assertFalse(registry.has_pending("task-1", "job-1"))

    async def test_default_waits_until_matching_decision_without_timeout(self):
        registry = PendingDecisionRegistry()
        published = asyncio.Event()
        waiting = asyncio.create_task(
            registry.publish_and_wait(
                task_id="task-1",
                job_id="job-1",
                device_id="robot-1",
                publish=published.set,
            )
        )
        await published.wait()

        _, pending = await asyncio.wait({waiting}, timeout=0.02)
        self.assertEqual(pending, {waiting})
        self.assertTrue(
            registry.resolve(
                task_id="task-1",
                job_id="job-1",
                device_id="robot-1",
                decision={"action": "retry"},
            )
        )
        self.assertEqual(await waiting, {"action": "retry"})

    async def test_first_matching_decision_wins(self):
        registry = PendingDecisionRegistry()
        published = asyncio.Event()

        def publish():
            self.assertTrue(registry.has_pending("task-1", "job-1"))
            published.set()

        waiting = asyncio.create_task(
            registry.publish_and_wait(
                task_id="task-1",
                job_id="job-1",
                device_id="robot-1",
                publish=publish,
                timeout=1,
            )
        )
        await published.wait()

        self.assertTrue(
            registry.resolve(
                task_id="task-1",
                job_id="job-1",
                device_id="robot-1",
                decision={"action": "retry"},
            )
        )
        self.assertFalse(
            registry.resolve(
                task_id="task-1",
                job_id="job-1",
                device_id="robot-1",
                decision={"action": "abort"},
            )
        )
        self.assertEqual(await waiting, {"action": "retry"})

    async def test_wrong_job_or_device_cannot_resolve_waiter(self):
        registry = PendingDecisionRegistry()
        published = asyncio.Event()
        waiting = asyncio.create_task(
            registry.publish_and_wait(
                task_id="task-1",
                job_id="job-1",
                device_id="robot-1",
                publish=published.set,
                timeout=1,
            )
        )
        await published.wait()

        self.assertFalse(
            registry.resolve(
                task_id="task-1",
                job_id="job-other",
                device_id="robot-1",
                decision={"action": "skip"},
            )
        )
        self.assertFalse(
            registry.resolve(
                task_id="task-1",
                job_id="job-1",
                device_id="robot-other",
                decision={"action": "skip"},
            )
        )
        self.assertTrue(
            registry.resolve(
                task_id="task-1",
                job_id="job-1",
                device_id="robot-1",
                decision={"action": "skip"},
            )
        )
        self.assertEqual((await waiting)["action"], "skip")

    async def test_timeout_defaults_to_abort_and_cleans_waiter(self):
        registry = PendingDecisionRegistry()
        decision = await registry.publish_and_wait(
            task_id="task-1",
            job_id="job-1",
            device_id="robot-1",
            publish=lambda: None,
            timeout=0.01,
        )

        self.assertEqual(decision["action"], "abort")
        self.assertFalse(registry.has_pending("task-1", "job-1"))


class ActionDecisionLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_max_retries_counts_additional_action_attempts(self):
        call_count = 0

        async def invoke():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("still failing")

        async def decide(_):
            return {"action": "retry"}

        with self.assertRaisesRegex(RuntimeError, "2 次重试"):
            await run_action_with_decisions(
                invoke=invoke,
                decide=decide,
                max_retries=2,
            )

        self.assertEqual(call_count, 3)

    async def test_retry_reexecutes_action(self):
        call_count = 0

        async def invoke():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("temporary")
            return "success"

        async def decide(_):
            return {"action": "retry"}

        result = await run_action_with_decisions(invoke=invoke, decide=decide)

        self.assertEqual(result, "success")
        self.assertEqual(call_count, 2)

    async def test_skip_returns_success_marker_without_reexecuting(self):
        call_count = 0

        async def invoke():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("known issue")

        async def decide(_):
            return {"action": "skip", "reason": "operator accepted"}

        result = await run_action_with_decisions(invoke=invoke, decide=decide)

        self.assertEqual(
            result,
            {"status": "skipped", "reason": "operator accepted"},
        )
        self.assertIsInstance(result, SkippedActionResult)
        self.assertEqual(call_count, 1)

    async def test_skip_shaped_action_output_is_not_framework_skip(self):
        async def invoke():
            return {"status": "skipped", "message": "ordinary action output"}

        async def decide(_):
            self.fail("successful Action output must not request a decision")

        result = await run_action_with_decisions(invoke=invoke, decide=decide)

        self.assertEqual(result, {"status": "skipped", "message": "ordinary action output"})
        self.assertNotIsInstance(result, SkippedActionResult)

    async def test_abort_reraises_original_exception(self):
        async def invoke():
            raise ValueError("fatal")

        async def decide(_):
            return {"action": "abort"}

        with self.assertRaisesRegex(ValueError, "fatal"):
            await run_action_with_decisions(invoke=invoke, decide=decide)


if __name__ == "__main__":
    unittest.main()
