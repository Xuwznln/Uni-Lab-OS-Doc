"""默认单进程执行器在真实调用边界遵守工站暂停与原命令恢复。"""

import threading

import pytest

from tests.scheduler_core.conftest import build_core_runtime
from unilabos.app.edge_control.execution_start import confirm_execution_start
from unilabos.app.scheduler.backend import JobExecutionBackend


class RecordingHost:
    def __init__(self):
        self.calls = []
        self.called = threading.Event()
        self.before_start = lambda _item: None

    def send_goal(self, item, **kwargs):
        self.before_start(item)
        confirm_execution_start(item)
        self.calls.append(item)
        self.called.set()


def test_queued_action_waits_for_human_resume_and_starts_once(tmp_path):
    host = RecordingHost()
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    runtime = build_core_runtime(tmp_path, execution_backend=backend)
    try:
        source = runtime.submit(task_name="source-error", devices=["reactor-a"])
        waiting = runtime.submit(task_name="queued-action", devices=["reactor-b"])
        source_job = source["jobs"][0]["uuid"]
        waiting_job = waiting["jobs"][0]["uuid"]
        control = runtime.bridge.error_control
        control.report_error(source_job, phase="execution", message="设备错误")
        backend.start()
        assert backend.wait_idle()
        assert host.calls == []
        assert (
            runtime.workflow_store.get_job(waiting_job)["start_state"] == "NOT_STARTED"
        )

        snapshot = control.snapshot()
        control.resume(
            {
                "expected_station_version": snapshot["station_version"],
                "observed_error_epoch": snapshot["error_epoch"],
                "operator_confirmed": True,
                "confirm_snapshot_id": snapshot["snapshot_id"],
            },
            idempotency_key="human-resume",
            operator_id="tester",
        )
        runtime.bridge.reschedule()
        assert host.called.wait(2)
        assert [item.job_id for item in host.calls] == [waiting_job]
        assert (
            runtime.workflow_store.get_job(waiting_job)["start_state"]
            == "START_COMMITTED"
        )
        runtime.scheduler.restore_not_started_dispatches()
        assert backend.wait_idle()
        assert [item.job_id for item in host.calls] == [waiting_job]
    finally:
        backend.stop()
        runtime.close()


def test_pause_between_host_delivery_and_driver_call_keeps_running_results(tmp_path):
    """队列检查之后的新错误仍挡住动作；已开始动作的真实结果继续结算。"""
    host = RecordingHost()
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    runtime = build_core_runtime(tmp_path, execution_backend=backend)
    backend.add_job_finished_listener(runtime.scheduler.on_job_finished)
    try:
        first = runtime.submit(task_name="running", devices=["reactor-a"])["jobs"][0][
            "uuid"
        ]
        waiting = runtime.submit(task_name="waiting", devices=["reactor-b"])["jobs"][0][
            "uuid"
        ]
        control = runtime.bridge.error_control

        def pause_before_driver(item):
            if item.job_id == waiting:
                control.report_error(first, phase="execution", message="执行期间故障")

        host.before_start = pause_before_driver
        backend.start()
        assert backend.wait_idle()
        assert [item.job_id for item in host.calls] == [first]
        assert runtime.workflow_store.get_job(waiting)["start_state"] == "NOT_STARTED"
        backend.publish_job_status(
            {}, host.calls[0], "success", {"suc": True, "return_value": {"done": True}}
        )
        assert backend.wait_idle()
        assert runtime.workflow_store.get_job(first)["status"] == "succeeded"
        assert control.snapshot()["mode"] == "PAUSED"
        assert [item.job_id for item in host.calls] == [first]
    finally:
        backend.stop()
        runtime.close()


def test_cancel_before_worker_starts_never_reaches_device(tmp_path):
    """撤销已排队但未启动的动作必须有明确未发送证明。"""
    host = RecordingHost()
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    runtime = build_core_runtime(tmp_path, execution_backend=backend)
    try:
        aggregate = runtime.submit(task_name="cancel-queued", devices=["reactor-a"])
        runtime.bridge.cancel(aggregate["task"]["uuid"])
        backend.start()
        assert backend.wait_idle()
        assert host.calls == []
        assert (
            runtime.workflow_store.get_job(aggregate["jobs"][0]["uuid"])["status"]
            == "canceled"
        )
    finally:
        backend.stop()
        runtime.close()


@pytest.mark.parametrize("engine_mode", ["legacy", "queue", "engine_fault"])
@pytest.mark.parametrize("gate", ["open", "recovering_before_admission", "draining", "recovering_before_start"])
def test_manual_action_bypasses_same_device_action_queue_while_paused(tmp_path, engine_mode, gate):
    """真实单进程队列中已有同设备同动作在途时，手动动作仍可独立开始。"""
    from tests.app.test_device_action_run_api import (
        DEVICE_RESOURCE_TEMPLATE_UUID,
        DeviceActionRegistry,
    )
    from unilabos.registry.template_projection import RegistryTemplateProjection
    from unilabos.scheduling.protocol import EngineError
    from unilabos.scheduling.queue import QueueEngine
    from unilabos.workflow.service import WorkflowService

    class RunRegistry(DeviceActionRegistry):
        def obtain_registry_device_info(self):
            devices = super().obtain_registry_device_info()
            actions = devices[0]["class"]["action_value_mappings"]
            actions["run"] = actions.pop("hold")
            return devices

    host = RecordingHost()
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    runtime = build_core_runtime(
        tmp_path, execution_backend=backend,
        scheduling_engine=QueueEngine() if engine_mode != "legacy" else None,
        max_in_flight_jobs=1,
    )
    try:
        source = runtime.submit(task_name="manual-source", devices=["reactor-a"])
        source_job = source["jobs"][0]["uuid"]
        backend.start()
        assert host.called.wait(2)
        host.called.clear()
        projection = RegistryTemplateProjection(
            runtime.workflow_store,
            authority_id="local",
            resource_template_identity_resolver=lambda _name: (
                DEVICE_RESOURCE_TEMPLATE_UUID
            ),
        )
        projection.refresh(RunRegistry())
        device_id = runtime.device_materials["reactor-a"]
        service = WorkflowService(
            runtime.workflow_store,
            task_scheduler_bridge=runtime.bridge,
            material_resolver=lambda identity: (
                {
                    "uuid": device_id,
                    "resource_template_uuid": DEVICE_RESOURCE_TEMPLATE_UUID,
                    "meta_data": {"edge_local_id": "reactor-a"},
                }
                if identity == device_id
                else None
            ),
        )
        control = runtime.bridge.error_control
        decision = control.report_error(
            source_job, phase="execution", message="人工调整"
        )
        snapshot = control.snapshot()
        entered = runtime.bridge.decide_error(
            decision["decision_id"],
            {
                "action": "enter_manual_handling",
                "expected_station_version": snapshot["station_version"],
                "expected_decision_version": decision["decision_version"],
                "observed_error_epoch": snapshot["error_epoch"],
            },
            idempotency_key="manual-enter",
            operator_id="tester",
        )
        station = entered["station"]
        session = station["active_session"]
        action = service.error_handling.manual_actions(
            session["session_id"], device_id
        )[0]
        if engine_mode == "engine_fault":
            runtime.scheduler._engine_runtime.fault(EngineError("ENGINE_UNAVAILABLE", "测试引擎退出"))
        if gate == "recovering_before_admission":
            control.recovering = True
        elif gate == "draining":
            runtime.scheduler.begin_drain()
        elif gate == "recovering_before_start":
            host.before_start = lambda _item: setattr(control, "recovering", True)
        result = service.error_handling.execute_manual(
            session["session_id"],
            {
                "device_id": device_id,
                "action_id": action["action_id"],
                "action_version": action["action_version"],
                "parameters": {"duration_seconds": 2},
                "expected_station_version": station["station_version"],
                "expected_session_version": session["version"],
            },
            idempotency_key="manual-run",
            operator_id="tester",
        )
        manual_job = result["manual_action"]["execution_job"]["uuid"]
        if gate != "open":
            assert backend.wait_idle()
            assert [item.job_id for item in host.calls] == [source_job]
            assert runtime.workflow_store.get_job(manual_job)["start_state"] == "NOT_STARTED"
            if gate != "recovering_before_start":
                assert runtime.workflow_store.get_job(manual_job)["status"] == "pending"
            control.recovering = False
            host.before_start = lambda _item: None
            if gate == "draining":
                runtime.scheduler.resume_from_drain()
            runtime.bridge.reschedule()
            runtime.scheduler.restore_not_started_dispatches()
        assert host.called.wait(2)
        assert [item.job_id for item in host.calls] == [source_job, manual_job]
        assert host.calls[-1].execution_source == "error_manual"
        assert host.calls[-1].device_action_key == host.calls[0].device_action_key
        assert control.snapshot()["mode"] == "PAUSED"
        assert (
            runtime.workflow_store.get_job(manual_job)["start_state"]
            == "START_COMMITTED"
        )
        if engine_mode != "legacy":
            engine = runtime.scheduler._engine_runtime
            snapshot = engine.projection.capture()
            manual = next(node for node in snapshot["task_view"]["nodes"] if node["job_id"] == manual_job)
            assert manual["kind"] == "manual" and manual["manual_mode"] == "intervention"
            assert not manual["ready"]
            assert not any(item["attempt_id"] == manual_job for item in engine.journal.intents("station"))
            pool = next(item for item in snapshot["capacity_view"]["pools"] if item["pool_id"] == "domain:jobs")
            assert pool["used"] == 2 and pool["limit"] == 1
            if engine_mode == "queue":
                assert engine.session.last_error is None
            else:
                assert engine.session.last_error.code == "ENGINE_UNAVAILABLE"
        runtime.scheduler.restore_not_started_dispatches()
        assert backend.wait_idle()
        assert [item.job_id for item in host.calls] == [source_job, manual_job]
    finally:
        backend.stop()
        runtime.close()
