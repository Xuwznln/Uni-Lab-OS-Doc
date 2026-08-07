import time
import unittest
from queue import Empty, Queue

from unilabos.app.ws_client import (
    DeviceActionManager,
    JobInfo,
    JobStatus,
    MessageProcessor,
    QueueProcessor,
)


class StartedJobKeepaliveTest(unittest.TestCase):
    def test_all_started_jobs_are_kept_alive_until_terminal_state(self):
        send_queue = Queue()
        manager = DeviceActionManager()
        processor = MessageProcessor("ws://unused", send_queue, manager)
        processor.connected = True
        queue_processor = QueueProcessor(manager, processor)
        queue_processor.keepalive_interval_s = 0.2
        queue_processor.keepalive_need_more_s = 20

        job = JobInfo(
            job_id="job-1",
            task_id="task-1",
            device_id="robot-1",
            notebook_id="",
            action_name="move",
            device_action_key="robot-1:move",
            status=JobStatus.QUEUE,
            start_time=time.time(),
        )
        should_start, _ = manager.enqueue_job(job)
        self.assertTrue(should_start)

        always_free_job = JobInfo(
            job_id="job-2",
            task_id="task-1",
            device_id="sensor-1",
            notebook_id="",
            action_name="read",
            device_action_key="sensor-1:read",
            status=JobStatus.QUEUE,
            start_time=time.time(),
            always_free=True,
        )
        should_start, _ = manager.enqueue_job(always_free_job)
        self.assertTrue(should_start)

        queue_processor.start()
        try:
            keepalive = send_queue.get(timeout=0.5)
            self.assertEqual(
                keepalive,
                {
                    "action": "report_action_state",
                    "data": {
                        "type": "job_call_back_status",
                        "device_id": "robot-1",
                        "action_name": "move",
                        "task_id": "task-1",
                        "job_id": "job-1",
                        "notebook_id": "",
                        "free": False,
                        "need_more": 21,
                    },
                },
            )
            always_free_keepalive = send_queue.get(timeout=0.5)
            self.assertEqual(always_free_keepalive["action"], "report_action_state")
            self.assertEqual(always_free_keepalive["data"]["job_id"], "job-2")
            self.assertFalse(always_free_keepalive["data"]["free"])
            self.assertEqual(always_free_keepalive["data"]["need_more"], 21)

            for _ in range(3):
                queue_processor.notify_queue_update()
            with self.assertRaises(Empty):
                send_queue.get(timeout=0.05)

            manager.end_job(job.job_id)
            manager.end_job(always_free_job.job_id)
            time.sleep(queue_processor.keepalive_interval_s + 0.05)
            with self.assertRaises(Empty):
                send_queue.get_nowait()
        finally:
            queue_processor.stop()


if __name__ == "__main__":
    unittest.main()
