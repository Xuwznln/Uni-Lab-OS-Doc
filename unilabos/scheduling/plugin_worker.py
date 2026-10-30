"""仅在独立子进程运行插件；原生崩溃与非法内存访问由宿主隔离。"""

from __future__ import annotations

import ctypes
import hashlib
import socket
import sys
import zipimport
from pathlib import Path
from typing import Any

from unilabos.scheduling._plugin_wire import receive_frame, send_frame
from unilabos.scheduling.protocol import (
    MAX_WIRE_BYTES,
    EngineError,
    decode_message,
    encode_message,
    require,
    validate_message,
)


class NativeLibrary:
    """响应复制前检查长度，始终用产生响应的库释放同一缓冲区。"""

    def __init__(self, path: Path) -> None:
        self.library = ctypes.CDLL(str(path))
        self.functions: dict[str, Any] = {}
        pointer = ctypes.POINTER(ctypes.c_uint8)
        for operation in ("describe", "create", "decide", "close"):
            function = getattr(self.library, "uni_sched_" + operation)
            function.argtypes = [pointer, ctypes.c_uint64, ctypes.POINTER(pointer), ctypes.POINTER(ctypes.c_uint64)]
            function.restype = ctypes.c_int32
            self.functions[operation] = function
        self.free = self.library.uni_sched_free_buffer
        self.free.argtypes = [pointer, ctypes.c_uint64]
        self.free.restype = None
        abi = self.library.uni_sched_abi_version
        abi.argtypes = []
        abi.restype = ctypes.c_uint32
        require(abi() == 0x00010000, "ENGINE_UNAVAILABLE", "原生引擎 ABI 不是受支持的 1.0")

    def call(self, request: bytes, *, timeout_ms: int) -> bytes:
        parsed = decode_message(request)
        validate_message(parsed)
        validate_message(parsed, parsed["operation"].capitalize() + "Request")
        source = (ctypes.c_uint8 * len(request)).from_buffer_copy(request)
        result = ctypes.POINTER(ctypes.c_uint8)()
        length = ctypes.c_uint64()
        status = self.functions[parsed["operation"]](source, len(request), ctypes.byref(result), ctypes.byref(length))
        try:
            require(status == 0, "ENGINE_UNAVAILABLE", "原生入口无法返回协议响应", details={"native_status": status})
            require(bool(result) and length.value > 0, "ENGINE_UNAVAILABLE", "原生入口返回空缓冲区")
            require(length.value <= MAX_WIRE_BYTES, "LIMIT_EXCEEDED", "原生响应超过字节上限")
            return ctypes.string_at(result, length.value)
        finally:
            if result:
                # 即使结构/长度非法，也不能改用宿主 free 或提前卸载库。
                self.free(result, length.value)


def main() -> None:
    if sys.argv[1] == "tcp":
        channel = socket.create_connection(("127.0.0.1", int(sys.argv[2])))
        send_frame(channel, b"H" + sys.argv[3].encode())
        # Windows 宿主先登记 Job Object，再放行插件载入，避免子进程抢先逃逸。
        require(receive_frame(channel) == b"G", "ENGINE_UNAVAILABLE", "宿主未批准插件载入")
        artifact, kind, digest = Path(sys.argv[4]), sys.argv[5], sys.argv[6]
    else:
        channel = socket.socket(fileno=int(sys.argv[1]))
        artifact, kind, digest = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    try:
        require(hashlib.sha256(artifact.read_bytes()).hexdigest() == digest, "ENGINE_UNAVAILABLE", "固定载入物摘要不一致")
        if kind == "native":
            engine = NativeLibrary(artifact)
        else:
            # 单文件 zip，入口必须来自已校验归档，不能由当前目录同名模块顶替。
            importer = zipimport.zipimporter(str(artifact))
            code = importer.get_code("scheduler_plugin")
            namespace: dict[str, Any] = {
                "__name__": "scheduler_plugin", "__file__": str(artifact) + "/scheduler_plugin.py",
            }
            sys.path.insert(0, str(artifact))
            exec(code, namespace)
            engine = namespace["create_engine"]()
            require(callable(getattr(engine, "call", None)), "ENGINE_UNAVAILABLE", "Python 插件缺少 call")
        send_frame(channel, b"R{}")
        while True:
            frame = receive_frame(channel)
            require(frame[:1] == b"Q", "INVALID_REQUEST", "worker 请求帧类型非法")
            request = frame[1:]
            parsed = decode_message(request)
            validate_message(parsed, parsed["operation"].capitalize() + "Request")
            response = engine.call(request, timeout_ms=parsed["timeout_ms"])
            require(isinstance(response, bytes), "INVALID_REQUEST", "插件响应必须为字节串")
            require(len(response) <= MAX_WIRE_BYTES, "LIMIT_EXCEEDED", "插件响应超过字节上限")
            send_frame(channel, b"R" + response)
    except BaseException as error:
        failure = error if isinstance(error, EngineError) else EngineError("ENGINE_UNAVAILABLE", "引擎 worker 无法完成调用")
        try:
            send_frame(channel, b"E" + encode_message(failure.payload()))
        except (OSError, EngineError):
            pass
    finally:
        channel.close()


if __name__ == "__main__":
    main()
