"""物料锁（@action lock_resource）与报错异常决策的调度联动测试。

覆盖：
- lock_resource 声明的资源被在执行 job 占用 → 后续节点跨设备也串行
- 实体型物料需求（instance_uuid / barcode）自动并入锁键
- suc_type=skip（异常后人工跳过）→ 节点算成功推进，但已消费物料 quarantined
- JobExecutionBackend 解析 return_info.suc_type 并 4 参回调（兼容旧 3 参 listener）
- 本地异常决策通道：HostNode 持有 pending，scheduler backend 只提供
  list/resolve REST 适配，不把决策路由回设备节点等待器
"""

from typing import Any, Dict, List, Optional

from unilabos.app.scheduler.backend import (
    JobExecutionBackend,
    make_device_lock_resource_resolver,
)
from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.inventory.domain import MaterialRequirement
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.app.scheduler.models import WorkflowEdge, WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler, _extract_resource_ids
from unilabos.app.ws_client import QueueItem
from unilabos.registry.action_policy import ERROR_DECISION_TARGET_MICRO_BACKEND
from unilabos.utils.type_check import serialize_result_info


def _node(node_id, device="dev1", action="run", param=None, materials=None):
    return WorkflowNode(
        id=node_id, device_id=device, action_name=action, action_type="goal",
        param=param or {}, material_requirements=materials or [],
    )


def _edge(src, dst):
    return WorkflowEdge(uuid=f"{src}->{dst}", source_node_id=src, target_node_id=dst)


class TestExtractResourceIds:
    def test_shapes(self):
        assert _extract_resource_ids("rack-1") == {"rack-1"}
        assert _extract_resource_ids({"name": "rack-1"}) == {"rack-1"}
        assert _extract_resource_ids({"id": "r2", "name": "ignored-lower-priority"}) == {"r2"}
        assert _extract_resource_ids({"data": {"unilabos_uuid": "u-9"}}) == {"u-9"}
        assert _extract_resource_ids([{"name": "a"}, "b", None]) == {"a", "b"}
        assert _extract_resource_ids(None) == set()
        assert _extract_resource_ids(42) == set()


class TestLockResourceSerializesAcrossDevices:
    def _make(self):
        dispatcher = RecordingDispatcher()
        # 两台设备的 run 动作都声明 lock_resource=["plate"]
        scheduler = EdgeScheduler(
            dispatcher=dispatcher,
            lock_resource_resolver=lambda device_id, action_name: ["plate"],
        )
        return scheduler, dispatcher

    def test_shared_resource_waits(self):
        """A(dev1) 与 B(dev2) 用同一 plate：设备锁不冲突，物料锁强制串行。"""
        scheduler, dispatcher = self._make()
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", device="dev1", param={"plate": {"name": "rack-1"}}),
                _node("B", device="dev2", param={"plate": "rack-1"}),
            ],
        )
        result = scheduler.submit_workflow(spec)
        assert len(result["dispatched"]) == 1  # 只有一个拿到 rack-1 锁

        snap = scheduler.snapshot()
        (job_id, job), = snap["inflight_jobs"].items()
        assert job["resource_locks"] == ["res:rack-1"]

        # 持锁 job 完成 → 锁释放 → 另一节点下发
        r2 = scheduler.on_job_finished(job_id, success=True)
        assert len(r2["dispatched"]) == 1
        assert {d["node_id"] for d in dispatcher.dispatched} == {"A", "B"}

    def test_disjoint_resources_parallel(self):
        scheduler, _ = self._make()
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", device="dev1", param={"plate": "rack-1"}),
                _node("B", device="dev2", param={"plate": "rack-2"}),
            ],
        )
        result = scheduler.submit_workflow(spec)
        assert len(result["dispatched"]) == 2  # 不同资源，跨设备并行

    def test_resolver_absent_no_lock(self):
        dispatcher = RecordingDispatcher()
        scheduler = EdgeScheduler(dispatcher=dispatcher)  # 未注入 resolver
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", device="dev1", param={"plate": "rack-1"}),
                _node("B", device="dev2", param={"plate": "rack-1"}),
            ],
        )
        assert len(scheduler.submit_workflow(spec)["dispatched"]) == 2


class TestInstanceRequirementLock:
    def test_same_instance_serialized_without_resolver(self):
        """实体型物料需求即使没有 lock_resource 声明也天然互斥。"""
        dispatcher = RecordingDispatcher()
        scheduler = EdgeScheduler(dispatcher=dispatcher)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", device="dev1", materials=[MaterialRequirement(instance_uuid="mi-1")]),
                _node("B", device="dev2", materials=[MaterialRequirement(instance_uuid="mi-1")]),
            ],
        )
        result = scheduler.submit_workflow(spec)
        assert len(result["dispatched"]) == 1
        job_id = result["dispatched"][0]["job_id"]
        r2 = scheduler.on_job_finished(job_id, success=True)
        assert len(r2["dispatched"]) == 1

    def test_barcode_lock_key(self):
        dispatcher = RecordingDispatcher()
        scheduler = EdgeScheduler(dispatcher=dispatcher)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[_node("A", materials=[MaterialRequirement(barcode="BC-7")])],
        )
        scheduler.submit_workflow(spec)
        snap = scheduler.snapshot()
        (job,) = snap["inflight_jobs"].values()
        assert job["resource_locks"] == ["res:barcode:BC-7"]


class TestSkipQuarantinesMaterials:
    def test_skip_marks_success_but_quarantines(self):
        """异常后人工 skip：节点成功推进，其已消费物料隔离待复核。"""
        svc = InventoryService(InventoryStore(":memory:"))
        svc.inbound_lot("tpl-w", 100.0, lot_id="lot-1")
        dispatcher = RecordingDispatcher()
        scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=svc)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", materials=[MaterialRequirement(lot_id="lot-1", quantity=20.0)]),
                _node("B", device="dev2"),
            ],
            edges=[_edge("A", "B")],
        )
        scheduler.submit_workflow(spec)
        job_a = dispatcher.dispatched[0]["job_id"]

        r2 = scheduler.on_job_finished(job_a, success=True, suc_type="skip")
        # 节点按成功推进（B 继续下发），workflow 不失败
        assert [d["node_id"] for d in r2["dispatched"]] == ["B"]
        # 但 A 已消费的物料转 quarantined（人工复核）
        assert svc.store.get_reservation("wf1", "A", 1)["status"] == "quarantined"

    def test_normal_success_untouched(self):
        svc = InventoryService(InventoryStore(":memory:"))
        svc.inbound_lot("tpl-w", 100.0, lot_id="lot-1")
        dispatcher = RecordingDispatcher()
        scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=svc)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[_node("A", materials=[MaterialRequirement(lot_id="lot-1", quantity=20.0)])],
        )
        scheduler.submit_workflow(spec)
        scheduler.on_job_finished(dispatcher.dispatched[0]["job_id"], success=True)
        assert svc.store.get_reservation("wf1", "A", 1)["status"] == "consumed"


class _FakeHost:
    """最小 host：send_goal 按预设 suc_type 立即回报。"""

    def __init__(self, suc_type="normal"):
        self.suc_type = suc_type
        self.backend: Optional[JobExecutionBackend] = None
        self.devices_instances: Dict[str, Any] = {}
        self.pending_error_decisions: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []

    def send_goal(self, item: QueueItem, action_type, action_kwargs,
                  sample_material, server_info=None):
        assert self.backend is not None
        assert item.error_decision_target == ERROR_DECISION_TARGET_MICRO_BACKEND
        self.backend.publish_job_status(
            {}, item, "success",
            serialize_result_info("", True, {"v": 1}, suc_type=self.suc_type),
        )

    def get_pending_action_error_decisions(self, decision_target=None):
        return [dict(report) for report in self.pending_error_decisions]

    def handle_action_error_decision(
        self, decision_id, job_id, decision, *, decision_target=None
    ):
        report = next(
            (
                item
                for item in self.pending_error_decisions
                if item.get("decision_id") == decision_id
            ),
            None,
        )
        if report is None:
            return False
        self.pending_error_decisions.remove(report)
        self.decisions.append(dict(decision))
        return True


class TestBackendSucTypePropagation:
    def _run_one(self, listener, suc_type="skip"):
        host = _FakeHost(suc_type=suc_type)
        backend = JobExecutionBackend(host_node_getter=lambda: host)
        host.backend = backend
        backend.start()
        backend.add_job_finished_listener(listener)
        backend.dispatch({
            "job_id": "job-1", "task_id": "t", "device_id": "dev1",
            "action": "run", "action_type": "goal", "action_args": {},
        })
        assert backend.wait_idle(timeout=5)
        backend.stop()

    def test_four_arg_listener_gets_suc_type(self):
        received: List[tuple] = []
        self._run_one(lambda job_id, success, ret, suc_type: received.append(
            (job_id, success, ret, suc_type)))
        assert received == [("job-1", True, {"v": 1}, "skip")]

    def test_three_arg_listener_still_works(self):
        received: List[tuple] = []

        def legacy(job_id, success, ret):
            received.append((job_id, success, ret))

        self._run_one(legacy, suc_type="normal")
        assert received == [("job-1", True, {"v": 1})]


class _Wrapper:
    def __init__(self, node):
        self._ros_node = node


class TestLocalErrorDecisionChannel:
    def _make(self):
        host = _FakeHost()
        backend = JobExecutionBackend(host_node_getter=lambda: host)
        host.backend = backend
        return backend, host

    def _report(self, decision_id="d-1"):
        return {
            "decision_id": decision_id,
            "device_id": "dev1",
            "action_name": "move",
            "job_id": "job-9",
            "exception_type": "CommunicationError",
            "error_message": "port closed",
            "options": [{"action": "retry", "label": "重试"},
                        {"action": "skip", "label": "跳过"}],
        }

    def test_store_list_resolve(self):
        backend, host = self._make()
        host.pending_error_decisions.append(self._report())
        decisions = backend.list_error_decisions()
        assert len(decisions) == 1
        assert decisions[0]["decision_id"] == "d-1"

        assert backend.resolve_error_decision("d-1", {"action": "retry"}) is True
        assert host.decisions[0]["action"] == "retry"
        assert backend.list_error_decisions() == []

    def test_resolve_unknown_decision(self):
        backend, _ = self._make()
        assert backend.resolve_error_decision("nope", {"action": "skip"}) is False

    def test_host_gone_has_no_report(self):
        """Host 不可用时微后端不能伪造设备侧 pending。"""
        backend = JobExecutionBackend(host_node_getter=lambda: None)
        assert backend.resolve_error_decision("d-1", {"action": "retry"}) is False
        assert backend.list_error_decisions() == []


class TestErrorDecisionRest:
    def test_rest_roundtrip(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from unilabos.app.scheduler.api import create_scheduler_router

        backend, host = TestLocalErrorDecisionChannel()._make()
        host.pending_error_decisions.append(
            TestLocalErrorDecisionChannel()._report("d-rest")
        )

        app = FastAPI()
        app.include_router(
            create_scheduler_router(lambda: None, get_backend=lambda: backend))
        client = TestClient(app)

        resp = client.get("/api/v1/error-decisions")
        assert resp.status_code == 200
        assert resp.json()["decisions"][0]["decision_id"] == "d-rest"

        resp = client.post("/api/v1/error-decisions/d-rest", json={"action": "skip"})
        assert resp.status_code == 200
        assert host.decisions[0]["action"] == "skip"

        resp = client.post("/api/v1/error-decisions/d-rest", json={"action": "skip"})
        assert resp.status_code == 404

    def test_backend_absent_503(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from unilabos.app.scheduler.api import create_scheduler_router

        app = FastAPI()
        app.include_router(create_scheduler_router(lambda: None))
        client = TestClient(app)
        assert client.get("/api/v1/error-decisions").status_code == 503


class TestLockResourceResolverFactory:
    def test_host_replica_covers_slave_devices(self):
        """Host._action_value_mappings 是权威副本：本地设备装配时写入，
        slave 设备经 registry_config 上报写入 —— 两类设备同一查找路径。"""

        class _Host:
            # dev-slave 不在 devices_instances（跑在 slave 机器上），
            # 但注册表副本里有它的 action mappings
            _action_value_mappings = {
                "dev-slave": {
                    "run": {"lock_resource": ["plate", "tips"]},
                    "auto-move": {"lock_resource": ["arm"]},
                },
            }
            devices_instances: Dict[str, Any] = {}

        resolver = make_device_lock_resource_resolver(lambda: _Host())
        assert resolver("dev-slave", "run") == ["plate", "tips"]
        assert resolver("dev-slave", "move") == ["arm"]  # auto- 前缀回退
        assert resolver("dev-slave", "unknown") == []
        assert resolver("dev-none", "run") == []         # 设备不存在

    def test_local_instance_fallback(self):
        """Host 副本尚未写入时回退本地设备实例的 mappings。"""

        class _Node:
            _action_value_mappings = {"run": {"lock_resource": ["rack"]}}

        class _Host:
            _action_value_mappings: Dict[str, Any] = {}
            devices_instances = {"dev-local": _Wrapper(_Node())}

        resolver = make_device_lock_resource_resolver(lambda: _Host())
        assert resolver("dev-local", "run") == ["rack"]

    def test_host_replica_wins_over_instance(self):
        class _Node:
            _action_value_mappings = {"run": {"lock_resource": ["stale"]}}

        class _Host:
            _action_value_mappings = {
                "dev1": {"run": {"lock_resource": ["fresh"]}},
            }
            devices_instances = {"dev1": _Wrapper(_Node())}

        resolver = make_device_lock_resource_resolver(lambda: _Host())
        assert resolver("dev1", "run") == ["fresh"]
