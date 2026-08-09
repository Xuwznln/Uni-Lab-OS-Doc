"""T02 schedule_ws OS 面 WS 服务器 hermetic 单测。

覆盖 AC-2（task_dag 下发 / job_status 收敛 / cancel）：
- submit_dag 下发严格 F002 task_dag 报文（action + 逐字段）
- job_status 回流按 (task_id, node_id) 收敛逐节点 NodeState（node_id==job_id）
- 全部节点终态时 RunHandle.done 置位
- on_job_status 回调按序收到每条 job_status data
- cancel_task 下发 F002 cancel 报文，未终态节点标 cancelled
- 任务级幂等：同 task_id 重复 submit 复用句柄、不重复下发
- host_ready 报文置位 session.host_ready

用内存 FakeOS 顶替真实 WS 传输：session 的 send 把报文塞入 FakeOS，
FakeOS 按脚本回 job_status 喂 session.handle_incoming。
沿用 F002 scheduler 测试约定——每例用 asyncio.run 单事件循环驱动，不依赖 pytest-asyncio。
不连真实设备、无 time.sleep。
"""

from __future__ import annotations

import asyncio
from typing import Any

from unilabos.app.local_bridge.schedule_ws import (
    RunHandle,
    ScheduleSession,
    serialize_task_dag,
)
from unilabos.scheduler.dag_model import NodeState, TaskDag


def _two_node_dag(task_id: str = "t1") -> TaskDag:
    """构造 a→b 两节点合法 DAG。"""
    return TaskDag.from_message(
        {
            "task_id": task_id,
            "nodes": [
                {"node_id": "a", "device_id": "pump", "action": "add", "action_args": {"v": 5}},
                {"node_id": "b", "device_id": "stir", "action": "stir"},
            ],
            "edges": [{"source_node_uuid": "a", "target_node_uuid": "b"}],
        }
    )


class FakeOS:
    """内存版 OS 连接：收桥下发的报文，按需回 job_status 喂回 session。"""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self._session: ScheduleSession | None = None

    def bind(self, session: ScheduleSession) -> None:
        self._session = session

    async def send(self, msg: dict[str, Any]) -> None:
        self.received.append(msg)

    async def emit_job_status(
        self, task_id: str, job_id: str, status: str, **extra: Any
    ) -> None:
        """模拟 OS publish_job_status：以 F002 job_status 报文喂回 session。"""
        assert self._session is not None
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
        await self._session.handle_incoming({"action": "job_status", "data": data})


def _make_session() -> tuple[ScheduleSession, FakeOS]:
    fake = FakeOS()
    session = ScheduleSession(fake.send, session_id="sess-1")
    fake.bind(session)
    return session, fake


def test_submit_dag_sends_f002_task_dag() -> None:
    """submit_dag 下发的报文 action=task_dag 且 data 逐字段为 F002。"""

    async def scenario() -> RunHandle:
        session, fake = _make_session()
        handle = await session.submit_dag(_two_node_dag())
        assert len(fake.received) == 1
        msg = fake.received[0]
        assert msg["action"] == "task_dag"
        payload = msg["data"]
        assert payload["task_id"] == "t1"
        assert {n["node_id"] for n in payload["nodes"]} == {"a", "b"}
        node_a = next(n for n in payload["nodes"] if n["node_id"] == "a")
        assert set(node_a) == {
            "node_id",
            "device_id",
            "action",
            "action_type",
            "action_args",
            "sample_material",
            "always_free",
        }
        assert node_a["device_id"] == "pump"
        assert node_a["action_args"] == {"v": 5}
        assert payload["edges"] == [{"source_node_uuid": "a", "target_node_uuid": "b"}]
        return handle

    handle = asyncio.run(scenario())
    assert isinstance(handle, RunHandle)
    assert handle.node_states == {"a": NodeState.PENDING, "b": NodeState.PENDING}


def test_job_status_converges_per_node() -> None:
    """job_status 回流按 (task_id, node_id) 收敛逐节点态，全终态置 done。"""

    async def scenario() -> None:
        session, fake = _make_session()
        handle = await session.submit_dag(_two_node_dag())

        await fake.emit_job_status("t1", "a", "running")
        assert session.node_state("t1", "a") == NodeState.RUNNING
        assert not handle.finished

        await fake.emit_job_status("t1", "a", "success")
        assert handle.node_states["a"] == NodeState.SUCCESS
        assert not handle.finished  # b 尚未终态

        await fake.emit_job_status("t1", "b", "running")
        await fake.emit_job_status("t1", "b", "success")
        assert handle.finished
        assert await handle.wait() == {"a": NodeState.SUCCESS, "b": NodeState.SUCCESS}

    asyncio.run(scenario())


def test_failed_status_is_terminal() -> None:
    """failed 是终态，同样能触发全图 done。"""

    async def scenario() -> None:
        session, fake = _make_session()
        handle = await session.submit_dag(_two_node_dag())
        await fake.emit_job_status("t1", "a", "failed")
        await fake.emit_job_status("t1", "b", "failed")
        assert handle.finished
        assert handle.node_states == {"a": NodeState.FAILED, "b": NodeState.FAILED}

    asyncio.run(scenario())


def test_on_job_status_callback_receives_each() -> None:
    """on_job_status 按序收到每条 job_status data 段。"""

    async def scenario() -> list[tuple[str, str]]:
        session, fake = _make_session()
        seen: list[tuple[str, str]] = []
        session.on_job_status(lambda d: seen.append((d["job_id"], d["status"])))
        await session.submit_dag(_two_node_dag())
        await fake.emit_job_status("t1", "a", "running")
        await fake.emit_job_status("t1", "a", "success")
        return seen

    assert asyncio.run(scenario()) == [("a", "running"), ("a", "success")]


def test_cancel_task_sends_f002_and_marks_cancelled() -> None:
    """cancel_task 下发 F002 cancel 报文；未终态节点标 cancelled 并 done。"""

    async def scenario() -> None:
        session, fake = _make_session()
        handle = await session.submit_dag(_two_node_dag())
        await fake.emit_job_status("t1", "a", "success")  # a 已成功

        await session.cancel_task("t1")

        cancel_msg = fake.received[-1]
        assert cancel_msg == {"action": "cancel_task", "data": {"task_id": "t1"}}
        assert handle.node_states["a"] == NodeState.SUCCESS
        assert handle.node_states["b"] == NodeState.CANCELLED
        assert handle.finished

    asyncio.run(scenario())


def test_duplicate_submit_is_idempotent() -> None:
    """同 task_id 重复 submit 复用句柄、不重复下发（任务级幂等）。"""

    async def scenario() -> None:
        session, fake = _make_session()
        dag = _two_node_dag()
        h1 = await session.submit_dag(dag)
        h2 = await session.submit_dag(dag)
        assert h1 is h2
        assert len(fake.received) == 1

    asyncio.run(scenario())


def test_host_ready_sets_event() -> None:
    """host_ready 报文置位 session.host_ready。"""

    async def scenario() -> None:
        session, _ = _make_session()
        assert not session.host_ready.is_set()
        await session.handle_incoming({"action": "host_ready", "data": {}})
        assert session.host_ready.is_set()

    asyncio.run(scenario())


def test_unknown_status_ignored() -> None:
    """未知 status 不改变节点态、不误触发 done。"""

    async def scenario() -> None:
        session, fake = _make_session()
        handle = await session.submit_dag(_two_node_dag())
        await fake.emit_job_status("t1", "a", "queued")  # 非 F002 终态/运行态
        assert session.node_state("t1", "a") == NodeState.PENDING
        assert not handle.finished

    asyncio.run(scenario())


def test_serialize_task_dag_roundtrip() -> None:
    """serialize_task_dag 的产物能被 TaskDag.from_message 原样重建（往返一致）。"""
    dag = _two_node_dag()
    payload = serialize_task_dag(dag)
    rebuilt = TaskDag.from_message(payload)

    assert set(rebuilt.nodes) == set(dag.nodes)
    assert rebuilt.nodes["a"].device_id == "pump"
    assert rebuilt.nodes["a"].action_args == {"v": 5}
    assert rebuilt.edges[0].source_node_uuid == "a"
    assert rebuilt.edges[0].target_node_uuid == "b"
