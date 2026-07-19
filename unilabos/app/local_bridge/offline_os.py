"""offline_os — 桥的离线执行核（进程内「仿真 OS」，无真实 OS 连入时的备档）。

真实路径里 task_dag 由**真实 OS 进程**的 ws_client._handle_task_dag→TaskDagRunner 跑，
桥只翻译。但本环境无 Go 后端、亦未必总能拉起真实 OS 进程；OfflineOS 在进程内顶替
OS 面：接桥下发的 F002 task_dag，用 F002 DagExecutor 走依赖偏序、每设备锁保 I3
（同设备串行、不重叠），逐节点回发 F002 job_status。UI 面因而无 OS 也能完整动，
且执行仍走 F002 真实 DagExecutor——不复制走图逻辑（单一事实源）。

接线：OfflineOS.receive 充当 ScheduleSession 的 send 协程（桥→OS 下行入口）；
bind(session) 后经 session.handle_incoming 把 job_status 回喂桥（OS→桥上行）。
节点自动完成（无真实硬件、无 time.sleep）——running 后让出一次事件循环再落终态，
使 running 态可观测；失败/取消由 results 编程或 cancel_task 触发。

契约见 docs/features/F003-local-workflow-bridge/interface-design.md §一（与 schedule_ws 同面）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from unilabos.app.local_bridge.schedule_ws import ScheduleSession
from unilabos.scheduler.dag_executor import DagExecutor
from unilabos.scheduler.dag_model import TERMINAL_STATES, DagNode, NodeState, TaskDag

logger = logging.getLogger(__name__)

# NodeState → job_status.status 字面量（schedule_ws._STATUS_TO_NODE_STATE 的逆，供收敛兜底）
_STATE_TO_STATUS: dict[NodeState, str] = {
    NodeState.RUNNING: "running",
    NodeState.SUCCESS: "success",
    NodeState.FAILED: "failed",
    NodeState.CANCELLED: "cancelled",
}


class OfflineOS:
    """进程内仿真 OS：接 F002 task_dag，用 DagExecutor 走图并回发 job_status。

    - receive(msg)：作为 ScheduleSession.send 注入——收桥下行 task_dag / cancel_task。
    - bind(session)：绑定回喂目标——经 session.handle_incoming 上行 job_status。
    - results：可编程终态（node_id→NodeState），缺省 SUCCESS；供演示失败路径。
    - model_device_lock：每 device_action_key 一把锁，非 always_free 节点串行（建模 I3）。
    """

    def __init__(
        self,
        results: dict[str, NodeState] | None = None,
        *,
        model_device_lock: bool = True,
    ) -> None:
        self.results = results or {}
        self.model_device_lock = model_device_lock
        self._session: ScheduleSession | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._executors: dict[str, DagExecutor] = {}
        self._dags: dict[str, TaskDag] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.received: list[dict[str, Any]] = []  # 观测量（供断言）
        # 每 device_action_key 的实时/峰值在跑数——供 I3 串行断言（==1 即从不重叠）
        self._key_running: dict[str, int] = {}
        self.max_concurrent_by_key: dict[str, int] = {}

    def bind(self, session: ScheduleSession) -> None:
        """绑定回喂目标 ScheduleSession（job_status 经其 handle_incoming 上行）。"""
        self._session = session

    async def receive(self, msg: dict[str, Any]) -> None:
        """ScheduleSession.send 注入点：分发桥→OS 下行报文（task_dag / cancel_task）。"""
        self.received.append(msg)
        action = msg.get("action", "")
        data = msg.get("data")
        data = data if isinstance(data, dict) else {}
        if action == "task_dag":
            await self._start_task(data)
        elif action == "cancel_task":
            self._cancel_task(data.get("task_id", ""))
        else:
            logger.debug("[offline_os] 忽略下行 action=%s", action)

    async def _start_task(self, payload: dict[str, Any]) -> None:
        """解析 F002 task_dag，起后台协程用 DagExecutor 走图（不阻塞下发方）。"""
        dag = TaskDag.from_message(payload)
        executor = DagExecutor(dag, self._make_submit(dag))
        self._dags[dag.task_id] = dag
        self._executors[dag.task_id] = executor
        self._tasks[dag.task_id] = asyncio.ensure_future(self._run(dag.task_id, executor))
        logger.info("[offline_os] 已受理 task_dag %s（%d 节点）", dag.task_id, len(dag.nodes))

    def _cancel_task(self, task_id: str) -> None:
        """cancel_task：停止对应 executor 调度后继（未决节点由收敛兜底落 cancelled）。"""
        executor = self._executors.get(task_id)
        if executor is not None:
            executor.cancel()
            logger.info("[offline_os] 已取消 task_dag %s", task_id)

    async def _run(self, task_id: str, executor: DagExecutor) -> None:
        """走完整张图；结束后对未收到终态 job_status 的节点补发（收敛兜底）。"""
        try:
            snapshot = await executor.run()
        finally:
            self._executors.pop(task_id, None)
            self._tasks.pop(task_id, None)
        # fail-fast/取消使部分节点未经 submit 即落终态（无 job_status），补发以令桥收敛
        dag = self._dags.get(task_id)
        if dag is None:
            return
        for node_id, state in snapshot.items():
            if self._bridge_terminal(task_id, node_id):
                continue
            status = _STATE_TO_STATUS.get(state)
            if status:
                await self._emit(dag, dag.nodes[node_id], status)

    def _bridge_terminal(self, task_id: str, node_id: str) -> bool:
        """桥侧该节点是否已达终态（避免对已收到终态的节点重复补发）。"""
        if self._session is None:
            return True
        state = self._session.node_state(task_id, node_id)
        return state in TERMINAL_STATES

    def _make_submit(self, dag: TaskDag):
        """构 DagExecutor 注入的 submit：每设备锁串行 + 回发 running/终态 job_status。"""

        async def submit(node: DagNode) -> NodeState:
            key = node.device_action_key
            lock: asyncio.Lock | None = None
            if self.model_device_lock and not node.always_free:
                lock = self._locks.setdefault(key, asyncio.Lock())
                await lock.acquire()
            # 进入运行——记录每 key 并发峰值（同 key 非 free 有锁则恒为 1，即 I3）
            self._key_running[key] = self._key_running.get(key, 0) + 1
            self.max_concurrent_by_key[key] = max(
                self.max_concurrent_by_key.get(key, 0), self._key_running[key]
            )
            try:
                await self._emit(dag, node, "running")
                await asyncio.sleep(0)  # 让出一次，使 running 态可观测（非 time.sleep）
                state = self.results.get(node.node_id, NodeState.SUCCESS)
                await self._emit(dag, node, _STATE_TO_STATUS[state])
                return state
            finally:
                self._key_running[key] -= 1
                if lock is not None:
                    lock.release()

        return submit

    async def _emit(self, dag: TaskDag, node: DagNode, status: str) -> None:
        """回发一条 F002 job_status 给桥（node_id==job_id）。"""
        if self._session is None:
            return
        await self._session.handle_incoming(
            {
                "action": "job_status",
                "data": {
                    "job_id": node.node_id,
                    "task_id": dag.task_id,
                    "device_id": node.device_id,
                    "notebook_id": dag.notebook_id,
                    "action_name": node.action,
                    "status": status,
                    "feedback_data": {},
                    "return_info": None,
                    "timestamp": 0.0,
                },
            }
        )
