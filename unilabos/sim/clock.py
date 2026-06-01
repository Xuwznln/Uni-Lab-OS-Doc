"""Simulation-aware clock with real/sim/twin modes."""

from __future__ import annotations

import asyncio
import time
import warnings
from typing import Callable, Literal

RuntimeMode = Literal["real", "sim", "twin"]


class SimClock:
    """Async simulation clock with rate control and pause/resume."""

    def __init__(self, mode: RuntimeMode = "real", scale: float = 1.0, tick_seconds: float = 0.05):
        if mode not in ("real", "sim", "twin"):
            raise ValueError(f"Unsupported runtime mode: {mode}")
        if scale <= 0:
            raise ValueError("scale must be > 0")
        self._mode: RuntimeMode = mode
        self._scale = float(scale if mode == "sim" else 1.0)
        self._tick_seconds = max(float(tick_seconds), 0.001)
        self._paused = False
        self._wall_started = time.monotonic()
        self._sim_started = 0.0
        self._last_wall = self._wall_started
        self._callbacks: list[Callable[[float], None]] = []

    @property
    def mode(self) -> RuntimeMode:
        return self._mode

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def paused(self) -> bool:
        return self._paused

    def now(self) -> float:
        if self._mode == "sim":
            if self._paused:
                return self._sim_started
            return self._sim_started + (time.monotonic() - self._last_wall) * self._scale
        return time.time()

    async def sleep(self, seconds: float) -> None:
        seconds = max(float(seconds), 0.0)
        if seconds == 0:
            await asyncio.sleep(0)
            return
        if self._mode != "sim":
            await asyncio.sleep(seconds)
            return

        target = self.now() + seconds
        while self.now() < target:
            if self._paused:
                await asyncio.sleep(self._tick_seconds)
                continue
            remaining_sim = max(target - self.now(), 0.0)
            await asyncio.sleep(min(self._tick_seconds, remaining_sim / self._scale))

    def sleep_sync(self, seconds: float) -> None:
        seconds = max(float(seconds), 0.0)
        if seconds == 0:
            return
        if self._mode != "sim":
            time.sleep(seconds)
            return
        target = self.now() + seconds
        while self.now() < target:
            if self._paused:
                time.sleep(self._tick_seconds)
                continue
            remaining_sim = max(target - self.now(), 0.0)
            time.sleep(min(self._tick_seconds, remaining_sim / self._scale))

    def set_scale(self, scale: float) -> bool:
        scale = float(scale)
        if scale <= 0:
            raise ValueError("scale must be > 0")
        if self._mode != "sim":
            warnings.warn(f"SimClock.set_scale: scale is locked to 1.0 in mode={self._mode}", UserWarning)
            self._scale = 1.0
            return False
        self._sim_started = self.now()
        self._last_wall = time.monotonic()
        self._scale = scale
        for cb in list(self._callbacks):
            cb(scale)
        return True

    def pause(self) -> bool:
        if self._mode != "sim":
            warnings.warn(f"SimClock.pause: no-op in mode={self._mode}", UserWarning)
            return False
        if not self._paused:
            self._sim_started = self.now()
            self._paused = True
        return True

    def resume(self) -> bool:
        if self._mode != "sim":
            warnings.warn(f"SimClock.resume: no-op in mode={self._mode}", UserWarning)
            return False
        if self._paused:
            self._last_wall = time.monotonic()
            self._paused = False
        return True

    def on_scale_change(self, cb: Callable[[float], None]) -> None:
        self._callbacks.append(cb)


async def sim_sleep(seconds: float) -> None:
    from unilabos.sim.context import get_runtime_context

    await get_runtime_context().clock.sleep(seconds)


def sim_sleep_sync(seconds: float) -> None:
    from unilabos.sim.context import get_runtime_context

    get_runtime_context().clock.sleep_sync(seconds)
