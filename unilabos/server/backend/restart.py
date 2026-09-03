"""安静点重启协调器（调试用）。

重启请求登记后先暂停本地调度派发，等执行端 active job 清空（"执行完最后一个
任务"或"任务都处于等待"）再执行重启，作用域按运行形态自动选择：

``edge``
    ``--role backend`` 分离模式：通知 Edge 进程整进程重启（HTTP 调 Edge 管理
    API），本进程（调度权威）常驻；Edge 重连 runtime.v1 控制面会话后自动恢复派发。
``process``
    同进程模式（Host 与设备同进程）：停管理 API → 走 main 的正常退出链路
    （关库、停 backend）→ CLI 入口用相同参数拉起新进程。等待/未派发的任务由
    调度器 ``start(recover=True)`` 在新进程里恢复，不会失败。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 连续两次采样为空才算安静，跨过"上一个 job 刚结束、回调链还没落账"的窗口
_QUIESCENT_CONFIRMATIONS = 2
_POLL_INTERVAL_SECONDS = 0.5


class RestartCoordinator:
    """单例：登记重启请求、检测安静点并执行进程级重启。"""

    def __init__(
        self,
        get_execution_backend: Callable[[], Any],
        get_scheduler: Callable[[], Any],
    ) -> None:
        self._get_execution_backend = get_execution_backend
        self._get_scheduler = get_scheduler
        self._lock = threading.RLock()
        self._pending = False
        self._mode = "quiescent"
        self._scope = "auto"
        self._requested_at: Optional[float] = None
        self._watcher: Optional[threading.Thread] = None
        self._restarting = False

    @staticmethod
    def _edge_control_service() -> Any:
        from unilabos.server.backend.edge_control import get_edge_control_service

        return get_edge_control_service()

    def _resolve_scope(self) -> str:
        with self._lock:
            scope = self._scope
        if scope != "auto":
            return scope
        if self._edge_control_service() is not None:
            return "edge"
        return "process"

    # ── 状态查询 ─────────────────────────────────────────────

    def active_job_ids(self) -> list[str]:
        backend = self._get_execution_backend()
        if backend is not None:
            manager = getattr(backend, "device_manager", None)
            if manager is not None:
                return [job.job_id for job in manager.get_active_jobs()]
            return []
        # --role backend：执行端在 Edge 进程，取 runtime.v1 控制面在途命令视图
        service = self._edge_control_service()
        if service is not None:
            return service.active_job_ids()
        return []

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "pending": self._pending,
                "mode": self._mode,
                "scope": self._scope,
                "effective_scope": self._resolve_scope(),
                "requested_at": self._requested_at,
                "restarting": self._restarting,
                "active_jobs": self.active_job_ids(),
                "dispatch_paused": self._scheduler_paused(),
            }

    def _scheduler_paused(self) -> bool:
        scheduler = self._get_scheduler()
        return bool(scheduler is not None and scheduler.dispatch_paused)

    # ── 请求 / 取消 ──────────────────────────────────────────

    def request(self, mode: str = "quiescent", scope: str = "auto") -> dict[str, Any]:
        if mode not in ("quiescent", "immediate"):
            raise ValueError(f"unsupported restart mode: {mode!r}")
        if scope not in ("auto", "edge", "process"):
            raise ValueError(f"unsupported restart scope: {scope!r}")
        if scope == "edge" and self._edge_control_service() is None:
            raise ValueError("restart scope 'edge' requires --role backend mode")
        with self._lock:
            if self._restarting:
                return self.status()
            self._pending = True
            self._mode = mode
            self._scope = scope
            self._requested_at = time.time()
            scheduler = self._get_scheduler()
            if scheduler is not None:
                scheduler.pause_dispatch()
            if self._watcher is None or not self._watcher.is_alive():
                self._watcher = threading.Thread(
                    target=self._watch,
                    name="RestartCoordinator",
                    daemon=True,
                )
                self._watcher.start()
        logger.info(
            "[Restart] 重启请求已登记 (mode=%s, scope=%s)，等待执行端安静",
            mode,
            scope,
        )
        return self.status()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._restarting:
                return self.status()
            self._pending = False
            self._requested_at = None
            scheduler = self._get_scheduler()
        if scheduler is not None:
            scheduler.resume_dispatch()
        logger.info("[Restart] 重启请求已取消，恢复调度派发")
        return self.status()

    # ── 安静点检测与执行 ─────────────────────────────────────

    def _watch(self) -> None:
        confirmations = 0
        while True:
            time.sleep(_POLL_INTERVAL_SECONDS)
            with self._lock:
                if not self._pending:
                    return
                mode = self._mode
            if mode == "immediate":
                break
            if self.active_job_ids():
                confirmations = 0
                continue
            confirmations += 1
            if confirmations >= _QUIESCENT_CONFIRMATIONS:
                break
        with self._lock:
            if not self._pending:
                return
            self._restarting = True
        self._execute()

    def _execute(self) -> None:
        backend = self._get_execution_backend()
        if backend is not None:
            backend.wait_idle(timeout=5.0)
        if self._resolve_scope() == "edge":
            self._execute_edge_restart()
        else:
            self._execute_process_restart()

    def _execute_edge_restart(self) -> None:
        """通知 Edge 进程整进程重启；调度权威常驻，重连后恢复派发。"""

        import requests

        from unilabos.config.config import HTTPConfig

        edge_addr = str(HTTPConfig.edge_data_addr or "").rstrip("/")
        logger.warning(
            "[Restart] 执行端已安静，通知 Edge 进程整进程重启 (%s)", edge_addr
        )
        try:
            response = requests.post(
                f"{edge_addr}/api/v1/restart",
                json={"mode": "immediate", "scope": "process"},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException:
            logger.exception(
                "[Restart] 无法通知 Edge 重启，派发保持暂停；"
                "确认 Edge 状态后可 DELETE /api/v1/restart 手动恢复"
            )
            with self._lock:
                self._restarting = False
            return
        ready = self._wait_edge_reconnected()
        if not ready:
            logger.error(
                "[Restart] Edge 重启后未在预期时间内重连，派发保持暂停；"
                "Edge 恢复后可 DELETE /api/v1/restart 手动恢复"
            )
            with self._lock:
                self._restarting = False
            return
        with self._lock:
            self._pending = False
            self._requested_at = None
            self._restarting = False
            scheduler = self._get_scheduler()
        if scheduler is not None:
            scheduler.resume_dispatch()
        logger.warning("[Restart] Edge 已重连 runtime.v1 控制面会话，派发已恢复")

    def _wait_edge_reconnected(
        self,
        *,
        disconnect_timeout: float = 30.0,
        reconnect_timeout: float = 300.0,
    ) -> bool:
        """等待 Edge 断开旧连接并以新进程重连。"""

        service = self._edge_control_service()
        if service is None:
            return False
        deadline = time.time() + disconnect_timeout
        while time.time() < deadline and service.connected:
            time.sleep(_POLL_INTERVAL_SECONDS)
        # 旧连接可能在通知前就已断开，不强求观察到断开事件
        deadline = time.time() + reconnect_timeout
        while time.time() < deadline:
            if service.connected:
                return True
            time.sleep(_POLL_INTERVAL_SECONDS)
        return False

    def _execute_process_restart(self) -> None:
        logger.warning("[Restart] 执行端已安静，开始整进程重启")
        _set_restart_requested()
        from unilabos.server.api.app import request_server_shutdown

        if not request_server_shutdown():
            # 重启依赖 uvicorn 停机驱动 main 的正常退出链路；管理 API 未运行
            # 时（嵌入式/测试）无法整进程重启，保留标记交由上层处理。
            logger.error("[Restart] 管理 API 未运行，无法触发整进程重启")
            with self._lock:
                self._restarting = False


# ── 进程级重启标记与再拉起 ──────────────────────────────────────

_restart_requested = threading.Event()


def _set_restart_requested() -> None:
    _restart_requested.set()


def is_restart_requested() -> bool:
    return _restart_requested.is_set()


def spawn_replacement_process() -> None:
    """用相同参数在同一控制台拉起新的 edge 进程。

    使用 ``python -m unilabos.app.main`` 而不是 ``os.execv``：Windows 的 execv
    是 spawn+exit 模拟，会把 listening socket 等句柄继承给新进程；这里在旧进程
    完整退出链路（端口、数据库均已释放）之后启动，且默认 close_fds 不继承句柄。
    """

    command = [sys.executable, "-m", "unilabos.app.main", *sys.argv[1:]]
    logger.warning("[Restart] 正在拉起新进程: %s", " ".join(command))
    subprocess.Popen(command)


_coordinator: Optional[RestartCoordinator] = None
_coordinator_lock = threading.Lock()


def get_restart_coordinator() -> RestartCoordinator:
    global _coordinator
    with _coordinator_lock:
        if _coordinator is None:
            from unilabos.server.backend.composition import (
                get_execution_backend,
                get_scheduler,
            )

            _coordinator = RestartCoordinator(
                get_execution_backend,
                get_scheduler,
            )
        return _coordinator


__all__ = [
    "RestartCoordinator",
    "get_restart_coordinator",
    "is_restart_requested",
    "spawn_replacement_process",
]
