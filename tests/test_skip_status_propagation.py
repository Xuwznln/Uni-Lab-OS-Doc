import json
import time
import unittest
from types import SimpleNamespace
from typing import Any, Callable, Optional

from action_msgs.msg import GoalStatus
from unilabos_msgs.action import EmptyIn

from unilabos.app.ws_client import JobInfo, JobStatus, QueueItem, WebSocketClient
from unilabos.ros.nodes.presets.host_node import HostNode


class _Logger:
    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: None


class _JobStatusBridge:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def publish_job_status(
        self,
        _result_data: dict[str, Any],
        _item: QueueItem,
        status: str,
        _return_info: Optional[dict[str, Any]],
    ) -> None:
        self.statuses.append(status)


class _HostHarness(HostNode):
    def __init__(self, job_id: str, bridge: _JobStatusBridge) -> None:
        self._goals = {job_id: object()}
        self.bridges = [bridge]
        self.lab_logger = lambda: _Logger()


def _queue_item() -> QueueItem:
    return QueueItem(
        task_type="job_call_back_status",
        device_id="fault-injection-device",
        action_name="raise_modbus_error",
        task_id="task-1",
        job_id="job-1",
        notebook_id="",
        device_action_key="fault-injection-device.raise_modbus_error",
    )


def _publish_host_result(return_info: dict[str, Any], action_name: str) -> list[str]:
    job_id = "job-host-status"
    bridge = _JobStatusBridge()
    host = _HostHarness(job_id, bridge)
    result_message = EmptyIn.Result()
    result_message.return_info = json.dumps(return_info)
    future = SimpleNamespace(
        result=lambda: SimpleNamespace(
            result=result_message,
            status=GoalStatus.STATUS_SUCCEEDED,
        )
    )

    host.get_result_callback(
        SimpleNamespace(job_id=job_id),
        action_name,
        future,
    )
    return bridge.statuses


class HostSkipStatusTest(unittest.TestCase):
    def test_user_skip_is_published_as_skipped(self) -> None:
        statuses = _publish_host_result(
            {
                "error": "",
                "suc": True,
                "suc_type": "user_bypass_error",
                "return_value": {
                    "status": "skipped",
                    "reason": "operator accepted",
                },
            },
            "raise_modbus_error",
        )
        self.assertEqual(statuses, ["skipped"])

    def test_action_return_value_cannot_spoof_skipped_status(self) -> None:
        statuses = _publish_host_result(
            {
                "error": "",
                "suc": True,
                "return_value": {
                    "status": "skipped",
                    "message": "ordinary action output",
                },
            },
            "ordinary_action",
        )
        self.assertEqual(statuses, ["success"])


class WebSocketSkipStatusTest(unittest.TestCase):
    def test_skipped_is_processed_as_terminal_job_status(self) -> None:
        client = WebSocketClient()
        job = JobInfo(
            job_id="job-1",
            task_id="task-1",
            device_id="fault-injection-device",
            notebook_id="",
            action_name="raise_modbus_error",
            device_action_key="fault-injection-device.raise_modbus_error",
            status=JobStatus.QUEUE,
            start_time=time.time(),
        )
        self.assertTrue(client.device_manager.add_queue_request(job))
        self.assertTrue(client.device_manager.start_job(job.job_id))

        client.publish_job_status({}, _queue_item(), "skipped", {"suc": True})

        self.assertIsNone(client.device_manager.get_job_info("job-1"))
        self.assertEqual(client.get_cached_job_start_response_status("job-1", "task-1"), "skipped")

    def test_late_failure_cannot_overwrite_cached_skipped_status(self) -> None:
        client = WebSocketClient()
        client.publish_job_status({}, _queue_item(), "skipped", {"suc": True})
        client.publish_job_status({}, _queue_item(), "failed", {"suc": False})

        self.assertEqual(client.get_cached_job_start_response_status("job-1", "task-1"), "skipped")
