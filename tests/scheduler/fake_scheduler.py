"""hermetic 测试用的 fake 节点调度器 + 驱动辅助。

不连真实设备 / send_goal，无 time.sleep。完成时刻由测试**手动**驱动
（``complete(node_id)``），并发/串行完全确定性可断言。

FakeScheduler 建模生产 DeviceActionManager 的关键契约：**同 device_action_key
的非 always_free 节点经每设备锁串行、绝不重叠**（I3 / AC-2）。DagExecutor 只管
依赖偏序、把并发交给调度器 —— 用这个 fake 正好验证「执行器不自己做互斥」。
"""

from __future__ import annotations

import asyncio

from unilabos.scheduler.dag_model import DagNode, NodeState


class FakeScheduler:
    def __init__(
        self,
        results: dict[str, NodeState] | None = None,
        *,
        model_device_lock: bool = True,
    ) -> None:
        # node_id -> 终态；缺省 SUCCESS
        self.results: dict[str, NodeState] = results or {}
        self.model_device_lock = model_device_lock
        self._locks: dict[str, asyncio.Lock] = {}
        self._events: dict[str, asyncio.Event] = {}
        # 观测量（供断言）
        self.submitted: list[str] = []  # submit() 被调用顺序
        self.started: list[str] = []  # 真正进入运行（拿到锁）的顺序
        self.running: set[str] = set()  # 当前在运行的节点
        self._key_running: dict[str, int] = {}
        self.max_concurrent_by_key: dict[str, int] = {}

    def _event(self, node_id: str) -> asyncio.Event:
        return self._events.setdefault(node_id, asyncio.Event())

    async def submit(self, node: DagNode) -> NodeState:
        self.submitted.append(node.node_id)
        key = node.device_action_key
        lock = None
        if self.model_device_lock and not node.always_free:
            lock = self._locks.setdefault(key, asyncio.Lock())
            await lock.acquire()
        # 进入运行
        self.running.add(node.node_id)
        self.started.append(node.node_id)
        self._key_running[key] = self._key_running.get(key, 0) + 1
        self.max_concurrent_by_key[key] = max(
            self.max_concurrent_by_key.get(key, 0), self._key_running[key]
        )
        try:
            await self._event(node.node_id).wait()
        finally:
            self.running.discard(node.node_id)
            self._key_running[key] -= 1
            if lock is not None:
                lock.release()
        return self.results.get(node.node_id, NodeState.SUCCESS)

    def complete(self, node_id: str) -> None:
        """标记某节点完成（解析其 awaitable）。"""
        self._event(node_id).set()


async def settle(rounds: int = 30) -> None:
    """把事件循环推进到静止：让已就绪的协程都跑到下一个 await 点。

    用 asyncio.sleep(0) 让出控制权（零墙钟耗时，非 time.sleep），
    小图 30 轮足够收敛，保证确定性。
    """
    for _ in range(rounds):
        await asyncio.sleep(0)
