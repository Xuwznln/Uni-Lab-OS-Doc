"""通过真实 ROS 动作包装器验证驱动显式开始回执与上下文隔离。"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from unilabos.app.edge_control.execution_observation import ExecutionStartReporter, report_execution_started
from unilabos.app.job_queue import QueueItem
from unilabos.config.config import BasicConfig
from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode, ROS2DeviceNode
from unilabos.ros.nodes.presets.host_node import HostNode


class Message:
    @classmethod
    def get_fields_and_field_types(cls):
        return {}


class Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("reports", [False, True])
@pytest.mark.parametrize("host_action", [False, True])
def test_driver_reports_only_after_its_ack_through_real_action_callback(monkeypatch, asynchronous, reports, host_action):
    async def scenario():
        events = []
        receipt_id = str(uuid4())
        guard_calls = []
        driver_calls = []

        def observe():
            assert guard_calls == ["WAIT", "START"]
            assert events == []
            driver_calls.append("started")
            # 适配器在这里已取得当前命令的启动回执；无此能力则省略。
            if reports:
                report_execution_started(
                    receipt_id=receipt_id, started_at_ms=1800000000500,
                    source_ref="device:reactor-a:command-ack:35",
                )
            return "completed"

        class Driver:
            def run(self):
                return observe()

            async def run_async(self):
                await asyncio.sleep(0)
                return observe()

        node = object.__new__(HostNode if host_action else BaseROS2DeviceNode)
        node.device_id = "reactor-a"
        node.driver_instance = Driver()
        node._job_contexts = {}
        node._job_contexts_lock = threading.Lock()
        node.callback_group = None
        node.lab_logger = lambda: Logger()
        node.sleep = lambda _seconds: asyncio.sleep(0.001)
        node._executor = ThreadPoolExecutor(max_workers=1)
        job_id = str(uuid4())

        def guard():
            assert events == []
            result = "WAIT" if not guard_calls else "START"
            guard_calls.append(result)
            return result

        action = "run_async" if asynchronous else "run"
        item = QueueItem(
            "job_call_back_status", "reactor-a", action, str(uuid4()), job_id, "", f"/devices/reactor-a/{action}",
            start_guard=guard,
            execution_start_reporter=ExecutionStartReporter(job_id, str(uuid4()), 1, str(uuid4()), events.append),
        )
        mapping = {"type": SimpleNamespace(Feedback=Message, Result=Message), "goal": {}, "feedback": {}, "result": {}}
        monkeypatch.setattr(
            ROS2DeviceNode, "run_async_func",
            staticmethod(lambda action, trace_error=False, **kwargs: asyncio.create_task(action(**kwargs))),
        )
        monkeypatch.setattr(
            ROS2DeviceNode, "async_wait_for", staticmethod(lambda *_args, **_kwargs: asyncio.sleep(0.001)),
        )
        monkeypatch.setattr(BasicConfig, "action_mode", "real")
        host = node if host_action else object.__new__(HostNode)
        host.device_id = "reactor-a" if host_action else "host"
        host.lab_logger = lambda: Logger()
        host.devices_instances = {} if host_action else {"reactor-a": SimpleNamespace(_ros_node=node)}
        callbacks = []

        class ActionClient:
            _action_type = SimpleNamespace(Goal=Message)

            def wait_for_server(self):
                return True

            def send_goal_async(self, goal_msg, *, feedback_callback, goal_uuid):
                assert guard_calls == [] and events == []
                goal = SimpleNamespace(
                    goal_id=SimpleNamespace(uuid=list(UUID(job_id).bytes)), request=goal_msg, succeed=lambda: None,
                )
                callbacks.append(asyncio.create_task(node._create_execute_callback(action, mapping)(goal)))
                return SimpleNamespace(add_done_callback=lambda _callback: None)

        host._action_clients = {f"/devices/reactor-a/{action}": ActionClient()}
        try:
            host.send_goal(item, "normal", {}, {})
            await callbacks[0]
            assert driver_calls == ["started"]
            assert len(events) == int(reports)
            if reports:
                assert events[0]["receipt_id"] == receipt_id
                assert events[0]["job_uuid"] == job_id
            with pytest.raises(RuntimeError, match="报告器"):
                report_execution_started(receipt_id=receipt_id, started_at_ms=1800000000500, source_ref="outside")
        finally:
            node._executor.shutdown()

    asyncio.run(scenario())
