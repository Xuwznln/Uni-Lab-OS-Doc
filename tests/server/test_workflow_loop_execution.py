"""循环容器节点：@workflow 声明 -> 模板 -> 实例化 -> 本机调度器逐轮执行。

覆盖 for（迭代变量占位符）、while（设备状态 / 节点输出条件）、安全上限、循环体失败、
嵌套循环，以及图校验与执行计划里的层级信息。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, Optional

import pytest

from unilabos.registry.workflows import (
    DeviceCatalog,
    WorkflowBuildContext,
    build_workflow_template_payload,
    clear_registered_workflows,
    get_registered_workflows,
    materialize_workflow_template,
    upsert_workflow,
    workflow,
)
from unilabos.server.services.runtime.workflow.service import WorkflowError, WorkflowService


@pytest.fixture(autouse=True)
def _isolated_workflow_registry():
    snapshot = get_registered_workflows()
    clear_registered_workflows()
    yield
    clear_registered_workflows()
    from unilabos.registry.workflows import _registered_workflows

    for definition in snapshot.values():
        _registered_workflows[definition.uuid] = definition


class LoopDriver:
    """记录动作参数以断言轮次与顺序（须为模块级类，供类路径实例化）。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.fail_on: Optional[Any] = None

    def record(self, value: Any = None, label: str = "") -> dict:
        if self.fail_on is not None and value == self.fail_on:
            raise RuntimeError(f"boom at {value}")
        self.calls.append(value if not label else (label, value))
        return {"value": value, "label": label, "count": len(self.calls)}

    def probe(self) -> dict:
        return {"ready": len(self.calls) >= 3, "count": len(self.calls)}


class _Stack:
    """HostLink 执行栈 + 本机调度器；device_state 由测试注入的读取器提供。"""

    def __init__(self, device_state_reader: Optional[Callable[[str], Dict[str, Any]]] = None) -> None:
        from unilabos.backend.hostlink.backend import HostLinkBackend
        from unilabos.backend.hostlink.host_node import HostNode
        from unilabos.backend.hostlink.local_runtime import HostLinkDriverSpec, HostLinkLocalRuntime
        from unilabos.server.backend.execution import JobExecutionBackend
        from unilabos.server.backend.scheduler.service import BackendScheduler

        self.local = HostLinkLocalRuntime()
        self.node = self.local.add_driver(
            HostLinkDriverSpec(
                device_id="loop-device",
                driver_class=LoopDriver,
                config={},
                action_names=("record", "probe"),
                action_value_mappings={
                    "record": {"type": "UniLabJsonCommand"},
                    "probe": {"type": "UniLabJsonCommand"},
                },
            )
        )
        self.runtime = HostLinkBackend(self.local, is_slave=False)
        self.local.start()
        self.adapter = HostNode("host_node", self.runtime, bridges=[])
        self.microbackend = JobExecutionBackend(host_node_getter=lambda: self.adapter)
        self.adapter.bridges = [self.microbackend]
        self.microbackend.start()
        self.service = WorkflowService(":memory:")
        self.scheduler = BackendScheduler(
            self.service, self.microbackend, device_state_reader=device_state_reader
        )
        self.service.set_task_submitter(self.scheduler.submit)
        self.scheduler.start(recover=True)

    @property
    def driver(self) -> LoopDriver:
        return self.node.driver

    def close(self) -> None:
        from unilabos.backend.hostlink.adapter_registry import clear_execution_adapter
        from unilabos.backend.hostlink.host_node import HostNode

        self.service.set_task_submitter(None)
        self.scheduler.stop()
        self.service.close()
        clear_execution_adapter(self.adapter)
        self.microbackend.stop()
        self.adapter.stop()
        HostNode.reset_state()
        self.runtime.stop()

    def run(self, timeout: float = 8.0) -> Dict[str, Any]:
        definition = next(iter(get_registered_workflows().values()))
        catalog = DeviceCatalog()
        catalog.add("loop-device", "loop_demo_class", str(uuid.uuid4()))
        template = build_workflow_template_payload(definition)
        record = upsert_workflow(self.service, materialize_workflow_template(template, catalog))
        task = self.service.create_workflow_task(
            workflow_uuid=record["uuid"],
            run_mode="normal",
            target_node_uuid=None,
            input_value={},
            description=None,
            meta_data={},
        )
        deadline = time.monotonic() + timeout
        current = self.service.get_workflow_task(task["uuid"])
        while current["status"] not in {"succeeded", "failed", "canceled"}:
            if time.monotonic() >= deadline:
                pytest.fail(f"workflow task 未在时限内结束: {current['status']}")
            time.sleep(0.02)
            current = self.service.get_workflow_task(task["uuid"])
        return current

    def runs_by_name(self, task_uuid: str) -> Dict[str, Dict[str, Any]]:
        snapshot = {
            node["uuid"]: node
            for node in self.service.get_workflow_task(task_uuid)["workflow_snapshot"]["nodes"]
        }
        return {
            snapshot[run["workflow_node_uuid"]]["name"]: run
            for run in self.service.list_workflow_node_runs(task_uuid)
        }


@pytest.fixture
def stack():
    instance = _Stack()
    try:
        yield instance
    finally:
        instance.close()


def test_step_mode_executes_one_hostlink_leaf_inside_nested_loops(stack: _Stack) -> None:
    """一次 step 不代表跑完整个循环：真实 HostLink 驱动每次只能收到一个叶动作。"""
    @workflow(display_name="嵌套逐步")
    def flow(ctx: WorkflowBuildContext) -> None:
        with ctx.loop_for(2, name="外层"):
            with ctx.loop_for(2, name="内层"):
                ctx.run("loop-device/record", {"value": "{{loop.index}}"}, name="记录")

    definition = next(iter(get_registered_workflows().values()))
    catalog = DeviceCatalog()
    catalog.add("loop-device", "loop_demo_class", str(uuid.uuid4()))
    record = upsert_workflow(stack.service, materialize_workflow_template(build_workflow_template_payload(definition), catalog))
    task = stack.service.create_workflow_task(
        workflow_uuid=record["uuid"], run_mode="step", target_node_uuid=None,
        input_value={}, description=None, meta_data={},
    )
    time.sleep(0.05)
    assert stack.driver.calls == []
    for count in range(1, 5):
        current = stack.service.get_workflow_task(task["uuid"])
        stack.service.command_workflow_task(task["uuid"], command_type="step",
                                           expected_revision=current["control_revision"],
                                           idempotency_key=f"loop-click-{count}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = stack.service.get_workflow_task(task["uuid"])
            if len(stack.driver.calls) == count and current["control_status"] == "paused":
                break
            time.sleep(0.01)
        time.sleep(0.04)
        assert len(stack.driver.calls) == count
        assert current["control_status"] == "paused"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and stack.service.get_workflow_task(task["uuid"])["status"] != "succeeded":
        time.sleep(0.01)
    assert stack.service.get_workflow_task(task["uuid"])["status"] == "succeeded"
    assert stack.driver.calls == [0, 1, 0, 1]
    runs = stack.runs_by_name(task["uuid"])
    assert runs["记录"]["attempt_count"] == 4
    assert len(stack.service.list_workflow_tasks(page=1, page_size=10)["items"]) == 1


def test_for_loop_runs_the_body_per_iteration_with_loop_variables(stack: _Stack) -> None:
    @workflow(display_name="for 循环")
    def flow(ctx: WorkflowBuildContext) -> None:
        ctx.run("loop-device/record", {"value": "start"}, name="开始")
        with ctx.loop_for(3, name="三轮"):
            ctx.run("loop-device/record", {"value": "{{loop.index}}", "label": "r{{loop.iteration}}/{{loop.count}}"}, name="记录")
        ctx.run("loop-device/record", {"value": "end"}, name="结束")

    task = stack.run()
    assert task["status"] == "succeeded", task["error_info"]
    # 占位符：整串占位符保留 int 类型，嵌入式做文本替换；循环体按轮执行、前后步骤各一次
    assert stack.driver.calls == ["start", ("r1/3", 0), ("r2/3", 1), ("r3/3", 2), "end"]

    runs = stack.runs_by_name(task["uuid"])
    loop_run = runs["三轮"]
    assert loop_run["status"] == "succeeded"
    assert loop_run["return_info"]["return_value"]["iterations"] == 3
    assert loop_run["control_data"]["loop"]["iteration"] == 2
    assert loop_run["control_data"]["loop"]["count"] == 3
    body_run = runs["记录"]
    assert body_run["status"] == "succeeded"
    # 每轮一个 attempt：首轮 initial，其后 loop_iteration
    assert [attempt["trigger"] for attempt in body_run["attempts"]] == [
        "initial",
        "loop_iteration",
        "loop_iteration",
    ]
    assert body_run["attempt_count"] == 3
    # 任务输出取节点最后一轮结果
    assert task["output"][body_run["workflow_node_uuid"]]["return_value"]["value"] == 2


def test_while_loop_polls_device_state_until_the_condition_turns_false() -> None:
    state: Dict[str, Any] = {"progress": 0}

    def reader(device_id: str) -> Dict[str, Any]:
        assert device_id == "loop-device"
        return {"progress": {"value": state["progress"], "updated_at": 0}}

    stack = _Stack(device_state_reader=reader)
    try:

        @workflow(display_name="while 设备状态")
        def flow(ctx: WorkflowBuildContext) -> None:
            with ctx.loop_while(
                ctx.device_state("loop-device", "progress", "<", 3), name="等进度"
            ):
                ctx.run("loop-device/record", {"value": "tick"}, name="推进")

        # 每次循环体执行后设备状态推进一格（模拟设备上报）
        original = stack.driver.record

        def record(value: Any = None, label: str = "") -> dict:
            result = original(value, label)
            state["progress"] += 1
            return result

        stack.driver.record = record  # type: ignore[method-assign]
        task = stack.run()
        assert task["status"] == "succeeded", task["error_info"]
        assert stack.driver.calls == ["tick", "tick", "tick"]
        loop_run = stack.runs_by_name(task["uuid"])["等进度"]
        assert loop_run["return_info"]["return_value"]["iterations"] == 3
        assert loop_run["control_data"]["loop"]["condition"] == "loop-device.progress < 3"
    finally:
        stack.close()


def test_while_loop_repeats_until_a_body_step_reports_ready(stack: _Stack) -> None:
    """条件引用循环体里的探测步骤：首轮前没有产出 => 先跑一轮（do-while 语义），
    之后每轮看最新探测结果。"""

    @workflow(display_name="重复直到就绪")
    def flow(ctx: WorkflowBuildContext) -> None:
        with ctx.loop_while(ctx.step_output("探测", "ready", "==", False), name="直到就绪") as loop:
            ctx.run("loop-device/record", {"value": "work"}, name="干活")
            ctx.run("loop-device/probe", {}, name="探测")
        assert loop.is_loop
        ctx.run("loop-device/record", {"value": "done"}, name="收尾")

    task = stack.run()
    assert task["status"] == "succeeded", task["error_info"]
    # probe 在 record 满 3 次后 ready=True：干活 3 轮，然后收尾
    assert stack.driver.calls == ["work", "work", "work", "done"]
    runs = stack.runs_by_name(task["uuid"])
    assert runs["直到就绪"]["return_info"]["return_value"]["iterations"] == 3
    assert runs["探测"]["attempt_count"] == 3
    assert runs["探测"]["return_info"]["return_value"]["ready"] is True


def test_step_output_by_name_must_resolve_to_exactly_one_step() -> None:
    with pytest.raises(ValueError, match="找不到该步骤"):

        @workflow(display_name="坏引用")
        def flow(ctx: WorkflowBuildContext) -> None:
            with ctx.loop_while(ctx.step_output("不存在", "ready", "==", False)):
                ctx.run("loop-device/record", {}, name="干活")

        build_workflow_template_payload(next(iter(get_registered_workflows().values())))


def test_while_loop_hits_max_iterations_and_fails_the_task(stack: _Stack) -> None:
    @workflow(display_name="死循环保护")
    def flow(ctx: WorkflowBuildContext) -> None:
        first = ctx.run("loop-device/record", {"value": "seed"}, name="种子")
        with ctx.loop_while(
            ctx.step_output(first, "value", "==", "seed"), max_iterations=2, name="永真"
        ):
            ctx.run("loop-device/record", {"value": "spin"}, name="空转")

    task = stack.run()
    assert task["status"] == "failed"
    loop_run = stack.runs_by_name(task["uuid"])["永真"]
    assert loop_run["status"] == "failed"
    assert loop_run["error_info"][0]["code"] == "loop_failed"
    assert "max_iterations=2" in loop_run["error_info"][0]["message"]
    assert loop_run["return_info"]["return_value"]["iterations"] == 2
    assert stack.driver.calls == ["seed", "spin", "spin"]


def test_body_failure_fails_the_loop_and_the_task(stack: _Stack) -> None:
    @workflow(display_name="循环体失败")
    def flow(ctx: WorkflowBuildContext) -> None:
        with ctx.loop_for(3, name="三轮"):
            ctx.run("loop-device/record", {"value": "{{loop.index}}"}, name="记录")
        ctx.run("loop-device/record", {"value": "never"}, name="不该执行")

    stack.driver.fail_on = 1
    task = stack.run()
    assert task["status"] == "failed"
    runs = stack.runs_by_name(task["uuid"])
    assert stack.driver.calls == [0]
    assert runs["三轮"]["status"] == "failed"
    assert runs["三轮"]["return_info"]["return_value"] == {"iterations": 1, "failed_iteration": 2}
    assert runs["记录"]["status"] == "failed"
    assert runs["不该执行"]["status"] == "canceled"


def test_nested_loops_reset_the_inner_iteration_each_outer_round(stack: _Stack) -> None:
    @workflow(display_name="嵌套循环")
    def flow(ctx: WorkflowBuildContext) -> None:
        with ctx.loop_for(2, name="外层"):
            ctx.run("loop-device/record", {"value": "outer-{{loop.index}}"}, name="外层步")
            with ctx.loop_for(2, name="内层"):
                ctx.run("loop-device/record", {"value": "inner-{{loop.index}}"}, name="内层步")

    task = stack.run()
    assert task["status"] == "succeeded", task["error_info"]
    assert stack.driver.calls == [
        "outer-0",
        "inner-0",
        "inner-1",
        "outer-1",
        "inner-0",
        "inner-1",
    ]
    runs = stack.runs_by_name(task["uuid"])
    assert runs["内层"]["attempt_count"] == 2  # 外层每轮重臂一次
    assert runs["内层步"]["attempt_count"] == 4
    assert runs["外层"]["return_info"]["return_value"]["iterations"] == 2


def test_execution_plan_records_the_loop_hierarchy_and_order() -> None:
    service = WorkflowService(":memory:")
    try:

        @workflow(display_name="计划层级")
        def flow(ctx: WorkflowBuildContext) -> None:
            ctx.run("loop-device/record", {"value": 1}, name="a")
            with ctx.loop_for(2, name="L"):
                ctx.run("loop-device/record", {"value": 2}, name="b")
                ctx.run("loop-device/record", {"value": 3}, name="c")
            ctx.run("loop-device/record", {"value": 4}, name="d")

        definition = next(iter(get_registered_workflows().values()))
        template = build_workflow_template_payload(definition)
        assert [node["kind"] for node in template["nodes"]] == ["action", "loop", "action", "action", "action"]
        assert template["nodes"][2]["parent"] == template["nodes"][1]["key"]
        # 顶层 a -> L -> d，循环体 b -> c；容器与自己的循环体之间没有边
        assert template["edges"] == [
            {"source": "step-0", "target": "step-1"},
            {"source": "step-2", "target": "step-3"},
            {"source": "step-1", "target": "step-4"},
        ]
        catalog = DeviceCatalog()
        catalog.add("loop-device", "loop_demo_class", str(uuid.uuid4()))
        materialized = materialize_workflow_template(template, catalog)
        loop_node = next(node for node in materialized["nodes"] if node["type"] == "loop")
        body = [node for node in materialized["nodes"] if node.get("parent_uuid") == loop_node["uuid"]]
        assert [node["name"] for node in body] == ["b", "c"]
        record = upsert_workflow(service, materialized)
        service.set_task_submitter(None)
        task = service.create_workflow_task(
            workflow_uuid=record["uuid"],
            run_mode="normal",
            target_node_uuid=None,
            input_value={},
            description=None,
            meta_data={},
        )
        plan_nodes = task["execution_plan"]["nodes"]
        names = {node["uuid"]: node["name"] for node in materialized["nodes"]}
        assert [names[node["uuid"]] for node in plan_nodes] == ["a", "L", "b", "c", "d"]
        assert [node["kind"] for node in plan_nodes] == [
            "device_action",
            "loop",
            "device_action",
            "device_action",
            "device_action",
        ]
        assert plan_nodes[2]["parent_uuid"] == loop_node["uuid"]
        assert "parent_uuid" not in plan_nodes[4]
        runs = service.list_workflow_node_runs(task["uuid"])
        assert [run["executor_kind"] for run in runs] == [
            "device_action",
            "loop",
            "device_action",
            "device_action",
            "device_action",
        ]
    finally:
        service.close()


def test_restart_resets_in_flight_loop_attempt_instead_of_asking_for_reconciliation() -> None:
    """循环容器没有设备副作用：进程重启时在飞的循环 attempt 退回 pending 续跑，
    只有循环体里在飞的设备 attempt 才转 execution_unknown 等人裁决。"""

    service = WorkflowService(":memory:")
    try:

        @workflow(display_name="重启恢复")
        def flow(ctx: WorkflowBuildContext) -> None:
            with ctx.loop_for(3, name="L"):
                ctx.run("loop-device/record", {"value": "{{loop.index}}"}, name="b")

        definition = next(iter(get_registered_workflows().values()))
        catalog = DeviceCatalog()
        catalog.add("loop-device", "loop_demo_class", str(uuid.uuid4()))
        record = upsert_workflow(
            service, materialize_workflow_template(build_workflow_template_payload(definition), catalog)
        )
        task = service.create_workflow_task(
            workflow_uuid=record["uuid"],
            run_mode="normal",
            target_node_uuid=None,
            input_value={},
            description=None,
            meta_data={},
        )
        runs = {run["executor_kind"]: run for run in service.list_workflow_node_runs(task["uuid"])}
        loop_run, body_run = runs["loop"], runs["device_action"]
        # 模拟：第 2 轮进行中，循环 attempt running，循环体 attempt 也 running
        service.mark_workflow_node_job_running(loop_run["current_job_uuid"])
        service.mark_workflow_node_job_running(body_run["current_job_uuid"])
        service.record_workflow_node_job_terminal(
            body_run["current_job_uuid"], status="succeeded", return_info={"return_value": {"value": 0}}
        )
        outcome = service.begin_workflow_loop_iteration(
            loop_run["uuid"],
            iteration=1,
            progress={"mode": "for", "count": 3},
            body_run_uuids=[body_run["uuid"]],
        )
        next_job = outcome["next_jobs"][body_run["uuid"]]
        assert next_job["trigger"] == "loop_iteration"
        assert next_job["attempt_no"] == 2
        assert outcome["run"]["control_data"]["loop"] == {"mode": "for", "count": 3, "iteration": 1}
        assert outcome["run"]["return_info"]["return_value"] == {"index": 1, "iteration": 2, "count": 3}
        service.mark_workflow_node_job_running(next_job["uuid"])

        # 新进程接管
        prepared = service.prepare_workflow_task_execution(task["uuid"])
        assert prepared["state"] == "waiting_reconciliation"
        by_kind = {run["executor_kind"]: run for run in prepared["runs"]}
        assert by_kind["loop"]["status"] == "pending"  # 循环续跑，不裁决
        assert by_kind["loop"]["control_data"]["loop"]["iteration"] == 1
        assert by_kind["device_action"]["status"] == "execution_unknown"  # 设备 attempt 要裁决
    finally:
        service.close()


def _loop_graph_nodes(*, loop_param: Dict[str, Any], body: bool = True, extra: Optional[list] = None) -> list:
    # 节点 uuid 在权威里全局唯一，每张图都用新的
    loop_uuid = str(uuid.uuid4())
    body_uuid = str(uuid.uuid4())
    nodes = [
        {
            "uuid": loop_uuid,
            "name": "L",
            "type": "loop",
            "param": loop_param,
            "pose": {},
            "meta_data": {},
            "execution_policy": {},
        }
    ]
    if body:
        nodes.append(
            {
                "uuid": body_uuid,
                "name": "b",
                "type": "device_action",
                "parent_uuid": loop_uuid,
                "material_uuid": str(uuid.uuid4()),
                "action_name": "record",
                "param": {"value": 1},
                "pose": {},
                "meta_data": {"target_device_id": "loop-device"},
                "execution_policy": {},
            }
        )
    nodes.extend(extra or [])
    return nodes


def test_graph_validation_rejects_bad_loops() -> None:
    service = WorkflowService(":memory:")
    try:

        def save(nodes: list) -> None:
            record = service.create_workflow(
                name="v", tags=[], description=None, meta_data={}, workflow_uuid=str(uuid.uuid4())
            )
            service.save_graph(record["uuid"], revision=record["revision"], nodes=nodes, edges=[])

        with pytest.raises(WorkflowError):
            save(_loop_graph_nodes(loop_param={"mode": "for"}))
        # 空循环体的 while 必须有轮询间隔
        with pytest.raises(WorkflowError):
            save(
                _loop_graph_nodes(
                    loop_param={
                        "mode": "while",
                        "condition": {"source": "device_state", "device_id": "d", "field": "f", "op": "==", "value": 1},
                    },
                    body=False,
                )
            )
        save(
            _loop_graph_nodes(
                loop_param={
                    "mode": "while",
                    "condition": {"source": "device_state", "device_id": "d", "field": "f", "op": "==", "value": 1},
                    "interval_seconds": 1,
                },
                body=False,
            )
        )
        # 循环体外的节点不能引用 {{loop.*}}
        outsider = {
            "uuid": str(uuid.uuid4()),
            "name": "z",
            "type": "device_action",
            "material_uuid": str(uuid.uuid4()),
            "action_name": "record",
            "param": {"value": "{{loop.index}}"},
            "pose": {},
            "meta_data": {"target_device_id": "loop-device"},
            "execution_policy": {},
        }
        with pytest.raises(WorkflowError):
            save(_loop_graph_nodes(loop_param={"mode": "for", "count": 2}, extra=[outsider]))
        save(_loop_graph_nodes(loop_param={"mode": "for", "count": 2}))
    finally:
        service.close()
