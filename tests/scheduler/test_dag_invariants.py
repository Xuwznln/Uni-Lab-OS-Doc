"""F002 property-based 不变量测试（I1~I6）— Hypothesis 生成任意合法 DAG。

靶子是纯同步状态机 DagWalk（调度是数学）：随机节点数 / 随机无环边 /
随机 device 分配 / 随机 always_free / 随机合法执行调度，断言不变量恒成立。

不变量：
- I1 恰好一次：每节点 PENDING->RUNNING->SUCCESS 各恰好一次，绝不重复提交。
- I2 偏序遵从：节点进入 ready 时，其全部前驱必已 SUCCESS。
- I4 resume 等价：从任意合法前缀游标恢复，续跑结果与整跑等价，已完成不重跑。
- I5 含环即抛：任何回边构成的环，解析期即 DagValidationError。
- I6 有限步终止：无环图必在 <= 节点数 轮内走完。

I3（同设备无重叠）是「注入的调度器」层属性（DeviceActionManager 每设备锁），
不属于纯 DagWalk —— 已由 T04 fake_scheduler 的 max_concurrent_by_key 断言覆盖。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from unilabos.scheduler.dag_model import (
    DagValidationError,
    NodeState,
    TaskDag,
)
from unilabos.scheduler.dag_executor import DagWalk

import pytest


# ---------------------------------------------------------------- 生成器
@st.composite
def legal_dags(draw: st.DrawFn) -> tuple[TaskDag, list[str], list[tuple[str, str]]]:
    """生成任意合法（无环）DAG。

    节点按 n0..n{k-1} 编号；边只从低号指向高号（i<j）从而**构造性无环**，
    覆盖任意宽度/深度/分叉/汇聚。device 与 always_free 随机分配。
    返回 (dag, node_ids, edges)。
    """
    n = draw(st.integers(min_value=1, max_value=7))
    node_ids = [f"n{i}" for i in range(n)]

    # 所有可能的前向边 (i<j)，随机取子集
    candidates = [(i, j) for i in range(n) for j in range(i + 1, n)]
    chosen = draw(
        st.lists(st.sampled_from(candidates), unique=True)
        if candidates
        else st.just([])
    )
    edges = [(node_ids[i], node_ids[j]) for (i, j) in chosen]

    devices = draw(
        st.lists(
            st.sampled_from(["d0", "d1", "d2"]),
            min_size=n,
            max_size=n,
        )
    )
    free = draw(st.lists(st.booleans(), min_size=n, max_size=n))

    nodes = [
        {
            "node_id": node_ids[i],
            "device_id": devices[i],
            "action": "act",
            "always_free": free[i],
        }
        for i in range(n)
    ]
    message = {
        "task_id": "tprop",
        "notebook_id": "nb",
        "server_info": {},
        "nodes": nodes,
        "edges": [
            {"source_node_uuid": s, "target_node_uuid": t} for (s, t) in edges
        ],
    }
    return TaskDag.from_message(message), node_ids, edges


def _predecessors(edges: list[tuple[str, str]]) -> dict[str, set[str]]:
    preds: dict[str, set[str]] = {}
    for s, t in edges:
        preds.setdefault(t, set()).add(s)
    return preds


def _drive(
    walk: DagWalk,
    edges: list[tuple[str, str]],
    draw: st.DrawFn,
) -> list[str]:
    """按随机合法调度把 walk 走到 done，返回真正 mark_running 的节点顺序。

    每轮：断言 ready 集偏序（I2），随机挑 ready 中一个 mark_running + on_success，
    保证覆盖任意交错顺序。返回运行顺序供 I1 断言。
    """
    preds = _predecessors(edges)
    run_order: list[str] = []
    rounds = 0
    max_rounds = len(walk.states) + 1  # I6：应在节点数轮内完成
    while not walk.is_done():
        rounds += 1
        assert rounds <= max_rounds, "超出有限步上界（违反 I6）"
        ready = walk.ready()
        assert ready, "未 done 却无 ready：无环图不应停摆"
        # I2：ready 中每个节点的全部前驱必已 SUCCESS
        for nid in ready:
            for p in preds.get(nid, ()):
                assert walk.states[p] == NodeState.SUCCESS, (
                    f"{nid} 就绪但前驱 {p} 未成功（违反 I2）"
                )
        pick = draw(st.sampled_from(ready))
        walk.mark_running(pick)
        run_order.append(pick)
        walk.on_success(pick)
    return run_order


# ---------------------------------------------------------------- I1/I2/I6
@settings(max_examples=200, deadline=None)
@given(bundle=legal_dags(), data=st.data())
def test_i1_i2_i6_exactly_once_partial_order_terminates(bundle, data):
    dag, node_ids, edges = bundle
    walk = DagWalk(dag)
    run_order = _drive(walk, edges, data.draw)

    # I1：每节点恰好运行一次，且全部到 SUCCESS
    assert sorted(run_order) == sorted(node_ids)
    assert len(run_order) == len(set(run_order))
    assert all(st_ == NodeState.SUCCESS for st_ in walk.snapshot().values())

    # I2（再证）：run_order 是合法拓扑序 —— 每条边 source 先于 target
    pos = {nid: i for i, nid in enumerate(run_order)}
    for s, t in edges:
        assert pos[s] < pos[t], f"边 {s}->{t} 违反拓扑序（违反 I2）"


# ---------------------------------------------------------------- I4
@settings(max_examples=200, deadline=None)
@given(bundle=legal_dags(), data=st.data())
def test_i4_resume_equivalent_no_duplicate(bundle, data):
    dag, node_ids, edges = bundle

    # 先整跑一遍得到一个合法拓扑序
    full = DagWalk(dag)
    topo = _drive(full, edges, data.draw)

    # 取该拓扑序的任意前缀作为「崩溃前已完成」游标
    k = data.draw(st.integers(min_value=0, max_value=len(topo)))
    completed = topo[:k]

    resumed = DagWalk(dag, completed=completed)
    # 已完成节点在 resume 后即 SUCCESS，且不再出现在 ready
    for nid in completed:
        assert resumed.states[nid] == NodeState.SUCCESS
    assert not (set(resumed.ready()) & set(completed))

    rerun = _drive(resumed, edges, data.draw)

    # I4：resume 只跑未完成部分，已完成绝不重跑；合计仍全 SUCCESS
    assert set(rerun) == set(node_ids) - set(completed)
    assert not (set(rerun) & set(completed))
    assert all(st_ == NodeState.SUCCESS for st_ in resumed.snapshot().values())


# ---------------------------------------------------------------- I5
@settings(max_examples=200, deadline=None)
@given(bundle=legal_dags(), data=st.data())
def test_i5_any_backedge_rejected_at_parse(bundle, data):
    dag, node_ids, edges = bundle
    if len(node_ids) < 2:
        return  # 单节点无法构环

    # 任取一对不同节点，加一条会成环的边：
    # 若已有 s->...->t（前向），加 t->s 即成环；这里直接对任意 i<j 加 j->i。
    i = data.draw(st.integers(min_value=0, max_value=len(node_ids) - 2))
    j = data.draw(st.integers(min_value=i + 1, max_value=len(node_ids) - 1))
    back = {"source_node_uuid": node_ids[j], "target_node_uuid": node_ids[i]}

    message = {
        "task_id": "tcycle",
        "notebook_id": "nb",
        "server_info": {},
        "nodes": [
            {"node_id": nid, "device_id": "d0", "action": "act"}
            for nid in node_ids
        ],
        "edges": [
            {"source_node_uuid": s, "target_node_uuid": t} for (s, t) in edges
        ]
        + [back],
    }
    # 需先有从 i 到 j 的可达路径才必成环；无路径时 j->i 仍是前向边、不一定成环。
    # 构造充分条件：显式加 i->j 前向边，再加 j->i，必成 2-环。
    message["edges"].append(
        {"source_node_uuid": node_ids[i], "target_node_uuid": node_ids[j]}
    )
    with pytest.raises(DagValidationError):
        TaskDag.from_message(message)
