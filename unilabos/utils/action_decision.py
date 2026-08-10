"""设备 Action 异常决策的等待与关联。"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from unilabos.utils.exception import BUILT_IN_DECISIONS


DecisionKey = Tuple[str, str]


class SkippedActionResult(dict[str, Any]):
    """框架恢复决策产生的跳过结果；普通 Action 返回字典不具备该身份。"""

    def __init__(self, reason: str) -> None:
        super().__init__(status="skipped", reason=reason)


async def run_action_with_decisions(
    *,
    invoke: Callable[[], Awaitable[Any]],
    decide: Callable[[Exception], Awaitable[dict]],
    max_iterations: int = 10,
    max_retries: Optional[int] = None,
) -> Any:
    """执行 Action，并按用户决策实现 retry / skip / abort。"""

    if max_retries is not None and max_retries < 0:
        raise ValueError("max_retries 必须大于等于 0")
    max_attempts = max_iterations if max_retries is None else max_retries + 1
    last_exception = None
    for _ in range(max_attempts):
        try:
            return await invoke()
        except Exception as exc:
            last_exception = exc
            decision = await decide(exc)
            action = decision.get("action", "abort")
            if action == "retry":
                continue
            if action == "skip":
                return SkippedActionResult(decision.get("reason", "user_skip"))
            raise

    if max_retries is not None:
        raise RuntimeError(f"Action 在 {max_retries} 次重试后仍然失败") from last_exception
    raise RuntimeError(f"Action 已连续重试 {max_iterations} 次") from last_exception


@dataclass
class _PendingDecision:
    device_id: str
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop
    is_resolved: bool = False


class PendingDecisionRegistry:
    """以 ``task_id + job_id`` 管理等待中的用户决策。"""

    def __init__(self):
        self._pending: Dict[DecisionKey, _PendingDecision] = {}
        self._lock = threading.Lock()

    async def publish_and_wait(
        self,
        *,
        task_id: str,
        job_id: str,
        device_id: str,
        publish: Callable[[], None],
        timeout: Optional[float] = None,
        default_action: str = "abort",
        on_timeout: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """先注册 Future，再发布异常并等待首个有效决策。"""

        if not task_id or not job_id:
            raise ValueError("异常处理缺少 task_id 或 job_id")
        if default_action not in BUILT_IN_DECISIONS:
            raise ValueError(f"不支持的决策超时默认动作: {default_action}")

        key = (task_id, job_id)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pending = _PendingDecision(device_id=device_id, future=future, loop=loop)

        with self._lock:
            if key in self._pending:
                raise RuntimeError(f"Action 已在等待异常决策: task_id={task_id}, job_id={job_id}")
            self._pending[key] = pending

        try:
            publish()
            if timeout is None:
                decision = await asyncio.shield(future)
            else:
                try:
                    decision = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
                except asyncio.TimeoutError:
                    decision = {"action": default_action, "reason": "user_decision_timeout"}
                    if on_timeout is not None:
                        on_timeout(decision)
                    return decision

            action = decision.get("action") if isinstance(decision, dict) else None
            if action not in BUILT_IN_DECISIONS:
                return {"action": "abort", "reason": "unsupported_decision"}
            return decision
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def resolve(
        self,
        *,
        task_id: str,
        job_id: str,
        device_id: str,
        decision: dict,
    ) -> bool:
        """设置第一条有效决策；重复、过期或路由不匹配时返回 False。"""

        action = decision.get("action") if isinstance(decision, dict) else None
        if action not in BUILT_IN_DECISIONS:
            return False

        key = (task_id, job_id)
        with self._lock:
            pending = self._pending.get(key)
            if pending is None or pending.is_resolved:
                return False
            if pending.device_id and pending.device_id != device_id:
                return False
            pending.is_resolved = True

        def set_result():
            if not pending.future.done():
                pending.future.set_result(decision)

        pending.loop.call_soon_threadsafe(set_result)
        return True

    def has_pending(self, task_id: str, job_id: str) -> bool:
        with self._lock:
            return (task_id, job_id) in self._pending
