"""@workflow 装饰器 + 构建 + 上报 + 端到端调度合同测试。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from unilabos.registry.ast_registry_scanner import _parse_file
from unilabos.registry.workflows import (
    DeviceCatalog,
    WorkflowBuildContext,
    build_workflow_payload,
    clear_registered_workflows,
    get_registered_workflows,
    report_workflows_to_service,
    workflow,
    workflow_uuid_for,
    _step_node_uuid,
)
from unilabos.server.database.repositories.workflow import WorkflowStore
from unilabos.server.services.workflow.service import WorkflowService


@pytest.fixture(autouse=True)
def _isolated_workflow_registry():
    """每个用例独立的进程内注册表。"""

    snapshot = get_registered_workflows()
    clear_registered_workflows()
    yield
    clear_registered_workflows()
    for definition in snapshot.values():
        from unilabos.registry.workflows import _registered_workflows

        _registered_workflows[definition.uuid] = definition


def _catalog() -> DeviceCatalog:
    catalog = DeviceCatalog()
    catalog.add("device-1", "demo_class", str(uuid.uuid4()))
    catalog.add("dup-a", "dup_class", str(uuid.uuid4()))
    catalog.add("dup-b", "dup_class", str(uuid.uuid4()))
    return catalog


def test_workflow_uuid_is_stable_and_path_scoped() -> None:
    assert workflow_uuid_for("pkg.mod:flow") == workflow_uuid_for("pkg.mod:flow")
    assert workflow_uuid_for("pkg.mod:flow") != workflow_uuid_for("pkg.mod:other")


def test_workflow_decorator_registers_definition_with_display_name() -> None:
    @workflow(display_name="演示流", description="d", tags=["t1"])
    def sample_flow(ctx: WorkflowBuildContext) -> None:
        ctx.run("device-1/succeed", {"value": 1})

    definitions = get_registered_workflows()
    assert len(definitions) == 1
    definition = next(iter(definitions.values()))
    assert definition.display_name == "演示流"
    assert definition.tags == ["t1"]
    assert definition.source_path.endswith("sample_flow")
    assert definition.uuid == workflow_uuid_for(definition.source_path)

    with pytest.raises(ValueError, match="display_name"):
        workflow(display_name="  ")


def test_step_node_uuid_orders_lexicographically() -> None:
    wf_uuid = str(uuid.uuid4())
    node_uuids = [_step_node_uuid(wf_uuid, index) for index in range(20)]
    assert node_uuids == sorted(node_uuids)
    for value in node_uuids:
        uuid.UUID(value)  # 均为合法 uuid
    # 稳定：同 workflow 同步骤 => 同节点 uuid
    assert _step_node_uuid(wf_uuid, 3) == _step_node_uuid(wf_uuid, 3)


def test_build_payload_resolves_run_and_run_template() -> None:
    @workflow(display_name="双步流")
    def two_steps(ctx: WorkflowBuildContext) -> None:
        ctx.run("external-device/do_thing", {"a": 1})
        ctx.run_template("demo_class/do_other", {"b": 2}, name="第二步")

    definition = next(iter(get_registered_workflows().values()))
    catalog = _catalog()
    payload = build_workflow_payload(definition, catalog)

    assert payload["workflow_uuid"] == definition.uuid
    assert payload["name"] == "双步流"
    assert payload["edges"] == []
    nodes = payload["nodes"]
    assert [node["action_name"] for node in nodes] == ["do_thing", "do_other"]
    # run: 显式 device_id；设备不在目录时 material_uuid 稳定占位
    assert nodes[0]["meta_data"]["target_device_id"] == "external-device"
    assert uuid.UUID(nodes[0]["material_uuid"])
    assert nodes[0]["param"] == {"a": 1}
    # run_template: 单实例自动填 device_id 与真实资源 uuid
    assert nodes[1]["meta_data"]["target_device_id"] == "device-1"
    assert nodes[1]["material_uuid"] == catalog.by_device_id["device-1"]["uuid"]
    assert nodes[1]["name"] == "第二步"
    # 节点 uuid 字典序 == 步骤序
    assert [node["uuid"] for node in nodes] == sorted(node["uuid"] for node in nodes)
    # 声明式步骤严格串行：第 i 步 execution_policy.depends_on 指向第 i-1 步
    assert nodes[0]["execution_policy"] == {}
    assert nodes[1]["execution_policy"] == {"depends_on": [nodes[0]["uuid"]]}


def test_run_template_requires_single_instance() -> None:
    @workflow(display_name="多实例流")
    def ambiguous(ctx: WorkflowBuildContext) -> None:
        ctx.run_template("dup_class/act", {})

    definition = next(iter(get_registered_workflows().values()))
    with pytest.raises(ValueError, match="多个实例"):
        build_workflow_payload(definition, _catalog())

    clear_registered_workflows()

    @workflow(display_name="零实例流")
    def missing(ctx: WorkflowBuildContext) -> None:
        ctx.run_template("nowhere_class/act", {})

    definition = next(iter(get_registered_workflows().values()))
    with pytest.raises(ValueError, match="没有类"):
        build_workflow_payload(definition, _catalog())


def test_step_target_must_contain_action() -> None:
    ctx = WorkflowBuildContext()
    with pytest.raises(ValueError, match="target"):
        ctx.run("only-device", {})
    with pytest.raises(ValueError, match="target"):
        ctx.run_template("only_class", {})


def test_report_workflows_is_idempotent_upsert() -> None:
    @workflow(display_name="上报流", tags=["demo"])
    def reported(ctx: WorkflowBuildContext) -> None:
        ctx.run("device-1/succeed", {"value": 5})
        ctx.run("device-1/succeed", {"value": 6})

    definition = next(iter(get_registered_workflows().values()))
    service = WorkflowService(WorkflowStore(":memory:"))
    try:
        catalog = _catalog()
        first = report_workflows_to_service(service, catalog)
        assert first == {definition.uuid: "上报流"}
        graph = service.get_graph(definition.uuid)
        assert len(graph["nodes"]) == 2

        # 重复上报（模拟重启）：uuid 不变、图被覆盖更新、不报错
        second = report_workflows_to_service(service, catalog)
        assert second == first
        graph = service.get_graph(definition.uuid)
        assert len(graph["nodes"]) == 2
        record = service.get_workflow(definition.uuid)
        assert record["name"] == "上报流"
    finally:
        service.close()


def test_ast_scanner_discovers_module_level_workflow(tmp_path: Path) -> None:
    module_path = tmp_path / "wf_module.py"
    module_path.write_text(
        "\n".join(
            [
                "from unilabos.registry.workflows import workflow",
                "",
                "@workflow(display_name='扫描流', description='desc', tags=['x'])",
                "def scanned_flow(ctx):",
                "    ctx.run('dev/act', {})",
                "",
                "def not_a_workflow(ctx):",
                "    pass",
            ]
        ),
        encoding="utf-8",
    )
    _devices, _resources, workflows = _parse_file(module_path, tmp_path)
    assert len(workflows) == 1
    meta = workflows[0]
    assert meta["function"] == "scanned_flow"
    assert meta["display_name"] == "扫描流"
    assert meta["tags"] == ["x"]
    assert meta["module"] == "wf_module"


def test_action_display_name_flows_from_decorator_to_registry(tmp_path: Path) -> None:
    from unilabos.registry.registry import Registry

    module_path = tmp_path / "display_driver.py"
    module_path.write_text(
        "\n".join(
            [
                "from unilabos.registry.decorators import action, device",
                "",
                "@device(id='display_demo_device', category=['test'])",
                "class Driver:",
                "    @action(description='d', display_name='友好动作名')",
                "    def do_thing(self, value: int = 0) -> dict:",
                "        return {'value': value}",
            ]
        ),
        encoding="utf-8",
    )
    devices, _resources, _workflows = _parse_file(module_path, tmp_path)
    assert (
        devices[0]["actions"]["do_thing"]["action_args"]["display_name"]
        == "友好动作名"
    )
    entry = Registry()._build_device_entry_from_ast("display_demo_device", devices[0])
    mapping = entry["class"]["action_value_mappings"]["do_thing"]
    assert mapping["display_name"] == "友好动作名"


class OrderedDriver:
    """e2e 用最小驱动：记录动作参数以断言执行顺序（须为模块级类，供类路径实例化）。"""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def record(self, value: int) -> dict:
        self.calls.append(value)
        return {"value": value}


def test_workflow_end_to_end_runs_via_local_scheduler() -> None:
    """@workflow -> 上报 -> 创建任务 -> HostLink 执行栈真实跑通并按序执行。"""

    from unilabos.backend.hostlink.adapter_registry import (
        clear_execution_adapter,
        set_execution_adapter,
    )
    from unilabos.backend.hostlink.backend import HostLinkBackend
    from unilabos.backend.hostlink.execution_adapter import HostLinkExecutionAdapter
    from unilabos.backend.hostlink.local_runtime import (
        HostLinkDriverSpec,
        HostLinkLocalRuntime,
    )
    from unilabos.server.backend.execution import JobExecutionBackend
    from unilabos.server.backend.scheduler.service import BackendScheduler

    local = HostLinkLocalRuntime()
    node = local.add_driver(
        HostLinkDriverSpec(
            device_id="wf-device",
            driver_class=OrderedDriver,
            config={},
            action_names=("record",),
            action_value_mappings={"record": {"type": "UniLabJsonCommand"}},
        )
    )
    runtime = HostLinkBackend(local, is_slave=False)
    local.start()
    adapter = HostLinkExecutionAdapter(
        runtime,
        devices_config=object(),
        resources_config=object(),
        bridges=[],
    )
    microbackend = JobExecutionBackend(host_node_getter=lambda: adapter)
    adapter.bridges = [microbackend]
    adapter.start()
    microbackend.start()
    set_execution_adapter(adapter)

    service = WorkflowService(WorkflowStore(":memory:"))
    scheduler = BackendScheduler(service, microbackend)
    service.set_task_submitter(scheduler.submit)
    scheduler.start(recover=True)
    try:
        @workflow(display_name="端到端流")
        def e2e_flow(ctx: WorkflowBuildContext) -> None:
            ctx.run("wf-device/record", {"value": 1})
            ctx.run("wf-device/record", {"value": 2})
            ctx.run("wf-device/record", {"value": 3})

        definition = next(iter(get_registered_workflows().values()))
        catalog = DeviceCatalog()
        catalog.add("wf-device", "wf_demo_class", str(uuid.uuid4()))
        reported = report_workflows_to_service(service, catalog)
        assert definition.uuid in reported

        task = service.create_workflow_task(
            workflow_uuid=definition.uuid,
            run_mode="normal",
            target_node_uuid=None,
            input_value={},
            description=None,
            meta_data={},
        )
        deadline = time.monotonic() + 5
        current = service.get_workflow_task(task["uuid"])
        while current["status"] not in {"succeeded", "failed"}:
            if time.monotonic() >= deadline:
                pytest.fail(f"workflow task 未在时限内结束: {current['status']}")
            time.sleep(0.02)
            current = service.get_workflow_task(task["uuid"])

        assert current["status"] == "succeeded"
        # execution_policy.depends_on 串行边 => 严格按声明序 1,2,3 执行
        assert node.driver.calls == [1, 2, 3]
        outputs = current["output"]
        assert {value["return_value"]["value"] for value in outputs.values()} == {1, 2, 3}
    finally:
        service.set_task_submitter(None)
        scheduler.stop()
        service.close()
        clear_execution_adapter(adapter)
        microbackend.stop()
        adapter.stop()
        runtime.stop()
