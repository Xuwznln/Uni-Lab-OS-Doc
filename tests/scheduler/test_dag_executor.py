"""F002 OS 本地 DAG 执行器 — hermetic 单元测试（覆盖 AC-1~AC-5）。

fake 节点调度器 + 手动驱动完成，不连真实设备、无 time.sleep、确定性无 flaky。
每个测试用 asyncio.run 单事件循环内并发驱动执行器与断言。
"""

from __future__ import annotations

import asyncio

import pytest

from unilabos.scheduler.dag_executor import DagExecutor, DagWalk
from unilabos.scheduler.dag_model import DagValidationError, NodeState, TaskDag
from unilabos.scheduler.dag_persistence import DagCursorStore

from tests.scheduler.fake_scheduler import FakeScheduler, settle


def _node(node_id: str, device_id: str, action: str = "a", **kw) -> dict:
    return {"node_id": node_id, "device_id": device_id, "action": action, **kw}


def _edge(src: str, tgt: str) -> dict:
    return {"source_node_uuid": src, "target_node_uuid": tgt}


def _dag(nodes: list[dict], edges: list[dict], task_id: str = "t1") -> TaskDag:
    return TaskDag.from_message(
        {
            "task_id": task_id,
            "notebook_id": "nb1",
            "server_info": {},
            "nodes": nodes,
            "edges": edges,
        }
    )


# ---------------------------------------------------------------- AC-1
def test_ac1_diamond_concurrent_walk():
    """菱形 A->B、A->C、B->D、C->D，B/C 不同设备：A 后 B/C 并发，二者后 D，各恰好一次。"""
    dag = _dag(
        [_node("A", "d1"), _node("B", "d2"), _node("C", "d3"), _node("D", "d4")],
        [_edge("A", "B"), _edge("A", "C"), _edge("B", "D"), _edge("C", "D")],
    )
    fake = FakeScheduler()
    ex = DagExecutor(dag, fake.submit)

    async def scenario():
        run_task = asyncio.ensure_future(ex.run())
        await settle()
        assert fake.running == {"A"}  # 起点先跑

        fake.complete("A")
        await settle()
        assert fake.running == {"B", "C"}  # 并发，不是串行

        fake.complete("B")
        fake.complete("C")
        await settle()
        assert fake.running == {"D"}  # B、C 都完成后 D 才起跑

        fake.complete("D")
        result = await run_task
        return result

    result = asyncio.run(scenario())
    assert all(st == NodeState.SUCCESS for st in result.values())
    # I1 恰好一次
    assert sorted(fake.started) == ["A", "B", "C", "D"]
    assert len(fake.started) == len(set(fake.started))


# ---------------------------------------------------------------- AC-2
def test_ac2_same_device_serialized():
    """同 device_action_key 两 ready 节点：经每设备锁串行、不重叠，顺序稳定。"""
    dag = _dag(
        [
            _node("A", "d1"),
            _node("B", "d2", "stir"),  # B、C 同设备同动作 -> 同 key
            _node("C", "d2", "stir"),
            _node("D", "d4"),
        ],
        [_edge("A", "B"), _edge("A", "C"), _edge("B", "D"), _edge("C", "D")],
    )
    fake = FakeScheduler()
    ex = DagExecutor(dag, fake.submit)

    async def scenario():
        run_task = asyncio.ensure_future(ex.run())
        await settle()
        fake.complete("A")
        await settle()
        # 同 key 锁：B、C 只有一个在跑
        both = fake.running & {"B", "C"}
        assert len(both) == 1
        first = both.pop()
        fake.complete(first)
        await settle()
        second = ({"B", "C"} - {first}).pop()
        assert fake.running & {"B", "C"} == {second}  # 前者释放锁后后者才跑
        fake.complete(second)
        await settle()
        assert fake.running == {"D"}
        fake.complete("D")
        return await run_task

    result = asyncio.run(scenario())
    assert all(st == NodeState.SUCCESS for st in result.values())
    # I3：同 key 峰值并发恒为 1（绝不重叠）
    assert fake.max_concurrent_by_key["/devices/d2/stir"] == 1


def test_ac2_always_free_not_serialized():
    """always_free 节点即便同 key 也不被锁串行（并发放行）。"""
    dag = _dag(
        [
            _node("A", "d1"),
            _node("B", "d2", "stir", always_free=True),
            _node("C", "d2", "stir", always_free=True),
        ],
        [_edge("A", "B"), _edge("A", "C")],
    )
    fake = FakeScheduler()
    ex = DagExecutor(dag, fake.submit)

    async def scenario():
        run_task = asyncio.ensure_future(ex.run())
        await settle()
        fake.complete("A")
        await settle()
        assert fake.running == {"B", "C"}  # always_free 并发
        fake.complete("B")
        fake.complete("C")
        return await run_task

    result = asyncio.run(scenario())
    assert all(st == NodeState.SUCCESS for st in result.values())
    assert fake.max_concurrent_by_key["/devices/d2/stir"] == 2


# ---------------------------------------------------------------- AC-3
def test_ac3_disconnect_does_not_halt_walk():
    """中途「断网」：on_node_terminal（上行/持久化）抛错不打断走图，全节点仍完成。"""
    dag = _dag(
        [_node("A", "d1"), _node("B", "d2"), _node("C", "d3")],
        [_edge("A", "B"), _edge("B", "C")],
    )
    fake = FakeScheduler()

    def flaky_publish(node_id: str, status: NodeState) -> None:
        if node_id == "B":  # 模拟 B 完成瞬间断网、上行失败
            raise ConnectionError("ws disconnected")

    ex = DagExecutor(dag, fake.submit, on_node_terminal=flaky_publish)

    async def scenario():
        run_task = asyncio.ensure_future(ex.run())
        await settle()
        fake.complete("A")
        await settle()
        fake.complete("B")  # 触发 flaky_publish 抛错
        await settle()
        assert fake.running == {"C"}  # 断网后仍继续调度后继
        fake.complete("C")
        return await run_task

    result = asyncio.run(scenario())
    assert all(st == NodeState.SUCCESS for st in result.values())


# ---------------------------------------------------------------- AC-4
def test_ac4_resume_no_duplicate():
    """崩溃后从游标恢复：已 completed 不重跑，未完成从正确 ready-set 续跑。"""
    dag = _dag(
        [_node("A", "d1"), _node("B", "d2"), _node("C", "d3"), _node("D", "d4")],
        [_edge("A", "B"), _edge("B", "C"), _edge("C", "D")],
        task_id="tr",
    )
    # 模拟已完成 A、B（游标）
    walk = DagWalk(dag, completed=["A", "B"])
    assert walk.ready() == ["C"]

    fake = FakeScheduler()
    ex = DagExecutor(dag, fake.submit, walk=walk)

    async def scenario():
        run_task = asyncio.ensure_future(ex.run())
        await settle()
        assert fake.running == {"C"}
        fake.complete("C")
        await settle()
        fake.complete("D")
        return await run_task

    result = asyncio.run(scenario())
    assert all(st == NodeState.SUCCESS for st in result.values())
    assert "A" not in fake.started and "B" not in fake.started  # 不重复执行
    assert sorted(fake.started) == ["C", "D"]


def test_ac4_cursor_roundtrip(tmp_path):
    """游标原子写 + 重读：completed 累积、failed 置位。"""
    store = DagCursorStore(tmp_path)
    store.record_terminal("tk", "A", NodeState.SUCCESS)
    store.record_terminal("tk", "B", NodeState.SUCCESS)
    cur = store.load("tk")
    assert cur is not None
    assert cur.completed == ["A", "B"]
    assert cur.failed is False
    store.record_terminal("tk", "C", NodeState.FAILED)
    assert store.load("tk").failed is True


# ---------------------------------------------------------------- AC-5
def test_ac5a_fail_fast():
    """某节点 failed：同组未终态节点被取消、不再调度新节点、任务整体 failed。"""
    dag = _dag(
        [_node("A", "d1"), _node("B", "d2"), _node("C", "d3"), _node("D", "d4")],
        [_edge("A", "B"), _edge("A", "C"), _edge("B", "D"), _edge("C", "D")],
    )
    fake = FakeScheduler(results={"B": NodeState.FAILED})
    ex = DagExecutor(dag, fake.submit)

    async def scenario():
        run_task = asyncio.ensure_future(ex.run())
        await settle()
        fake.complete("A")
        await settle()
        fake.complete("B")  # B 失败 -> fail-fast
        return await run_task

    result = asyncio.run(scenario())
    assert result["B"] == NodeState.FAILED
    assert result["D"] != NodeState.SUCCESS  # D 绝不起跑
    assert "D" not in fake.started
    # 未终态节点被取消（C 可能在跑，被取消；D 从未调度）
    assert result["D"] in (NodeState.CANCELLED, NodeState.PENDING)


def test_ac5b_cycle_rejected_at_parse():
    """含环 DAG：解析期即抛 DagValidationError，绝不进入执行。"""
    with pytest.raises(DagValidationError):
        _dag(
            [_node("A", "d1"), _node("B", "d2")],
            [_edge("A", "B"), _edge("B", "A")],
        )
