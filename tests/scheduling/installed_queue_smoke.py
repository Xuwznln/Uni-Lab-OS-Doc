"""离开源码目录后，验证公开宿主和第三方策略共用串行、人工与恢复语义。"""

import importlib.abc
import os
import sys
from pathlib import Path
from uuid import uuid4


class NoSolver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("ortools", "unilabos.scheduling.cpsat")):
            raise RuntimeError("公开平台执行不能导入求解器：" + fullname)
sys.meta_path.insert(0, NoSolver())
plugin_path = sys.argv[2] if len(sys.argv) > 2 else None
os.environ["ULAB_WORKFLOW_HISTORY_DB"] = str(Path.cwd() / ("plugin-workflow.sqlite" if plugin_path else "workflow.sqlite"))
os.environ["ULAB_INVENTORY_DB"] = ""
if plugin_path:
    os.environ["UNILABOS_SCHEDULING_ENGINE"] = "plugin"
    os.environ["UNILABOS_SCHEDULING_MANIFEST"] = plugin_path
from unilabos.app.scheduler import main
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.scheduling.startup import configured_engine
from unilabos.workflow.task_input import PreparedTaskInput
from unilabos.workflow.task_scheduler_bridge import TaskSchedulerBridge

scheduler, bridge = main._scheduler, main._workflow_bridge
def task(device, *, manual=False):
    node_id, job_id = str(uuid4()), str(uuid4())
    plan = {
        "version": 1, "run_mode": "normal", "target_node_uuid": None,
        "nodes": [{
            "uuid": node_id, "kind": "manual_confirm" if manual else "device_action", "device_id": device,
            "action_name": "run", "action_type": "UniLabJsonCommand", "param": {},
            "param_schema": {"type": "object", "properties": {
                "goal": {"type": "object", "properties": {}, "additionalProperties": True}
            }, "additionalProperties": False}, "material_requirements": [], "material_uuid": "",
            **({"manual_confirmation": {"timeout_seconds": 3600}} if manual else {}),
        }], "handles": [], "edges": [],
    }
    prepared = PreparedTaskInput(workflow_snapshot={}, resolved_input={}, execution_plan=plan, jobs=[{
        "uuid": job_id, "workflow_node_uuid": node_id, "topological_index": 0,
        "executor_kind": "manual_confirm" if manual else "device_action",
        "execution_policy": {}, "execution_timeout_seconds": 0, "param": {},
    }])
    return bridge._store.create_task_with_jobs(
        workflow_uuid=str(uuid4()), task_uuid=str(uuid4()), run_mode="normal",
        target_node_uuid=None, description=None, meta_data={}, priority="normal",
        plan_builder=lambda _: prepared, applied_graph={},
    )
try:
    scheduler.begin_drain()
    tasks = [task("reactor-a"), task("reactor-b", manual=True), task("reactor-c")]
    jobs = [bridge.submit(item)["jobs"][0]["uuid"] for item in tasks]
    dispatcher = scheduler._dispatcher
    assert dispatcher.dispatched == []
    scheduler.resume_from_drain()
    expected = list(reversed(jobs)) if plugin_path else jobs
    assert [item["job_id"] for item in dispatcher.dispatched] == expected[:1]
    scheduler.on_job_finished(expected[0], True, {})
    manual = bridge._store.get_job(expected[1])
    assert manual["status"] == "running" and manual["start_state"] == "NOT_STARTED", manual
    assert len(dispatcher.dispatched) == 1
    # 只重启调度控制器，执行端和持久事实保持；人工批准必须续行原尝试。
    store = bridge._store
    bridge.close()
    scheduler = EdgeScheduler(dispatcher=dispatcher, engine_managed=True)
    selected = configured_engine()
    bridge = TaskSchedulerBridge(store, scheduler=scheduler, **selected.bridge_arguments())
    bridge.recover_active_tasks()
    assert len(dispatcher.dispatched) == 1
    bridge.decide_manual_confirmation(expected[1], action="approve")
    assert [item["job_id"] for item in dispatcher.dispatched] == expected[:2]
    assert dispatcher.dispatched[1]["command_uuid"] == manual["edge_command_uuid"]
    scheduler.on_job_finished(expected[1], True, {})
    assert [item["job_id"] for item in dispatcher.dispatched] == expected
    scheduler.on_job_finished(expected[2], True, {})
    assert all(store.get_task(item["uuid"])["status"] == "succeeded" for item in tasks)
    assert scheduler.time_schedule_snapshot()["algorithm"] == ("example.newest_task" if plugin_path else "unilab.queue")
finally:
    bridge.close()
    main._workflow_service.close()
    main._history.close()
