"""开源串行库的 Python 传输适配；队列选择与准入策略在 Rust 内执行。"""

from pathlib import Path

from unilabos.scheduling.plugin import PluginTransport

QUEUE_CONFIG = {
    "queue_order": "fifo",
    "paused_task": "hold",
    "failed_task": "advance_after_settlement",
    "canceled_task": "advance_after_settlement",
    "manual_outside_scope": "reject",
}


class QueueEngine:
    """保留调用方的字节接口，每个适配器使用独立原生 worker。"""

    engine_id = "unilab.queue"
    config_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {key: {"const": value} for key, value in QUEUE_CONFIG.items()},
        "required": list(QUEUE_CONFIG),
    }

    def __init__(self, manifest: str | Path | None = None) -> None:
        self._manifest = Path(manifest) if manifest is not None else Path(__file__).with_name("queue_native") / "plugin.json"
        self._transport: PluginTransport | None = None

    def call(self, request: bytes, *, timeout_ms: int) -> bytes:
        if self._transport is None:
            self._transport = PluginTransport(self._manifest)
        return self._transport.call(request, timeout_ms=timeout_ms)

    def shutdown(self) -> None:
        if getattr(self, "_transport", None) is not None:
            self._transport.shutdown()

    def __del__(self) -> None:
        self.shutdown()
