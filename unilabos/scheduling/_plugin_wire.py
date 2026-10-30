"""宿主与独立 worker 的有限长度帧；设备和数据库对象不跨进程。"""

from __future__ import annotations

import socket
import struct
import time

from unilabos.scheduling.protocol import MAX_WIRE_BYTES, EngineError, require


def _budget(channel: socket.socket, deadline: float | None) -> None:
    remaining = None if deadline is None else deadline - time.monotonic()
    require(remaining is None or remaining > 0, "DEADLINE_EXCEEDED", "引擎调用超过截止时间")
    channel.settimeout(remaining)


def send_frame(channel: socket.socket, payload: bytes, deadline: float | None = None) -> None:
    require(len(payload) <= MAX_WIRE_BYTES + 1, "LIMIT_EXCEEDED", "worker 帧超过字节上限")
    _budget(channel, deadline)
    channel.sendall(struct.pack("!Q", len(payload)) + payload)


def receive_frame(channel: socket.socket, deadline: float | None = None) -> bytes:
    def read_exact(count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            _budget(channel, deadline)
            chunk = channel.recv(min(count - len(result), 64 * 1024))
            if not chunk:
                raise EngineError("ENGINE_UNAVAILABLE", "引擎 worker 已退出或断开")
            result.extend(chunk)
        return bytes(result)

    length, = struct.unpack("!Q", read_exact(8))
    require(1 <= length <= MAX_WIRE_BYTES + 1, "LIMIT_EXCEEDED", "worker 响应长度非法")
    return read_exact(length)
