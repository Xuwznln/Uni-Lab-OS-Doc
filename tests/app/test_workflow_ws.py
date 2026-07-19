"""T03 workflow_ws 实现 A（云端 panel）UI 面 WS 服务器 hermetic 单测。

覆盖 AC-3（WorkflowWSActionType ↔ task_dag/workflow_update 翻译，下行报文形状匹配
WorkflowDAGPanel.onMessageCallback 契约）：
- fetch_graph → 下行 {code:0, data:{action:'fetch_graph', data:{nodes,edges}}}，
  节点带 uuid/pose.position（供 handleNodesToWorkflowReactFlow 渲染）
- run_workflow → demo 图经 workflow_to_dag 构 TaskDag 交 schedule_ws 下发（OS 面收 F002
  task_dag），并回 {code:0, data:{action:'run_workflow', data:<task_id>}}
- job_status 回流 → 翻译成 {code:0, data:{action:'workflow_update', code:0,
  data:{node_uuid, job_status, task_status, header, msg}}}；node_uuid==job_id
- task_status 仅在整张 DAG 全终态时为 'end'，否则 'running'
- stop_workflow → schedule_ws.cancel_task，并回 {code:0, data:{action:'stop_workflow'}}
- 非本会话 task_id 的 job_status 不推送

用内存 FakePanel（收桥→panel 下行）+ 内存 OS（收桥→OS 下行、按脚本回 job_status）
顶替真实 WS 传输。沿用 F002 asyncio.run 约定，不依赖 pytest-asyncio，无 time.sleep。
"""

from __future__ import annotations

import asyncio
from typing import Any

from unilabos.app.local_bridge.schedule_ws import ScheduleSession
from unilabos.app.local_bridge.workflow_ws import (
    FETCH_GRAPH,
    RUN_WORKFLOW,
    STOP_WORKFLOW,
    TASK_STATUS_END,
    TASK_STATUS_RUNNING,
    WORKFLOW_UPDATE,
    WorkflowSession,
    _extract_uuid,
    build_demo_graph,
    translate_job_status_to_update,
)


class FakeTransport:
    """内存版收信端：把发来的报文塞入 received 列表。"""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []

    async def send(self, msg: dict[str, Any]) -> None:
        self.received.append(msg)


def _make_session(uuid: str = "wf1") -> tuple[WorkflowSession, FakeTransport, ScheduleSession, FakeTransport]:
    """接线：schedule(→OS 面 os_side) + workflow(→panel 面 panel_side)，共享 schedule。"""
    os_side = FakeTransport()
    schedule = ScheduleSession(os_side.send)
    panel_side = FakeTransport()
    workflow = WorkflowSession(panel_side.send, schedule, uuid=uuid)
    return workflow, panel_side, schedule, os_side


async def _emit_job_status(
    schedule: ScheduleSession, task_id: str, job_id: str, status: str, **extra: Any
) -> None:
    """模拟 OS publish_job_status：以 F002 job_status 报文喂回 schedule。"""
    data = {
        "job_id": job_id,
        "task_id": task_id,
        "device_id": extra.get("device_id", ""),
        "notebook_id": extra.get("notebook_id", ""),
        "action_name": extra.get("action_name", ""),
        "status": status,
        "feedback_data": extra.get("feedback_data", {}),
        "return_info": extra.get("return_info"),
        "timestamp": extra.get("timestamp", 0.0),
    }
    await schedule.handle_incoming({"action": "job_status", "data": data})


def test_fetch_graph_returns_render_ready_graph() -> None:
    async def scenario() -> None:
        workflow, panel_side, _schedule, _os = _make_session()
        await workflow.handle_incoming({"action": FETCH_GRAPH, "msg_uuid": "m1"})
        assert len(panel_side.received) == 1
        msg = panel_side.received[0]
        assert msg["code"] == 0
        assert msg["data"]["action"] == FETCH_GRAPH
        graph = msg["data"]["data"]
        assert {n["uuid"] for n in graph["nodes"]} == {"n1", "n2"}
        # 渲染必需字段：pose.position（handleNodesToWorkflowReactFlow 读）
        for node in graph["nodes"]:
            assert "x" in node["pose"]["position"]
            assert "y" in node["pose"]["position"]
        assert graph["edges"][0]["source_node_uuid"] == "n1"
        assert graph["edges"][0]["target_node_uuid"] == "n2"

    asyncio.run(scenario())


def test_run_workflow_submits_task_dag_and_acks() -> None:
    async def scenario() -> None:
        workflow, panel_side, _schedule, os_side = _make_session(uuid="wf1")
        await workflow.handle_incoming({"action": RUN_WORKFLOW, "msg_uuid": "m1"})
        # OS 面收到 F002 task_dag（逐字段）
        assert len(os_side.received) == 1
        dag_msg = os_side.received[0]
        assert dag_msg["action"] == "task_dag"
        payload = dag_msg["data"]
        # 每次运行铸唯一 task_id（uuid + 递增序号）——首次为 wf1-1
        assert payload["task_id"] == "wf1-1"
        assert {n["node_id"] for n in payload["nodes"]} == {"n1", "n2"}
        assert {n["device_id"] for n in payload["nodes"]} == {"pump_1", "stirrer_1"}
        assert payload["edges"] == [{"source_node_uuid": "n1", "target_node_uuid": "n2"}]
        # panel 面收到 run_workflow ack，data == 铸出的 task_id
        assert panel_side.received[-1] == {
            "code": 0,
            "data": {"action": RUN_WORKFLOW, "data": "wf1-1"},
        }

    asyncio.run(scenario())


def test_rerun_workflow_dispatches_fresh_task() -> None:
    """重跑同一 panel 工作流应真下发（新 task_id），而非命中旧终态句柄静默空操作。"""

    async def scenario() -> None:
        workflow, _panel, schedule, os_side = _make_session(uuid="wf1")
        await workflow.handle_incoming({"action": RUN_WORKFLOW})
        await _emit_job_status(schedule, "wf1-1", "n1", "success")
        await _emit_job_status(schedule, "wf1-1", "n2", "success")
        # 二次运行：必须再下发一条 task_dag，且 task_id 不同（wf1-2）
        await workflow.handle_incoming({"action": RUN_WORKFLOW})
        assert len(os_side.received) == 2
        assert os_side.received[0]["data"]["task_id"] == "wf1-1"
        assert os_side.received[1]["data"]["task_id"] == "wf1-2"

    asyncio.run(scenario())


def test_job_status_translates_to_workflow_update() -> None:
    async def scenario() -> None:
        workflow, panel_side, schedule, _os = _make_session(uuid="wf1")
        await workflow.handle_incoming({"action": RUN_WORKFLOW})
        tid = workflow._task_id  # noqa: SLF001 —— 取本次运行铸出的 task_id
        panel_side.received.clear()
        await _emit_job_status(schedule, tid, "n1", "running", action_name="加液")
        assert len(panel_side.received) == 1
        msg = panel_side.received[0]
        assert msg["code"] == 0
        inner = msg["data"]
        assert inner["action"] == WORKFLOW_UPDATE
        assert inner["code"] == 0
        body = inner["data"]
        assert body["node_uuid"] == "n1"  # node_uuid == job_id
        assert body["job_status"] == "running"
        assert body["task_status"] == TASK_STATUS_RUNNING
        assert body["header"] == "加液"

    asyncio.run(scenario())


def test_task_status_end_only_when_all_terminal() -> None:
    async def scenario() -> None:
        workflow, panel_side, schedule, _os = _make_session(uuid="wf1")
        await workflow.handle_incoming({"action": RUN_WORKFLOW})
        tid = workflow._task_id  # noqa: SLF001
        panel_side.received.clear()
        # 首节点成功——尚有 n2 未终态，task_status 应为 running
        await _emit_job_status(schedule, tid, "n1", "success")
        assert panel_side.received[-1]["data"]["data"]["task_status"] == TASK_STATUS_RUNNING
        # 末节点成功——全终态，task_status 应为 end
        await _emit_job_status(schedule, tid, "n2", "success")
        last = panel_side.received[-1]["data"]["data"]
        assert last["node_uuid"] == "n2"
        assert last["job_status"] == "success"
        assert last["task_status"] == TASK_STATUS_END

    asyncio.run(scenario())


def test_failed_status_reaches_end() -> None:
    async def scenario() -> None:
        workflow, panel_side, schedule, _os = _make_session(uuid="wf1")
        await workflow.handle_incoming({"action": RUN_WORKFLOW})
        tid = workflow._task_id  # noqa: SLF001
        panel_side.received.clear()
        await _emit_job_status(schedule, tid, "n1", "success")
        await _emit_job_status(schedule, tid, "n2", "failed")
        last = panel_side.received[-1]["data"]["data"]
        assert last["job_status"] == "failed"
        assert last["task_status"] == TASK_STATUS_END

    asyncio.run(scenario())


def test_job_status_for_other_task_ignored() -> None:
    async def scenario() -> None:
        workflow, panel_side, schedule, _os = _make_session(uuid="wf1")
        await workflow.handle_incoming({"action": RUN_WORKFLOW})
        panel_side.received.clear()
        # 非本会话 task_id 的回流不应推 panel
        await _emit_job_status(schedule, "other", "n1", "running")
        assert panel_side.received == []

    asyncio.run(scenario())


def test_closed_session_deregisters_callback() -> None:
    """会话 close() 后注销回调：后续回流不再触达该会话（杜绝跨连接累积）。"""

    async def scenario() -> None:
        workflow, panel_side, schedule, _os = _make_session(uuid="wf1")
        await workflow.handle_incoming({"action": RUN_WORKFLOW})
        tid = workflow._task_id  # noqa: SLF001
        panel_side.received.clear()
        workflow.close()
        # 回调已注销，schedule 的回调列表应为空
        assert schedule._job_status_cbs == []  # noqa: SLF001
        # 断开后的回流不应再推此 panel
        await _emit_job_status(schedule, tid, "n1", "running")
        assert panel_side.received == []

    asyncio.run(scenario())


def test_stop_workflow_cancels_task() -> None:
    async def scenario() -> None:
        workflow, panel_side, _schedule, os_side = _make_session(uuid="wf1")
        await workflow.handle_incoming({"action": RUN_WORKFLOW})
        tid = workflow._task_id  # noqa: SLF001
        os_side.received.clear()
        panel_side.received.clear()
        await workflow.handle_incoming({"action": STOP_WORKFLOW, "data": tid})
        # OS 面收到 cancel_task
        assert os_side.received[0]["action"] == "cancel_task"
        assert os_side.received[0]["data"] == {"task_id": tid}
        # panel 面收到 stop_workflow 确认
        assert panel_side.received[-1] == {"code": 0, "data": {"action": STOP_WORKFLOW}}

    asyncio.run(scenario())


def test_translate_job_status_pure_shape() -> None:
    data = {
        "job_id": "n7",
        "task_id": "wf1",
        "status": "running",
        "action_name": "搅拌",
        "return_info": {"ok": True},
    }
    running = translate_job_status_to_update(data, finished=False)
    assert running == {
        "code": 0,
        "data": {
            "action": WORKFLOW_UPDATE,
            "code": 0,
            "data": {
                "node_uuid": "n7",
                "job_status": "running",
                "task_status": TASK_STATUS_RUNNING,
                "header": "搅拌",
                "msg": '{"ok": true}',
            },
        },
    }
    ended = translate_job_status_to_update(data, finished=True)
    assert ended["data"]["data"]["task_status"] == TASK_STATUS_END


def test_build_demo_graph_translates_to_valid_task_dag() -> None:
    from unilabos.app.local_bridge.workflow_to_dag import workflow_to_task_dag

    graph = build_demo_graph()
    dag = workflow_to_task_dag(graph["nodes"], graph["edges"], task_id="wf1")
    assert set(dag.nodes) == {"n1", "n2"}
    assert len(dag.edges) == 1


def test_extract_uuid() -> None:
    assert _extract_uuid("/ws/workflow/abc123") == "abc123"
    assert _extract_uuid("/ws/workflow/abc123?access_token_v2=xxx") == "abc123"
    assert _extract_uuid("/ws/workflow/") == ""
    assert _extract_uuid("/nope") == ""
