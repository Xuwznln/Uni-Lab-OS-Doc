"""JobExecutionBackend（HostNode 微后端）与 EdgeScheduler 全链路测试。

FakeHostNode 模拟设备执行：send_goal 后按配置同步回报 publish_job_status，
验证「调度器 → 微后端 → HostNode → 回报 → 调度器重排」闭环。
"""

import threading
import time
from typing import Any, Dict, List, Optional

from unilabos.app.scheduler.backend import JobExecutionBackend, create_edge_stack
from unilabos.app.scheduler.dispatch import build_job_start_payload
from unilabos.app.scheduler.models import WorkflowEdge, WorkflowNode, WorkflowSpec
from unilabos.app.ws_client import QueueItem
from unilabos.utils.type_check import serialize_result_info


class FakeHostNode:
    """记录 send_goal；auto_complete 时立即回报成功结果。"""

    def __init__(self, backend_ref: Dict[str, Any], auto_complete: bool = True,
                 ret_values: Optional[Dict[str, Any]] = None):
        self.sent_goals: List[QueueItem] = []
        self.backend_ref = backend_ref  # {"backend": JobExecutionBackend}，延迟绑定
        self.auto_complete = auto_complete
        self.ret_values = ret_values or {}
        self.lock = threading.Lock()

    def send_goal(self, item: QueueItem, action_type: str, action_kwargs: Dict[str, Any],
                  sample_material: Dict[str, Any], server_info: Any = None) -> None:
        with self.lock:
            self.sent_goals.append(item)
        if self.auto_complete:
            ret = self.ret_values.get(f"{item.device_id}/{item.action_name}", {"done": True})
            backend = self.backend_ref["backend"]
            backend.publish_job_status(
                {}, item, "success", serialize_result_info("", True, ret)
            )


def _node(node_id: str, device: str = "dev1", action: str = "run") -> WorkflowNode:
    return WorkflowNode(id=node_id, device_id=device, action_name=action,
                        action_type="goal", param={})


def _edge(src: str, dst: str) -> WorkflowEdge:
    return WorkflowEdge(uuid=f"{src}->{dst}", source_node_id=src, target_node_id=dst)


def _make_backend(auto_complete: bool = True):
    ref: Dict[str, Any] = {}
    host = FakeHostNode(ref, auto_complete=auto_complete)
    backend = JobExecutionBackend(host_node_getter=lambda: host)
    ref["backend"] = backend
    backend.start()
    return backend, host


class TestBackendAlone:
    def test_dispatch_sends_goal(self):
        backend, host = _make_backend(auto_complete=False)
        try:
            backend.dispatch(build_job_start_payload(
                job_id="j1", task_id="t1", workflow_id="wf", node_id="A",
                device_id="d1", action_name="run", action_type="goal", action_args={"x": 1},
            ))
            assert backend.wait_idle()
            assert [g.job_id for g in host.sent_goals] == ["j1"]
            assert backend.busy_device_action_keys() == {"/devices/d1/run"}
        finally:
            backend.stop()

    def test_same_device_queued_then_started_after_finish(self):
        backend, host = _make_backend(auto_complete=False)
        try:
            for jid in ("j1", "j2"):
                backend.dispatch(build_job_start_payload(
                    job_id=jid, task_id="t", workflow_id="wf", node_id=jid,
                    device_id="d1", action_name="run", action_type="goal", action_args={},
                ))
            assert backend.wait_idle()
            # j2 排队未执行
            assert [g.job_id for g in host.sent_goals] == ["j1"]

            # j1 完成回报 → j2 自动出队执行
            backend.publish_job_status({}, host.sent_goals[0], "success",
                                       serialize_result_info("", True, {}))
            assert backend.wait_idle()
            assert [g.job_id for g in host.sent_goals] == ["j1", "j2"]
        finally:
            backend.stop()

    def test_listener_receives_ret_value(self):
        backend, host = _make_backend(auto_complete=False)
        received: List[tuple] = []
        backend.add_job_finished_listener(lambda *args: received.append(args))
        try:
            backend.dispatch(build_job_start_payload(
                job_id="j1", task_id="t", workflow_id="wf", node_id="A",
                device_id="d1", action_name="run", action_type="goal", action_args={},
            ))
            assert backend.wait_idle()
            backend.publish_job_status({}, host.sent_goals[0], "success",
                                       serialize_result_info("", True, {"volume": 7}))
            assert backend.wait_idle()
            # 第 4 参 suc_type：normal / skip / operator_intervention（异常决策来源）
            assert received == [("j1", True, {"volume": 7}, "normal")]
        finally:
            backend.stop()

    def test_foreign_job_status_ignored(self):
        backend, _ = _make_backend(auto_complete=False)
        received: List[tuple] = []
        backend.add_job_finished_listener(lambda *args: received.append(args))
        try:
            item = QueueItem(task_type="job_call_back_status", device_id="d", action_name="a",
                             task_id="t", job_id="ghost", notebook_id="",
                             device_action_key="/devices/d/a")
            backend.publish_job_status({}, item, "success", serialize_result_info("", True, {}))
            assert backend.wait_idle()
            assert received == []
        finally:
            backend.stop()

    def test_running_status_does_not_finish(self):
        backend, host = _make_backend(auto_complete=False)
        received: List[tuple] = []
        backend.add_job_finished_listener(lambda *args: received.append(args))
        try:
            backend.dispatch(build_job_start_payload(
                job_id="j1", task_id="t", workflow_id="wf", node_id="A",
                device_id="d1", action_name="run", action_type="goal", action_args={},
            ))
            assert backend.wait_idle()
            backend.publish_job_status({"pct": 50}, host.sent_goals[0], "running", None)
            assert backend.wait_idle()
            assert received == []
            assert backend.busy_device_action_keys() == {"/devices/d1/run"}
        finally:
            backend.stop()


class TestEdgeStackEndToEnd:
    def _wait(self, predicate, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_full_workflow_auto_completes(self):
        """submit → 微后端执行 → 回报 → 重排推进，直到工作流 success。"""
        ref: Dict[str, Any] = {}
        host = FakeHostNode(ref, auto_complete=True)
        scheduler, backend = create_edge_stack(host_node_getter=lambda: host)
        ref["backend"] = backend
        try:
            spec = WorkflowSpec(
                workflow_id="wf-e2e",
                nodes=[_node("A"), _node("B"), _node("C", device="dev2")],
                edges=[_edge("A", "B"), _edge("A", "C")],
            )
            scheduler.submit_workflow(spec)
            assert self._wait(
                lambda: (scheduler.workflow_snapshot("wf-e2e") or {}).get("state") == "success"
            ), scheduler.snapshot()
            assert len(host.sent_goals) == 3
        finally:
            backend.stop()

    def test_two_workflows_same_device_serialized(self):
        ref: Dict[str, Any] = {}
        host = FakeHostNode(ref, auto_complete=True)
        scheduler, backend = create_edge_stack(host_node_getter=lambda: host)
        ref["backend"] = backend
        try:
            for wid in ("wf1", "wf2"):
                scheduler.submit_workflow(WorkflowSpec(
                    workflow_id=wid,
                    nodes=[_node("A", device="shared"), _node("B", device="shared")],
                    edges=[_edge("A", "B")],
                ))
            ok = self._wait(lambda: all(
                (scheduler.workflow_snapshot(w) or {}).get("state") == "success"
                for w in ("wf1", "wf2")
            ))
            assert ok, scheduler.snapshot()
            assert len(host.sent_goals) == 4
        finally:
            backend.stop()
