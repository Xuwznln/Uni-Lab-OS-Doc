"""T05 offline_os 离线执行核 + server 组合入口 hermetic 单测。

覆盖 AC-6（离线自足档：无真实 OS 亦可用 F002 DagExecutor 走同一 TaskDag、回发 job_status）：
- 整张 DAG 跑通 → 逐节点 SUCCESS 回流、RunHandle 收敛。
- 编程失败 → fail-fast，下游 CANCELLED，且补发 cancelled 令桥收敛（无悬挂）。
- 同 device_action_key 非 always_free 节点经每设备锁串行（I3，峰值并发==1）。
- cancel_task → 全节点 CANCELLED。
- build_offline_session 装配正确；LocalBridgeServer 离线/真实模式 state 就绪时机。

沿用 F002/F003 的 asyncio.run(scenario()) 约定（不引 pytest-asyncio）；无 time.sleep、无网络。
"""

from __future__ import annotations

import asyncio
from typing import Any

from unilabos.app.local_bridge.offline_os import OfflineOS
from unilabos.app.local_bridge.schedule_ws import ScheduleSession
from unilabos.app.local_bridge.server import LocalBridgeServer, build_offline_session
from unilabos.app.local_bridge.workflow_to_dag import workflow_to_task_dag
from unilabos.scheduler.dag_model import NodeState, TaskDag


def _chain_dag(task_id: str, n: int = 3) -> TaskDag:
    """构 n1→n2→…→nn 线性链（各不同设备，纯偏序）。"""
    nodes = [
        {"node_id": f"n{i}", "device_id": f"dev_{i}", "action": "act", "action_args": {}}
        for i in range(1, n + 1)
    ]
    edges = [
        {"source_node_uuid": f"n{i}", "target_node_uuid": f"n{i + 1}"}
        for i in range(1, n)
    ]
    return workflow_to_task_dag(nodes, edges, task_id=task_id)


def _same_device_parallel_dag(task_id: str) -> TaskDag:
    """构两节点同 device_action_key、无边（同时 ready）——用于 I3 串行断言。"""
    nodes = [
        {"node_id": "a", "device_id": "pump_1", "action": "pump", "action_args": {}},
        {"node_id": "b", "device_id": "pump_1", "action": "pump", "action_args": {}},
    ]
    return workflow_to_task_dag(nodes, [], task_id=task_id)


def test_offline_runs_full_dag_to_success() -> None:
    async def scenario() -> None:
        schedule, offline = build_offline_session()
        dag = _chain_dag("t1", 3)
        handle = await schedule.submit_dag(dag)
        await asyncio.wait_for(handle.wait(), timeout=5)
        assert handle.node_states == {
            "n1": NodeState.SUCCESS,
            "n2": NodeState.SUCCESS,
            "n3": NodeState.SUCCESS,
        }
        # OfflineOS 确收桥下发的 F002 task_dag
        assert offline.received[0]["action"] == "task_dag"
        assert offline.received[0]["data"]["task_id"] == "t1"

    asyncio.run(scenario())


def test_offline_failure_fails_fast_downstream_cancelled() -> None:
    async def scenario() -> None:
        schedule, _offline = build_offline_session({"n2": NodeState.FAILED})
        dag = _chain_dag("t2", 3)
        handle = await schedule.submit_dag(dag)
        await asyncio.wait_for(handle.wait(), timeout=5)
        # n1 成功、n2 失败、n3 因 fail-fast 取消——且桥全终态（无 PENDING 悬挂）
        assert handle.node_states["n1"] == NodeState.SUCCESS
        assert handle.node_states["n2"] == NodeState.FAILED
        assert handle.node_states["n3"] == NodeState.CANCELLED

    asyncio.run(scenario())


def test_offline_same_device_serialized_i3() -> None:
    async def scenario() -> None:
        schedule, offline = build_offline_session()
        dag = _same_device_parallel_dag("t3")
        key = dag.nodes["a"].device_action_key
        handle = await schedule.submit_dag(dag)
        await asyncio.wait_for(handle.wait(), timeout=5)
        assert handle.node_states == {"a": NodeState.SUCCESS, "b": NodeState.SUCCESS}
        # 同 device_action_key 峰值并发恒为 1——每设备锁保串行（I3）
        assert offline.max_concurrent_by_key[key] == 1

    asyncio.run(scenario())


def test_offline_cancel_marks_all_cancelled() -> None:
    async def scenario() -> None:
        schedule, _offline = build_offline_session()
        dag = _chain_dag("t4", 3)
        handle = await schedule.submit_dag(dag)
        # 下发后立即取消（executor 已建、_run 尚未推进）——走图见 _cancelled 即全取消
        await schedule.cancel_task("t4")
        await asyncio.wait_for(handle.wait(), timeout=5)
        assert all(s == NodeState.CANCELLED for s in handle.node_states.values())

    asyncio.run(scenario())


def test_build_offline_session_wires_send_to_offline() -> None:
    session, offline = build_offline_session()
    assert isinstance(session, ScheduleSession)
    assert isinstance(offline, OfflineOS)
    # session.send 即 OfflineOS.receive，offline 已 bind 回该 session
    assert offline._session is session  # noqa: SLF001 —— 白盒校验装配


def test_offline_no_op_on_unknown_downlink() -> None:
    async def scenario() -> None:
        _session, offline = build_offline_session()
        await offline.receive({"action": "host_ready", "data": {}})
        # 未知/无关下行不建任务、不抛错
        assert offline._executors == {}  # noqa: SLF001

    asyncio.run(scenario())


def test_server_offline_mode_state_ready_immediately() -> None:
    server = LocalBridgeServer(offline=True)
    # 离线模式构造即就绪：UI 面可解析到 session 与 LocalApiState
    assert server._get_schedule_session() is not None  # noqa: SLF001
    assert server._get_local_api_state() is not None  # noqa: SLF001


def test_server_real_mode_state_none_until_os_connects() -> None:
    server = LocalBridgeServer(offline=False)
    # 真实模式：OS 未连入前 UI 面解析为 None（local_api 据此 503）
    assert server._get_schedule_session() is None  # noqa: SLF001
    assert server._get_local_api_state() is None  # noqa: SLF001
    # 模拟 OS 连入 → on_session 回调接管，建唯一 LocalApiState
    fake_send = _make_noop_send()
    session = ScheduleSession(fake_send)
    server._adopt_session(session)  # noqa: SLF001
    assert server._get_schedule_session() is session  # noqa: SLF001
    assert server._get_local_api_state() is not None  # noqa: SLF001


def _make_noop_send():
    async def _send(_msg: dict[str, Any]) -> None:
        return None

    return _send
