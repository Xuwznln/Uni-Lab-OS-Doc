"""公开版启动、串行执行与时间线不能依赖商业算法源码。"""

import os
import subprocess
import sys

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler


def test_configured_platform_does_not_fall_back_before_engine_is_attached():
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, engine_managed=True)
    try:
        scheduler.submit_workflow(WorkflowSpec(
            workflow_id="awaiting-engine",
            nodes=[WorkflowNode(id="ready-node", device_id="reactor-a", action_name="run")],
        ))
        scheduler.reschedule()
        assert dispatcher.dispatched == []
        assert scheduler.time_schedule_snapshot()["lifecycle"] == "recovering"
        assert scheduler.timeline()["estimator"]["stats"] == {}
    finally:
        scheduler.close()


def test_production_queue_runs_when_cpsat_and_legacy_planners_are_unavailable(tmp_path):
    script = """
import importlib.abc
import sys
class NoCommercialAlgorithm(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("unilabos.scheduling.cpsat") or fullname in {
            "unilabos.app.scheduler.estimation", "unilabos.app.scheduler.timing",
            "unilabos.app.scheduler.time_planning", "unilabos.app.scheduler.time_scheduling",
            "unilabos.app.scheduler.timing_config",
        }:
            raise RuntimeError("公开版不应导入商业算法: " + fullname)
sys.meta_path.insert(0, NoCommercialAlgorithm())
from unilabos.app.scheduler import main
from tests.scheduler_core.conftest import persist_task
scheduler, bridge = main._scheduler, main._workflow_bridge
try:
    first = persist_task(bridge._store, task_name="public-a", devices=["reactor-a"])
    second = persist_task(bridge._store, task_name="public-b", devices=["reactor-b"])
    first_job = bridge.submit(first)["jobs"][0]["uuid"]
    second_job = bridge.submit(second)["jobs"][0]["uuid"]
    assert len(scheduler._dispatcher.dispatched) == 1
    scheduler.on_job_finished(first_job, True, {})
    assert len(scheduler._dispatcher.dispatched) == 2
    assert scheduler._dispatcher.dispatched[-1]["job_id"] == second_job
    scheduler.on_job_finished(second_job, True, {})
    timeline = scheduler.timeline()
    assert len(timeline["completed"]) == 2
    assert timeline["estimator"]["mode"] == "engine"
    assert timeline["estimator"]["stats"] == {}
    assert scheduler.time_schedule_snapshot()["algorithm"] == "unilab.queue"
finally:
    main._workflow_service.close()
    main._history.close()
"""
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UNILABOS_SCHEDULING_", "UNILABOS_TIME_SCHEDULING_", "ULAB_ESTIMATE_"))
    }
    environment.pop("ULAB_SCHEDULER_AGING_INTERVAL_SECONDS", None)
    environment.update(
        ULAB_DEVICE_STATE_DB="off", ULAB_WORKFLOW_HISTORY_DB=str(tmp_path / "workflow.sqlite"),
        ULAB_INVENTORY_DB="", PUBCHEM_ADDR="off",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
