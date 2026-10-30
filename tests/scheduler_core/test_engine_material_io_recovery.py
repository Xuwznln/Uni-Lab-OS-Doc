"""本地库存效果与 Workflow 结果之间中断时，只恢复可核验的原效果。"""

import pytest

from tests.scheduler_core.conftest import build_core_runtime
from tests.scheduler_core.test_material_io_runtime import _install_load_target, _load_param, _submit_material_io
from unilabos.scheduling.queue import QueueEngine
from unilabos.workflow.service import WorkflowService


@pytest.mark.parametrize("committed", [False, True])
def test_material_io_recovery_uses_committed_effect_without_reexecuting(tmp_path, monkeypatch, committed):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        template, mount, site = _install_load_target(runtime, "recovery")
        _, job_id = _submit_material_io(runtime, task_name="io-recovery", param=_load_param(template, mount, site))
        material_io = runtime.inventory.material_io
        settle = material_io.settle_load
        calls = []

        def crash_at_inventory_boundary(**values):
            calls.append(values["permit"]["effect_uuid"])
            if committed:
                settle(**values)
            raise RuntimeError("模拟库存调用返回前崩溃")

        monkeypatch.setattr(material_io, "settle_load", crash_at_inventory_boundary)
        service = WorkflowService(runtime.workflow_store, task_scheduler_bridge=runtime.bridge)
        with pytest.raises(RuntimeError, match="模拟库存调用返回前崩溃"):
            service.decide_manual_confirmation(job_id, action="approve")
        assert runtime.workflow_store.get_job(job_id)["start_state"] == "START_COMMITTED"
        before = runtime.inventory_store.query_one("SELECT occupied_material_uuid FROM site WHERE uuid=?", (site,))
        runtime.bridge.recover_active_tasks()
        runtime.bridge.reschedule()
        # 恢复不能再次调用写入口，即使库存调用本身具备幂等键。
        assert len(calls) == 1
        assert runtime.inventory_store.query_one("SELECT occupied_material_uuid FROM site WHERE uuid=?", (site,)) == before
        job = runtime.workflow_store.get_job(job_id)
        if committed:
            assert job["status"] == "succeeded"
            assert job["return_info"]["material"]["uuid"] == before["occupied_material_uuid"]
        else:
            assert job["status"] == "running"
            assert job["uncertainty_reason"]
            assert before["occupied_material_uuid"] is None
            assert runtime.bridge.active_or_uncertain_job_ids() == {job_id}
    finally:
        runtime.close()


def test_approved_material_io_waits_for_resume_then_settles(tmp_path):
    runtime = build_core_runtime(tmp_path, scheduling_engine=QueueEngine())
    try:
        template, mount, site = _install_load_target(runtime, "paused")
        aggregate, job_id = _submit_material_io(runtime, task_name="io-paused", param=_load_param(template, mount, site))
        runtime.bridge.pause(aggregate["task"]["uuid"])
        WorkflowService(runtime.workflow_store, task_scheduler_bridge=runtime.bridge).decide_manual_confirmation(job_id, action="approve")
        assert runtime.workflow_store.get_job(job_id)["start_state"] == "NOT_STARTED"
        assert runtime.inventory_store.query_one("SELECT occupied_material_uuid FROM site WHERE uuid=?", (site,))["occupied_material_uuid"] is None
        runtime.bridge.resume(aggregate["task"]["uuid"])
        runtime.bridge.reschedule()
        assert runtime.workflow_store.get_job(job_id)["status"] == "succeeded"
        assert runtime.dispatcher.dispatched == []
    finally:
        runtime.close()
