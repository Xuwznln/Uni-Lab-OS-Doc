"""EdgeScheduler 触发点与资源锁行为测试。

核心断言：**每个工作流提交、每个子 action 完成，都触发一次重排**。
"""

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import (
    Handle,
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
)
from unilabos.app.scheduler.ordering import OrderingContext, StableLocalOrderer
from unilabos.app.scheduler.service import EdgeScheduler


def _node(node_id: str, device: str = "dev1", action: str = "run") -> WorkflowNode:
    return WorkflowNode(
        id=node_id, device_id=device, action_name=action, action_type="goal", param={}
    )


def _edge(src: str, dst: str, sh: str = "", th: str = "") -> WorkflowEdge:
    return WorkflowEdge(
        uuid=f"{src}->{dst}",
        source_node_id=src,
        target_node_id=dst,
        source_handle_uuid=sh,
        target_handle_uuid=th,
    )


def _chain_spec(workflow_id: str, device: str = "dev1", priority=1.0) -> WorkflowSpec:
    """A → B 两节点链。"""
    return WorkflowSpec(
        workflow_id=workflow_id,
        nodes=[_node("A", device), _node("B", device)],
        edges=[_edge("A", "B")],
        priority=priority,
    )


def _make() -> "tuple[EdgeScheduler, RecordingDispatcher]":
    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher)
    return scheduler, dispatcher


class TestTriggerOnSubmit:
    def test_submit_dispatches_ready_immediately(self):
        scheduler, dispatcher = _make()
        result = scheduler.submit_workflow(_chain_spec("wf1"))
        # 触发点 1：提交即重排，根节点 A 立即下发
        assert len(result["dispatched"]) == 1
        assert result["dispatched"][0]["node_id"] == "A"
        assert len(dispatcher.dispatched) == 1
        assert dispatcher.dispatched[0]["action"] == "run"

    def test_each_submit_triggers_reschedule(self):
        scheduler, _ = _make()
        scheduler.submit_workflow(_chain_spec("wf1", device="d1"))
        scheduler.submit_workflow(_chain_spec("wf2", device="d2"))
        assert scheduler.snapshot()["reschedule_count"] == 2

    def test_duplicate_submit_rejected(self):
        scheduler, _ = _make()
        scheduler.submit_workflow(_chain_spec("wf1"))
        try:
            scheduler.submit_workflow(_chain_spec("wf1"))
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestTriggerOnJobFinish:
    def test_finish_dispatches_next(self):
        scheduler, dispatcher = _make()
        result = scheduler.submit_workflow(_chain_spec("wf1"))
        job_id = result["dispatched"][0]["job_id"]

        # 触发点 2：A 完成 → 重排 → B 下发
        result2 = scheduler.on_job_finished(job_id, success=True, ret_value={})
        assert [d["node_id"] for d in result2["dispatched"]] == ["B"]
        assert len(dispatcher.dispatched) == 2
        assert scheduler.snapshot()["reschedule_count"] == 2

    def test_finish_last_node_completes_workflow(self):
        scheduler, _ = _make()
        r1 = scheduler.submit_workflow(_chain_spec("wf1"))
        r2 = scheduler.on_job_finished(r1["dispatched"][0]["job_id"], True)
        r3 = scheduler.on_job_finished(r2["dispatched"][0]["job_id"], True)
        assert r3["workflow_state"] == "success"
        assert r3["dispatched"] == []

    def test_failure_stops_workflow(self):
        scheduler, _ = _make()
        r1 = scheduler.submit_workflow(_chain_spec("wf1"))
        r2 = scheduler.on_job_finished(r1["dispatched"][0]["job_id"], success=False)
        assert r2["workflow_state"] == "failed"
        assert r2["dispatched"] == []

    def test_unknown_job_ignored(self):
        scheduler, _ = _make()
        assert scheduler.on_job_finished("nope", True)["dispatched"] == []


class TestResourceLock:
    def test_same_device_action_serialized(self):
        """两个工作流抢同一 device+action：后者等前者完成的那次重排。"""
        scheduler, dispatcher = _make()
        r1 = scheduler.submit_workflow(_chain_spec("wf1", device="shared"))
        r2 = scheduler.submit_workflow(_chain_spec("wf2", device="shared"))
        # wf2 的 A 因锁忙未下发
        assert r2["dispatched"] == []
        assert len(dispatcher.dispatched) == 1

        # wf1.A 完成 → 释放锁 → 这次重排同时下发 wf1.B(等待) 或 wf2.A（顺序由排序器决定）
        r3 = scheduler.on_job_finished(r1["dispatched"][0]["job_id"], True)
        # 同一设备只能有一个在跑
        assert len(r3["dispatched"]) == 1

    def test_different_devices_parallel(self):
        scheduler, dispatcher = _make()
        scheduler.submit_workflow(_chain_spec("wf1", device="d1"))
        scheduler.submit_workflow(_chain_spec("wf2", device="d2"))
        assert len(dispatcher.dispatched) == 2

    def test_external_busy_key_blocks(self):
        busy = {"/devices/dev1/run"}
        dispatcher = RecordingDispatcher()
        scheduler = EdgeScheduler(dispatcher=dispatcher, external_busy_keys=busy)
        r = scheduler.submit_workflow(_chain_spec("wf1", device="dev1"))
        assert r["dispatched"] == []
        # 外部锁释放后，手动/下次触发重排即可下发
        busy.clear()
        assert [d["node_id"] for d in scheduler.reschedule()] == ["A"]


class TestPriorityOrdering:
    def test_high_priority_first_on_contention(self):
        """同设备争抢时，高优先级工作流先拿到锁。"""
        dispatcher = RecordingDispatcher()
        scheduler = EdgeScheduler(dispatcher=dispatcher, external_busy_keys={"/devices/s/run"})
        scheduler.submit_workflow(_chain_spec("wf-low", device="s", priority="low"))
        scheduler.submit_workflow(_chain_spec("wf-high", device="s", priority="high"))
        # 解锁后重排：high 先下发
        scheduler._external_busy_keys.clear()
        dispatched = scheduler.reschedule()
        assert dispatched[0]["workflow_id"] == "wf-high"

    def test_stable_orderer_key(self):
        orderer = StableLocalOrderer()
        from unilabos.app.scheduler.models import ReadyTask

        t1 = ReadyTask("wf1", _node("A"), priority_weight=50.0, submitted_at=1.0)
        t2 = ReadyTask("wf2", _node("A"), priority_weight=200.0, submitted_at=2.0)
        t3 = ReadyTask("wf3", _node("A"), priority_weight=200.0, submitted_at=1.5)
        ordered = orderer.order([t1, t2, t3], OrderingContext(set()))
        assert [t.workflow_id for t in ordered] == ["wf3", "wf2", "wf1"]


class TestParamFlow:
    def test_ret_value_passed_via_handles(self):
        """A 的返回值经 handle 传参写入 B 的 action_args。"""
        sh = Handle(uuid="sh", data_source="executor", handle_key="out", data_key="volume")
        th = Handle(uuid="th", data_source="handle", handle_key="in", data_key="target_volume")
        spec = WorkflowSpec(
            workflow_id="wf-param",
            nodes=[
                _node("A", device="d1"),
                WorkflowNode(
                    id="B",
                    device_id="d2",
                    action_name="run",
                    action_type="goal",
                    param={"target_volume": 0},
                ),
            ],
            edges=[_edge("A", "B", sh="sh", th="th")],
            handles=[sh, th],
        )
        scheduler, dispatcher = _make()
        r1 = scheduler.submit_workflow(spec)
        scheduler.on_job_finished(r1["dispatched"][0]["job_id"], True, ret_value={"volume": 42})
        assert dispatcher.dispatched[-1]["node_id"] == "B"
        assert dispatcher.dispatched[-1]["action_args"] == {"target_volume": 42}

    def test_param_resolve_failure_fails_node(self):
        sh = Handle(uuid="sh", data_source="executor", handle_key="out", data_key="missing")
        th = Handle(uuid="th", data_source="handle", handle_key="in", data_key="k")
        spec = WorkflowSpec(
            workflow_id="wf-bad",
            nodes=[_node("A", device="d1"), _node("B", device="d2")],
            edges=[_edge("A", "B", sh="sh", th="th")],
            handles=[sh, th],
        )
        scheduler, _ = _make()
        r1 = scheduler.submit_workflow(spec)
        r2 = scheduler.on_job_finished(r1["dispatched"][0]["job_id"], True, ret_value={"other": 1})
        assert r2["dispatched"] == []
        assert r2["workflow_state"] == "failed"


class TestCancel:
    def test_cancel_stops_dispatch(self):
        scheduler, dispatcher = _make()
        r1 = scheduler.submit_workflow(_chain_spec("wf1"))
        assert scheduler.cancel_workflow("wf1") is True
        # 完成回调后不再推进
        scheduler.on_job_finished(r1["dispatched"][0]["job_id"], True)
        assert len(dispatcher.dispatched) == 1


class TestManualConfirmNodes:
    """manual_confirm 特殊节点：不进执行器、不占设备锁，靠 finish_job 人工放行。"""

    def _manual_spec(self, workflow_id: str) -> WorkflowSpec:
        manual = WorkflowNode(
            id="M",
            device_id="operator",
            action_name="confirm",
            action_type="goal",
            param={"prompt": "确认无误后继续"},
            node_type="manual_confirm",
        )
        return WorkflowSpec(
            workflow_id=workflow_id,
            nodes=[_node("A"), manual, _node("B")],
            edges=[_edge("A", "M"), _edge("M", "B")],
        )

    def test_manual_confirm_parks_without_dispatch(self):
        scheduler, dispatcher = _make()
        scheduler.submit_workflow(self._manual_spec("wf-manual"))
        job_a = dispatcher.dispatched[0]["job_id"]
        scheduler.on_job_finished(job_a, True, {}, "normal")
        # M 已进入 dispatched 停驻，但执行器只收到过 A
        assert len(dispatcher.dispatched) == 1
        snap = scheduler.workflow_snapshot("wf-manual")
        assert snap["nodes"]["M"]["state"] == "dispatched"
        # 快照必须带 job_id，前端凭它调 /jobs/{id}/finish
        manual_job = snap["nodes"]["M"].get("job_id")
        assert manual_job

    def test_manual_confirm_finish_releases_downstream(self):
        scheduler, dispatcher = _make()
        scheduler.submit_workflow(self._manual_spec("wf-manual2"))
        scheduler.on_job_finished(dispatcher.dispatched[0]["job_id"], True, {}, "normal")
        manual_job = scheduler.workflow_snapshot("wf-manual2")["nodes"]["M"]["job_id"]
        scheduler.on_job_finished(manual_job, True, {"confirmed": True}, "normal")
        # 人工放行后 B 正常下发，链路走完工作流成功
        assert dispatcher.dispatched[-1]["action"] == "run"
        scheduler.on_job_finished(dispatcher.dispatched[-1]["job_id"], True, {}, "normal")
        assert scheduler.workflow_snapshot("wf-manual2")["state"] == "success"

    def test_manual_confirm_ignores_device_busy(self):
        scheduler, dispatcher = _make()
        # 同 key 的设备 job 占着锁
        scheduler.submit_workflow(
            WorkflowSpec(
                workflow_id="wf-busy",
                nodes=[_node("A", device="operator", action="confirm")],
            )
        )
        # manual_confirm 与其同 key，但 always-free：照样立即停驻下发
        manual_only = WorkflowSpec(
            workflow_id="wf-manual3",
            nodes=[
                WorkflowNode(
                    id="M",
                    device_id="operator",
                    action_name="confirm",
                    node_type="manual_confirm",
                )
            ],
        )
        scheduler.submit_workflow(manual_only)
        snap = scheduler.workflow_snapshot("wf-manual3")
        assert snap["nodes"]["M"]["state"] == "dispatched"
