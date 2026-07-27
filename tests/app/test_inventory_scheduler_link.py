"""EdgeScheduler × InventoryService 原子衔接测试.

覆盖测试门槛：
- submit 预留不足 → waiting_for_material，不进入执行队列；补料后自动恢复
- 节点开始（下发前）预留转消费；节点失败已用物料 quarantined、
  未消费预留在工作流终态时 release
- cancel/restart 依据 DB reservation 状态恢复，不依赖内存
- 旧 workflow 无物料字段：不产生任何 inventory 调用，行为完全不变
"""

import json

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.inventory.domain import MaterialRequirement
from unilabos.app.scheduler.inventory.service import InventoryService
from unilabos.app.scheduler.inventory.store import InventoryStore
from unilabos.app.scheduler.models import (
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
    WorkflowState,
    node_from_dict,
    spec_from_dict,
)
from unilabos.app.scheduler.service import EdgeScheduler


def _node(node_id, device="dev1", action="run", materials=None):
    return WorkflowNode(
        id=node_id, device_id=device, action_name=action, action_type="goal",
        param={}, material_requirements=materials or [],
    )


def _edge(src, dst):
    return WorkflowEdge(uuid=f"{src}->{dst}", source_node_id=src, target_node_id=dst)


def _req(lot="", qty=0.0, instance=""):
    return MaterialRequirement(lot_id=lot, quantity=qty, instance_uuid=instance)


def _stack(stock=100.0):
    svc = InventoryService(InventoryStore(":memory:"))
    if stock > 0:
        svc.inbound_lot("tpl-w", stock, lot_id="lot-1")
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, inventory=svc)
    return scheduler, dispatcher, svc


class TestSubmitReserve:
    def test_submit_reserves_whole_dag(self):
        scheduler, dispatcher, svc = _stack(stock=100.0)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", materials=[_req(lot="lot-1", qty=20.0)]),
                _node("B", device="dev2", materials=[_req(lot="lot-1", qty=30.0)]),
            ],
            edges=[_edge("A", "B")],
        )
        result = scheduler.submit_workflow(spec)
        assert result["state"] == "running"
        lot = svc.store.get_lot("lot-1")
        # 整 DAG（A+B）在入队时一次性预留
        assert lot["quantity_reserved"] == 30.0  # B 还没开始，A 已消费
        assert lot["quantity_total"] == 80.0     # A 下发时消费了 20
        assert len(dispatcher.dispatched) == 1   # A 已下发

    def test_insufficient_waits_not_queued(self):
        scheduler, dispatcher, svc = _stack(stock=10.0)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[_node("A", materials=[_req(lot="lot-1", qty=50.0)])],
        )
        result = scheduler.submit_workflow(spec)
        assert result["state"] == "waiting_for_material"
        assert dispatcher.dispatched == []  # 不进入执行队列
        assert svc.store.get_lot("lot-1")["quantity_reserved"] == 0.0

    def test_resumes_after_inbound(self):
        """补料后下一次重排自动恢复 RUNNING 并下发."""
        scheduler, dispatcher, svc = _stack(stock=10.0)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[_node("A", materials=[_req(lot="lot-1", qty=50.0)])],
        )
        scheduler.submit_workflow(spec)
        svc.inbound_lot("tpl-w", 100.0, lot_id="lot-1")  # 补料
        scheduler.reschedule()  # 任何触发点都会重试预留
        snap = scheduler.workflow_snapshot("wf1")
        assert snap["state"] == "running"
        assert len(dispatcher.dispatched) == 1

    def test_no_material_workflow_untouched(self):
        """旧 workflow（无物料字段）：不产生任何 inventory 调用."""

        class ExplodingInventory:
            def __getattr__(self, name):
                raise AssertionError(f"inventory.{name} must not be called")

        scheduler = EdgeScheduler(
            dispatcher=RecordingDispatcher(), inventory=ExplodingInventory()
        )
        spec = WorkflowSpec(workflow_id="wf-old", nodes=[_node("A"), _node("B")],
                            edges=[_edge("A", "B")])
        result = scheduler.submit_workflow(spec)
        assert result["state"] == "running"
        assert len(result["dispatched"]) == 1


class TestNodeLifecycle:
    def test_consume_on_dispatch_and_success_flow(self):
        scheduler, dispatcher, svc = _stack(stock=100.0)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", materials=[_req(lot="lot-1", qty=20.0)]),
                _node("B", device="dev2", materials=[_req(lot="lot-1", qty=30.0)]),
            ],
            edges=[_edge("A", "B")],
        )
        scheduler.submit_workflow(spec)
        assert svc.store.get_reservation("wf1", "A", 1)["status"] == "consumed"
        assert svc.store.get_reservation("wf1", "B", 1)["status"] == "active"

        job_a = dispatcher.dispatched[0]["job_id"]
        scheduler.on_job_finished(job_a, success=True, ret_value={"ok": 1})
        # B 下发时消费
        assert svc.store.get_reservation("wf1", "B", 1)["status"] == "consumed"
        job_b = dispatcher.dispatched[1]["job_id"]
        scheduler.on_job_finished(job_b, success=True)

        assert scheduler.workflow_snapshot("wf1")["state"] == "success"
        lot = svc.store.get_lot("lot-1")
        assert lot["quantity_total"] == 50.0
        assert lot["quantity_reserved"] == 0.0

    def test_failure_quarantines_used_releases_rest(self):
        """节点失败：已消费物料 quarantined；未开始节点的预留在终态时 release."""
        scheduler, dispatcher, svc = _stack(stock=100.0)
        svc.register_instance(edge_uuid="mi-1")
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", materials=[_req(lot="lot-1", qty=20.0), _req(instance="mi-1")]),
                _node("B", device="dev2", materials=[_req(lot="lot-1", qty=30.0)]),
            ],
            edges=[_edge("A", "B")],
        )
        scheduler.submit_workflow(spec)
        job_a = dispatcher.dispatched[0]["job_id"]
        scheduler.on_job_finished(job_a, success=False)

        # A 已物理使用 → quarantined（lot 不虚假加回，实例进人工复核）
        assert svc.store.get_reservation("wf1", "A", 1)["status"] == "quarantined"
        assert svc.store.get_instance("mi-1")["status"] == "quarantined"
        # B 未开始 → 预留 release，数量回到 available
        assert svc.store.get_reservation("wf1", "B", 1)["status"] == "released"
        lot = svc.store.get_lot("lot-1")
        assert lot["quantity_total"] == 80.0     # A 消费的 20 不加回
        assert lot["quantity_reserved"] == 0.0
        assert lot["quantity_available"] == 80.0

    def test_cancel_releases_active_reservations(self):
        scheduler, dispatcher, svc = _stack(stock=100.0)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", materials=[_req(lot="lot-1", qty=20.0)]),
                _node("B", device="dev2", materials=[_req(lot="lot-1", qty=30.0)]),
            ],
            edges=[_edge("A", "B")],
        )
        scheduler.submit_workflow(spec)
        scheduler.cancel_workflow("wf1")
        # A 已消费不回滚；B 的 active 预留释放
        assert svc.store.get_reservation("wf1", "B", 1)["status"] == "released"
        lot = svc.store.get_lot("lot-1")
        assert lot["quantity_reserved"] == 0.0
        assert lot["quantity_available"] == 80.0

    def test_restart_recovers_from_db_not_memory(self):
        """restart：换一个全新 scheduler（内存清空），仅凭 DB 状态恢复.

        cancel 后 attempt=1 的 released 预留不会阻碍 attempt=2 重新预留。
        """
        svc = InventoryService(InventoryStore(":memory:"))
        svc.inbound_lot("tpl-w", 100.0, lot_id="lot-1")

        sched1 = EdgeScheduler(dispatcher=RecordingDispatcher(), inventory=svc)
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", materials=[_req(lot="lot-1", qty=20.0)]),
                _node("B", device="dev2", materials=[_req(lot="lot-1", qty=30.0)]),
            ],
            edges=[_edge("A", "B")],
        )
        sched1.submit_workflow(spec)
        sched1.cancel_workflow("wf1")
        # 模拟进程重启：全新 scheduler，凭 DB 重新预留（attempt=2 是新幂等键）
        svc.reserve_workflow(
            "wf1", {"B": [_req(lot="lot-1", qty=30.0)]}, attempt=2
        )
        assert svc.store.get_reservation("wf1", "B", 2)["status"] == "active"
        lot = svc.store.get_lot("lot-1")
        # A(20) 已消费，B attempt=2 预留 30
        assert lot["quantity_total"] == 80.0
        assert lot["quantity_reserved"] == 30.0


class TestSpecSerialization:
    """物料字段向后兼容 schema：解析/序列化."""

    def test_spec_without_materials_parses(self):
        spec = spec_from_dict({
            "workflow_id": "wf1",
            "nodes": [{"id": "A", "device_id": "d1", "action_name": "run"}],
        })
        assert spec.nodes[0].material_requirements == []
        assert spec.material_requirements_by_node() == {}

    def test_spec_with_materials_parses(self):
        node = node_from_dict({
            "id": "A", "device_id": "d1", "action_name": "run",
            "material_requirements": [
                {"lot_id": "lot-1", "quantity": 5, "unit": "mL"},
                {"instance_uuid": "mi-1"},
                {"barcode": "BC-2"},
            ],
        })
        assert len(node.material_requirements) == 3
        assert node.material_requirements[0].quantity == 5.0
        assert node.material_requirements[1].is_instance_requirement()
        assert node.material_requirements[2].barcode == "BC-2"

    def test_requirement_roundtrip(self):
        req = MaterialRequirement(lot_id="lot-1", quantity=3.5, unit="mL")
        assert MaterialRequirement.from_dict(req.to_dict()) == req

    def test_requirement_roundtrip_via_json(self):
        req = MaterialRequirement(template_id="tpl", quantity=2.0, barcode="BC")
        parsed = MaterialRequirement.from_dict(json.loads(json.dumps(req.to_dict())))
        assert parsed == req

    def test_disabled_node_materials_excluded(self):
        spec = WorkflowSpec(
            workflow_id="wf1",
            nodes=[
                _node("A", materials=[_req(lot="lot-1", qty=5.0)]),
                WorkflowNode(id="B", disabled=True,
                             material_requirements=[_req(lot="lot-1", qty=99.0)]),
            ],
        )
        assert list(spec.material_requirements_by_node().keys()) == ["A"]
