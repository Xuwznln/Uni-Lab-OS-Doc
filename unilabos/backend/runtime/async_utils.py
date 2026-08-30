"""Backend 无关的异步函数调度辅助。"""

from __future__ import annotations

import asyncio
import contextlib
from concurrent.futures import CancelledError as FutureCancelledError
import inspect
import threading
import time
import traceback
from typing import Any, Awaitable, Callable, List, Optional


TaskScheduler = Callable[[Awaitable[Any]], Any]
TraceCallback = Callable[[Any], None]
ErrorCallback = Callable[[str], None]


class DeviceAsyncMutex:
    """跨 backend 的设备节点异步互斥锁（FIFO）。

    等待者挂在 ``node.create_wait_future()`` 创建的 Future 上（rclpy executor
    对应 rclpy Future，asyncio loop 对应 asyncio Future），释放时经
    ``node.create_task`` 把唤醒协程调度回节点自己的执行器，避免跨线程直接
    set_result 或依赖 timer。
    """

    def __init__(self, name: str = ""):
        self._lock = threading.Lock()
        self._acquired = False
        self._queue: List[Any] = []
        self._name = name
        self._holder: Optional[str] = None

    async def acquire(self, node: Any, tag: str = "") -> None:
        """获取锁；已被占用时异步等待直到释放。"""
        t0 = time.time()
        with self._lock:
            qlen = len(self._queue)
            if not self._acquired:
                self._acquired = True
                self._holder = tag
                return
            holder = self._holder
            waiter = node.create_wait_future()
            self._queue.append(waiter)
        node.lab_logger().debug(
            f"[Mutex:{self._name}] 进入锁等待队列 tag={tag} holder={holder} queue={qlen + 1}"
        )
        try:
            await waiter
        except BaseException:
            with self._lock:
                if waiter in self._queue:
                    self._queue.remove(waiter)
                    node.lab_logger().debug(
                        f"[Mutex:{self._name}] 取消锁等待 tag={tag} queue={len(self._queue)}"
                    )
            raise
        wait_ms = (time.time() - t0) * 1000
        self._holder = tag
        node.lab_logger().debug(
            f"[Mutex:{self._name}] 队列继续执行 tag={tag} waited={wait_ms:.0f}ms"
        )

    def release(self, node: Any) -> None:
        """释放锁，经节点执行器唤醒下一个等待者。"""
        with self._lock:
            old_holder = self._holder
            next_waiter = None
            while self._queue:
                next_waiter = self._queue.pop(0)
                if not next_waiter.done():
                    break
                next_waiter = None
            if next_waiter is None:
                self._acquired = False
                self._holder = None
                return
            queue_len = len(self._queue)

        node.lab_logger().debug(
            f"[Mutex:{self._name}] 释放锁 holder={old_holder} 唤醒队列下一个 queue={queue_len}"
        )

        async def _wake():
            if not next_waiter.done():
                next_waiter.set_result(None)

        node.create_task(_wake())


async def run_blocking(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """在正确的线程策略下执行阻塞调用（gateway / materials IO）。

    设备协程既可能跑在 asyncio loop（HostLink 本地运行时，阻塞 IO 必须
    挪到线程池），也可能由 rclpy executor 直接迭代（无运行中 loop，
    ``asyncio.to_thread`` 会抛 no running event loop）。rclpy 多线程
    executor 下阻塞当前 callback 线程是安全的，与 ROS2 原生同步
    service 调用同语义。
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return func(*args, **kwargs)
    return await asyncio.to_thread(func, *args, **kwargs)


def run_node_coroutine(node: Any, coroutine: Any, timeout: float = 30.0) -> Any:
    """在节点自己的执行器上运行协程并阻塞等待结果（供非执行器线程调用）。

    依赖 ``node.create_task`` 返回带 ``add_done_callback`` / ``result`` 的
    task/future（rclpy Task 与 concurrent.futures.Future 均满足）。
    """
    task = node.create_task(coroutine)
    done = threading.Event()
    task.add_done_callback(lambda _task: done.set())
    if not done.wait(timeout):
        with contextlib.suppress(Exception):
            task.cancel()
        raise TimeoutError(f"设备协程执行超时({timeout}s)")
    return task.result()


def schedule_async_func(
    scheduler: TaskScheduler,
    func: Any,
    trace_error: bool = True,
    inner_trace_callback: Optional[TraceCallback] = None,
    error_callback: Optional[ErrorCallback] = None,
    **kwargs: Any,
) -> Any:
    """用 backend 提供的 scheduler 执行函数或 awaitable，并返回其 Future。"""

    if not callable(func) and kwargs:
        raise TypeError("awaitable 对象不能再接收额外关键字参数")

    task_name = str(
        getattr(func, "__qualname__", "")
        or getattr(func, "__name__", "")
        or type(func).__name__
    )

    async def invoke() -> Any:
        try:
            result = func(**kwargs) if callable(func) else func
            if inspect.isawaitable(result):
                result = await result
        except BaseException as exc:
            if inner_trace_callback is not None:
                inner_trace_callback(exc)
            raise
        if inner_trace_callback is not None:
            inner_trace_callback(result)
        return result

    coroutine = invoke()
    try:
        future = scheduler(coroutine)
    except BaseException:
        coroutine.close()
        raise

    if trace_error:
        def report_error(done_future: Any) -> None:
            try:
                done_future.result()
            except (asyncio.CancelledError, FutureCancelledError):
                return
            except BaseException as exc:
                if error_callback is not None:
                    detail = "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    )
                    error_callback(f"异步任务 {task_name} 执行失败\n{detail}")

        future.add_done_callback(report_error)

    return future


__all__ = [
    "DeviceAsyncMutex",
    "run_blocking",
    "run_node_coroutine",
    "schedule_async_func",
]
