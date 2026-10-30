"""设备适配器显式报告可信开始证据；通用执行包装器不推断物理开始。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID

_REPORTER: ContextVar[ExecutionStartReporter | None] = ContextVar("unilab_execution_start_reporter", default=None)


@dataclass(frozen=True)
class ExecutionStartReporter:
    """在派发时绑定不可变尝试身份，设备只提供当前命令的观测证据。"""

    job_uuid: str
    command_uuid: str
    attempt: int
    execution_instance_id: str
    commit: Callable[[dict[str, Any]], Any]

    def __call__(self, *, receipt_id: str, started_at_ms: int, source_ref: str) -> None:
        receipt = {
            "receipt_id": receipt_id, "job_uuid": self.job_uuid, "command_uuid": self.command_uuid,
            "attempt": self.attempt, "execution_instance_id": self.execution_instance_id,
            "started_at_ms": started_at_ms, "source_ref": source_ref,
        }
        validate_start_receipt(receipt)
        self.commit(receipt)


@contextmanager
def execution_start_reporting(reporter: ExecutionStartReporter | None) -> Iterator[None]:
    """在实际驱动调用上下文中安装报告器；跨线程依赖已有上下文传递。"""
    token = _REPORTER.set(reporter)
    try:
        yield
    finally:
        _REPORTER.reset(token)


def report_execution_started(*, receipt_id: str, started_at_ms: int, source_ref: str) -> None:
    """由设备适配器在取得当前命令的启动回执后调用；同一回执重报须保持字段一致。"""
    reporter = _REPORTER.get()
    if reporter is None:
        raise RuntimeError("当前驱动调用没有实际开始报告器")
    reporter(receipt_id=receipt_id, started_at_ms=started_at_ms, source_ref=source_ref)


def validate_start_receipt(receipt: dict[str, Any]) -> None:
    """拒绝身份、证据引用或时间不完整的回执，不补猜字段。"""
    if not isinstance(receipt, dict) or set(receipt) != {
        "receipt_id", "job_uuid", "command_uuid", "attempt",
        "execution_instance_id", "started_at_ms", "source_ref",
    }:
        raise ValueError("实际开始回执字段不完整")
    for field in ("receipt_id", "job_uuid", "command_uuid", "execution_instance_id"):
        value = receipt[field]
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("实际开始回执身份无效：" + field)
    for field in ("attempt", "started_at_ms"):
        value = receipt[field]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 9007199254740991:
            raise ValueError("实际开始回执数值无效：" + field)
    source = receipt["source_ref"]
    if not isinstance(source, str) or not source.strip() or len(source) > 4096:
        raise ValueError("实际开始回执必须包含当前命令的证据引用")
