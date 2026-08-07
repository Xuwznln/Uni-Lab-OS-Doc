import asyncio
import unittest

from unilabos.utils.action_decision import (
    PendingDecisionRegistry,
    run_action_with_decisions,
)
from unilabos.utils.exception import (
    DeviceException,
    EmergencyStopError,
    UserAction,
)


class DeviceExceptionTest(unittest.TestCase):
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


class PendingDecisionRegistryTest(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual(call_count, 1)

    async def test_abort_reraises_original_exception(self):
        async def invoke():
            raise ValueError("fatal")

        async def decide(_):
            return {"action": "abort"}

        with self.assertRaisesRegex(ValueError, "fatal"):
            await run_action_with_decisions(invoke=invoke, decide=decide)


if __name__ == "__main__":
    unittest.main()
