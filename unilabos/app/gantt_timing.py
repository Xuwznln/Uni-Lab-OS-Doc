"""[临时调试] 甘特图回传链路耗时埋点。

用于分析 scheduler 下发 ``device_info`` 消息后，edge 侧到最终调用后端
``/api/v1/edge/job/result`` 接口的全链路耗时，以及中途各 LIMS 接口
(order-list / gantts-by-order-id / gantt-with-simulation-by-order-id) 的耗时。

每个 ``device_info``（每个 ``uuid``）单独写一个日志文件：
``<repo_root>/gantt_timing/gantt_timing_<uuid>.log``。
分析完成后可整体删除本文件及调用点（见 plan 文档末尾说明）。
"""

import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime


def _now_str() -> str:
    """当前墙钟时间，精确到毫秒。"""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "gantt_timing",
)

_loggers = {}
_loggers_lock = threading.Lock()
_starts = {}
_starts_lock = threading.Lock()


def _safe_name(uuid: str) -> str:
    """把 uuid 转成安全的文件名片段。"""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(uuid)) or "unknown"


def _get_logger(uuid: str):
    with _loggers_lock:
        lg = _loggers.get(uuid)
        if lg is not None:
            return lg
        os.makedirs(_LOG_DIR, exist_ok=True)
        lg = logging.getLogger(f"gantt_timing.{uuid}")
        lg.setLevel(logging.INFO)
        lg.propagate = False
        if not lg.handlers:
            log_path = os.path.join(_LOG_DIR, f"gantt_timing_{_safe_name(uuid)}.log")
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s.%(msecs)03d | %(message)s", "%Y-%m-%d %H:%M:%S")
            )
            lg.addHandler(handler)
        _loggers[uuid] = lg
        return lg


def mark_received(uuid: str) -> None:
    """记录收到 device_info 触发的时刻（全链路计时起点）。"""
    arrived_at = _now_str()
    with _starts_lock:
        _starts[uuid] = (time.perf_counter(), arrived_at)
    _get_logger(uuid).info(
        f"uuid={uuid} | [收到 device_info] action 到达时刻={arrived_at} 全链路计时开始"
    )


def record(uuid: str, label: str, elapsed_ms: float, extra: str = "") -> None:
    """记录某一步骤的耗时。"""
    suffix = f" | {extra}" if extra else ""
    _get_logger(uuid).info(f"uuid={uuid} | {label} | 耗时={elapsed_ms:.1f}ms{suffix}")


@contextmanager
def timed(uuid: str, label: str, extra: str = ""):
    """上下文管理器：自动记录代码块耗时。"""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        record(uuid, label, elapsed_ms, extra)


def finish(uuid: str, summary: str = "") -> None:
    """记录全链路结束并计算从 mark_received 到现在的总耗时，并关闭该 uuid 的文件句柄。"""
    finished_at = _now_str()
    with _starts_lock:
        start = _starts.pop(uuid, None)
    lg = _get_logger(uuid)
    if start is None:
        lg.info(
            f"uuid={uuid} | [全链路结束] 完成时刻={finished_at} (无起点记录) {summary}"
        )
    else:
        t0, arrived_at = start
        total_ms = (time.perf_counter() - t0) * 1000
        suffix = f" | {summary}" if summary else ""
        lg.info(
            f"uuid={uuid} | [全链路结束] action 到达时刻={arrived_at} 调用api完成时刻={finished_at} "
            f"总耗时={total_ms:.1f}ms{suffix}"
        )
    # 关闭并释放该 uuid 的文件句柄，避免句柄长期占用
    with _loggers_lock:
        removed = _loggers.pop(uuid, None)
    if removed is not None:
        for h in list(removed.handlers):
            try:
                h.close()
            except Exception:
                pass
            removed.removeHandler(h)
