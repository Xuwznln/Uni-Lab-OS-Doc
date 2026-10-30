"""运行库、设备事实到统一引擎协议的公开投影边界。"""

import json

import pytest

from tests.scheduler_core.conftest import build_core_runtime, stable_uuid
from tests.scheduler_core.test_control_flow import (
    attach_resource_plan,
    dependency,
    device_node,
    job,
    submit_repeat_task,
)
from tests.scheduler_core.test_material_io_runtime import _install_load_target, _load_param, _submit_material_io
from tests.scheduler_core.test_resource_admission import transfer_task
from unilabos.app.scheduler.engine_projection import RuntimeSnapshot
from unilabos.app.scheduler.inventory.backend_contract import BackendResourceService
from unilabos.scheduling.protocol import EngineError
from unilabos.scheduling.queue import QUEUE_CONFIG, QueueEngine
from unilabos.workflow.scheduling_journal import SchedulingJournal
from unilabos.workflow.scheduling_session import SchedulingSession


def test_snapshot_includes_unregistered_task_and_preserves_uncertain_occupancy(core_runtime):
    runtime = core_runtime
    first = runtime.submit(task_name="projection-active", devices=["reactor-a"])
    job_id = runtime.dispatcher.dispatched[0]["job_id"]
    runtime.scheduler.on_execution_process_restarted((job_id,))
    waiting = runtime.persist(task_name="projection-unregistered", devices=["reactor-b"])
    journal = SchedulingJournal(runtime.workflow_store)
    session = SchedulingSession(
        journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
    )
    session.start()
    projection = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station")
    snapshot = projection.capture()
    tasks = {task["task_id"]: task for task in snapshot["task_view"]["tasks"]}
    active_id, waiting_id = first["task"]["uuid"], waiting["uuid"]
    assert tasks[active_id]["status"] == "failed"
    assert tasks[active_id]["control"] == "recovery_hold"
    assert not tasks[active_id]["terminal_settled"]
    # 未注册 Edge 的 Task 不能在队列和配额视图中消失。
    assert tasks[waiting_id]["status"] == "pending"
    assert tasks[waiting_id]["admission"] == "pending"
    assert not tasks[waiting_id]["terminal_settled"]
    flight = snapshot["inflight"][0]
    assert flight["job_id"] == flight["attempt_id"] == job_id
    assert flight["status"] == "uncertain"
    assert not flight["continuation_allowed"]
    assert any(item["kind"] == "uncertain" for item in snapshot["resource_view"]["allocations"])
    pool = next(pool for pool in snapshot["capacity_view"]["pools"] if pool["kind"] == "inflight_jobs")
    assert pool["used"] == 1
    assert pool["holders"][0]["attempt_id"] == job_id
    assert session.decide(snapshot)["decision"]["dispatch_now"] == []
    # 快照重建不能改变稳定队序或已经冻结的参数修订。
    again = projection.capture()
    assert again["task_view"] == snapshot["task_view"]
    session.close()


@pytest.mark.parametrize("state", ["busy", "offline"])
def test_unavailable_devices_never_become_dispatchable_options(tmp_path, state):
    online = {"value": True}
    runtime = build_core_runtime(tmp_path, device_available=lambda _: online["value"])
    try:
        runtime.submit(task_name="projection-owner", devices=["reactor-a"])
        runtime.scheduler.begin_drain()
        waiting = runtime.submit(task_name="projection-waiter", devices=["reactor-a"])
        online["value"] = state != "offline"
        journal = SchedulingJournal(runtime.workflow_store)
        session = SchedulingSession(
            journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
        )
        session.start()
        snapshot = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station").capture()
        options = snapshot["resource_view"]["options"]
        if state == "busy":
            assert len(options) == 1 and not options[0]["eligible_now"]
            assert options[0]["blockers"][0]["code"] == "RESOURCE_BUSY"
        else:
            assert options == []
            node = next(node for node in snapshot["task_view"]["nodes"] if node["ref"]["task_id"] == waiting["task"]["uuid"])
            assert node["blockers"][0]["code"] == "DEVICE_OFFLINE"
        assert len(snapshot["inflight"]) == 1
        assert session.decide(snapshot)["decision"]["dispatch_now"] == []
        session.close()
    finally:
        runtime.close()


def test_bindings_and_parameter_revisions_follow_platform_edits(core_runtime):
    runtime = core_runtime
    runtime.scheduler.begin_drain()
    aggregate = runtime.submit(task_name="projection-binding", devices=["reactor-a"])
    task_id = aggregate["task"]["uuid"]
    job_id = aggregate["jobs"][0]["uuid"]
    journal = SchedulingJournal(runtime.workflow_store)
    session = SchedulingSession(
        journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
    )
    session.start()
    projection = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station")
    first = projection.capture()
    node = first["task_view"]["nodes"][0]
    option = first["resource_view"]["options"][0]
    assert node["ready"] and node["parameters_final"]
    assert option["node"] == node["ref"]
    assert option["device_id"] == runtime.device_materials["reactor-a"]
    assert option["resource_ids"] == [f"/devices/{runtime.device_materials['reactor-a']}"]
    assert option["eligible_now"]
    runtime.bridge.pause(task_id)
    runtime.bridge.update_pending_job_param(task_id, job_id, {"temperature": 50})
    changed = projection.capture()
    assert changed["task_view"]["nodes"][0]["parameters_revision"] != node["parameters_revision"]
    assert changed["resource_view"]["options"][0]["option_id"] != option["option_id"]
    assert changed["resource_view"]["options"][0]["resolved_parameters"] == {"temperature": 50}
    assert projection.capture()["resource_view"]["options"] == changed["resource_view"]["options"]
    assert runtime.dispatcher.dispatched == []
    assert session.decide(changed)["decision"]["dispatch_now"] == []
    session.close()


def test_current_loop_instances_and_result_inputs_remain_distinct(core_runtime):
    runtime = core_runtime
    aggregate = submit_repeat_task(runtime)
    task_id = aggregate["task"]["uuid"]
    journal = SchedulingJournal(runtime.workflow_store)
    session = SchedulingSession(
        journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
    )
    session.start()
    projection = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station")
    first = projection.capture()
    region = first["task_view"]["control_regions"][0]
    assert region["kind"] == "loop" and region["phase"] == "body_active"
    assert region["iteration_no"] == 0 and region["unmaterialized_tail"]
    assert not first["task_view"]["tasks"][0]["known_graph_complete"]
    assert len(region["members"]) == 2
    assert len(region["exit_nodes"]) == 1
    assert len(region["input_nodes"]) == 2
    assert region["controller"] not in region["members"]
    controller = next(node for node in first["task_view"]["nodes"] if node["ref"] == region["controller"])
    assert controller["status"] == "running" and not controller["ready"]
    assert len(first["inflight"]) == 1  # 本地循环 controller 不能伪装成物理在途。
    assert session.decide(first)["decision"]["dispatch_now"] == []
    measurement = runtime.dispatcher.dispatched[-1]["job_id"]
    runtime.scheduler.on_job_finished(measurement, True, {"qualified": False})
    adjustment = runtime.dispatcher.dispatched[-1]["job_id"]
    runtime.scheduler.on_job_finished(adjustment, True, {"next_dose": 2})
    second = projection.capture()
    next_region = second["task_view"]["control_regions"][0]
    assert next_region["region_id"] == region["region_id"]
    assert next_region["iteration_no"] == 1
    assert {ref["node_id"] for ref in next_region["members"]}.isdisjoint(
        ref["node_id"] for ref in region["members"]
    )
    assert {node["ref"]["task_id"] for node in second["task_view"]["nodes"]} == {task_id}
    assert session.decide(second)["decision"]["dispatch_now"] == []
    session.close()


def test_condition_success_does_not_complete_its_active_branch(core_runtime):
    runtime = core_runtime
    identities = [stable_uuid("projection-condition:" + name) for name in ("control", "selected", "skipped")]
    control_id, selected, skipped = identities
    body = {
        "predecessor_node_uuids": [], "bindings": {},
        "branches": [
            {"label": "if", "condition": {"lit": True}, "node_uuids": [selected], "entry_node_uuids": [selected], "exit_node_uuids": [selected]},
            {"label": "else", "condition": None, "node_uuids": [skipped], "entry_node_uuids": [skipped], "exit_node_uuids": [skipped]},
        ],
    }
    runtime.scheduler.begin_drain()
    runtime.submit_frozen(
        task_name="projection-condition",
        execution_plan={
            "version": 2, "run_mode": "normal",
            "capabilities": ["condition_expression_v1", "control_regions_v1"],
            "nodes": [
                {"uuid": control_id, "parent_uuid": None, "kind": "condition", "param": body, "control_region": body, "execution_policy": {}, "action_resource_contract": {}},
                *[
                    device_node(
                        node_uuid=identity, parent_uuid=control_id, device_id="reactor-a",
                        material_uuid=runtime.device_materials["reactor-a"], action="run",
                    ) for identity in (selected, skipped)
                ],
            ],
            "handles": [], "edges": [dependency(control_id, identity, name=identity) for identity in (selected, skipped)],
        },
        jobs=[
            job(job_uuid=stable_uuid("projection-job:" + identity), node_uuid=identity, index=index,
                kind="condition" if index == 0 else "device_action", param=body if index == 0 else {})
            for index, identity in enumerate(identities)
        ],
    )
    journal = SchedulingJournal(runtime.workflow_store)
    session = SchedulingSession(
        journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
    )
    session.start()
    projection = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station")
    before = projection.capture()
    assert before["task_view"]["control_regions"][0]["phase"] == "pending"
    assert before["task_view"]["control_regions"][0]["unmaterialized_tail"]
    runtime.scheduler.resume_from_drain()
    active = projection.capture()
    region = active["task_view"]["control_regions"][0]
    assert region["phase"] == "body_active"
    assert not region["unmaterialized_tail"]
    assert set(ref["node_id"] for ref in region["members"]) == {selected, skipped}
    assert session.decide(active)["decision"]["dispatch_now"] == []
    runtime.scheduler.on_job_finished(runtime.dispatcher.dispatched[-1]["job_id"], True, {})
    settled = projection.capture()
    assert settled["task_view"]["control_regions"][0]["phase"] == "resolved"
    assert settled["task_view"]["tasks"][0]["terminal_settled"]
    assert session.decide(settled)["decision"]["dispatch_now"] == []
    session.close()


def test_held_interval_remains_visible_and_owner_can_continue(core_runtime):
    runtime = core_runtime
    identities = [stable_uuid("projection-interval:" + str(index)) for index in range(2)]
    nodes = [
        device_node(
            node_uuid=identity, parent_uuid=None, device_id="reactor-a",
            material_uuid=runtime.device_materials["reactor-a"], action="run",
        ) for identity in identities
    ]
    edges = [dependency(*identities, name="projection-interval")]
    resource_plan = attach_resource_plan(runtime, name="projection-interval", nodes=nodes, edges=edges, resource_scopes=[])
    aggregate = runtime.submit_frozen(
        task_name="projection-interval",
        execution_plan={
            "version": 1, "run_mode": "normal", "nodes": nodes, "edges": edges, "handles": [],
            "resource_plan": resource_plan, "capabilities": resource_plan["capabilities"],
        },
        jobs=[
            job(job_uuid=stable_uuid("projection-interval-job:" + identity), node_uuid=identity, index=index, kind="device_action")
            for index, identity in enumerate(identities)
        ],
    )
    journal = SchedulingJournal(runtime.workflow_store)
    session = SchedulingSession(
        journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
    )
    session.start()
    projection = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station")
    first = projection.capture()
    interval = first["resource_view"]["intervals"][0]
    assert len(interval["members"]) == 2 and interval["held_allocation_ids"]
    assert not first["resource_view"]["options"][0]["eligible_now"]
    assert session.decide(first)["decision"]["dispatch_now"] == []
    runtime.scheduler.begin_drain()
    runtime.scheduler.on_job_finished(runtime.dispatcher.dispatched[-1]["job_id"], True, {})
    continuation = projection.capture()
    held = continuation["resource_view"]["intervals"][0]
    assert held["interval_id"] == interval["interval_id"]
    assert held["held_allocation_ids"]
    assert len(continuation["inflight"]) == 0
    allocations = {item["allocation_id"]: item for item in continuation["resource_view"]["allocations"]}
    assert all(allocations[key]["kind"] == "continuous" for key in held["held_allocation_ids"])
    assert continuation["resource_view"]["options"][0]["eligible_now"]
    assert not continuation["task_view"]["tasks"][0]["terminal_settled"]
    assert session.decide(continuation)["decision"]["dispatch_now"] == []
    runtime.scheduler.resume_from_drain()
    runtime.scheduler.on_job_finished(runtime.dispatcher.dispatched[-1]["job_id"], True, {})
    finished = projection.capture()
    assert finished["resource_view"]["allocations"] == []
    assert all(not item["held_allocation_ids"] for item in finished["resource_view"]["intervals"])
    assert finished["task_view"]["tasks"][0]["task_id"] == aggregate["task"]["uuid"]
    assert finished["task_view"]["tasks"][0]["terminal_settled"]
    session.close()


def test_material_io_projects_inventory_scope_without_selecting_a_device(core_runtime):
    runtime = core_runtime
    template, mount, site = _install_load_target(runtime, "projection")
    runtime.scheduler.begin_drain()
    _submit_material_io(runtime, task_name="projection-io", param=_load_param(template, mount, site))
    journal = SchedulingJournal(runtime.workflow_store)
    session = SchedulingSession(
        journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
    )
    session.start()
    projection = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station")
    snapshot = projection.capture()
    option = snapshot["resource_view"]["options"][0]
    assert option["device_id"] is None
    assert set(option["resource_ids"]) == {
        f"material/{mount}/exclusive", f"material/{mount}/site/{site}/exclusive",
    }
    assert snapshot["task_view"]["nodes"][0]["manual_mode"] == "material_io"
    session.decide(snapshot)
    runtime.scheduler.resume_from_drain()
    admitted = projection.capture()
    assert len(admitted["inflight"]) == 1
    assert admitted["inflight"][0]["manual_phase"] == "awaiting_approval"
    assert admitted["inflight"][0]["start_state"] == "NOT_STARTED"
    assert not admitted["inflight"][0]["continuation_allowed"]
    assert runtime.dispatcher.dispatched == []
    session.decide(admitted)
    session.close()


def test_external_busy_facts_are_preserved_and_provider_failure_rejects_snapshot(core_runtime):
    runtime = core_runtime
    runtime.scheduler.begin_drain()
    runtime.submit(task_name="external-facts", devices=["reactor-a"])
    runtime.scheduler._busy_key_provider = lambda: {"/devices/reactor-a/run"}
    journal = SchedulingJournal(runtime.workflow_store)
    session = SchedulingSession(
        journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
    )
    session.start()
    projection = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station")
    snapshot = projection.capture()
    assert not snapshot["resource_view"]["options"][0]["eligible_now"]
    external = snapshot["resource_view"]["allocations"]
    assert len(external) == 1 and external[0]["kind"] == "external"
    assert external[0]["owner_task_id"] is None and external[0]["release_boundary"] is None
    assert external[0]["resource_ids"] == [f"/devices/{runtime.device_materials['reactor-a']}"]
    session.decide(snapshot)

    def unavailable():
        raise OSError("占用事实源断开")

    runtime.scheduler._busy_key_provider = unavailable
    with pytest.raises(EngineError, match="SNAPSHOT_INVALID"):
        projection.capture()
    session.close()


def test_transfer_binding_contains_source_target_gripper_and_executor(core_runtime):
    runtime = core_runtime
    backend = BackendResourceService(runtime.inventory_store)
    template = backend.sync_resource_templates([{
        "id": "projection.transfer-item", "display_name": "投影测试物料", "registry_type": "material", "class": {},
    }])["templates"][0]
    material = backend.create_material({"resource_template_uuid": template["uuid"], "name": "测试烧杯"})
    sites = [
        ("source", "warehouse-a", material["uuid"], {}),
        ("target", "reactor-a", None, {}),
        ("gripper", "robot-a", None, {"unilab": {"resource_role": "robot.gripper"}}),
    ]
    with runtime.inventory_store.transaction() as connection:
        for label, owner, occupied, metadata in sites:
            connection.execute(
                """INSERT INTO site(uuid,create_time,update_time,meta_data,material_uuid,name,
                sort_order,allowed_resource_template_uuids,occupied_material_uuid,
                position_x,position_y,position_z,depth,length,width)
                VALUES (?,?,?,?,?,?,0,'[]',?,0,0,0,0,0,0)""",
                (stable_uuid("projection-transfer:" + label), "2026-10-20", "2026-10-20", json.dumps(metadata),
                 runtime.device_materials[owner], label, occupied),
            )
    runtime.scheduler.begin_drain()
    transfer_task(
        runtime, task_name="projection-transfer", executor="robot-a", material_uuid=material["uuid"],
        target_owner_uuid=runtime.device_materials["reactor-a"],
        site_uuids=[stable_uuid("projection-transfer:target")], named_group=False,
    )
    journal = SchedulingJournal(runtime.workflow_store)
    session = SchedulingSession(
        journal, QueueEngine(), domain_id="station", engine_id="unilab.queue", config=QUEUE_CONFIG,
    )
    session.start()
    projection = RuntimeSnapshot(runtime.workflow_store, runtime.scheduler, journal, domain_id="station")
    snapshot = projection.capture()
    option = snapshot["resource_view"]["options"][0]
    required = {f"material/{material['uuid']}/exclusive"}
    for label, owner, _, _ in sites:
        required.add(f"material/{runtime.device_materials[owner]}/site/{stable_uuid('projection-transfer:' + label)}/exclusive")
        required.add(f"/devices/{runtime.device_materials[owner]}")
    assert set(option["resource_ids"]) == required
    assert option["resolved_parameters"]["source_site"] == "source"
    assert option["resolved_parameters"]["site"] == "target"
    assert option["eligible_now"]
    session.decide(snapshot)
    assert runtime.dispatcher.dispatched == []
    runtime.scheduler.resume_from_drain()
    actual = projection.capture()["inflight"][0]
    assert set(actual["resource_ids"]) == required
    assert actual["parameters"] == option["resolved_parameters"]
    session.close()
