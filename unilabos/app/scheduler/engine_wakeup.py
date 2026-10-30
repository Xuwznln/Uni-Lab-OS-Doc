"""宿主拥有的单个合并计时器；引擎只能建议下一次计算时间。"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

MINIMUM_WAKEUP_MS = 100


class EngineWakeup:
    def __init__(
        self, wake: Callable[[], Any], *, timer_factory: Callable[..., Any] = threading.Timer,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._wake, self._timer_factory, self._monotonic = wake, timer_factory, monotonic
        self._lock = threading.Lock()
        self._timer: Any = None
        self._deadline = 0.0
        self._generation = 0
        self._closed = False

    def schedule(self, delay_ms: int | None) -> None:
        """0 至少退避 100ms；重复建议不推迟已有的更早唤醒。"""
        with self._lock:
            if self._closed:
                return
            if delay_ms is None:
                self._cancel()
                return
            delay = min(max(MINIMUM_WAKEUP_MS, delay_ms) / 1000, threading.TIMEOUT_MAX)
            deadline = self._monotonic() + delay
            if self._timer is not None and self._deadline <= deadline:
                return
            self._cancel()
            generation = self._generation

            def fire() -> None:
                with self._lock:
                    if self._closed or generation != self._generation:
                        return
                    self._timer = None
                self._wake()

            self._deadline = deadline
            self._timer = self._timer_factory(delay, fire)
            self._timer.daemon = True
            self._timer.start()

    def _cancel(self) -> None:
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def cancel(self) -> None:
        with self._lock:
            self._cancel()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel()
