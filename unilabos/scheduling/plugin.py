"""批准契约、固定插件字节和独立 worker 装配；失败不自动换引擎。"""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from unilabos.scheduling._plugin_wire import receive_frame, send_frame
from unilabos.scheduling._windows_job import WindowsWorkerJob
from unilabos.scheduling.protocol import (
    CONTRACT_DIR,
    CONTRACT_SHA256,
    PROTOCOL,
    SCHEMA_VERSION,
    EngineError,
    decode_message,
    encode_message,
    require,
    validate_message,
    validate_value,
    validator,
)

MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
CONTRACT_MANIFEST = "scheduler-engine-v1-contract.manifest.json"
WORKER_STDERR_LIMIT = 16 * 1024
MAX_WORKER_RESPAWNS = 3
WORKER_RESPAWN_WINDOW_S = 60.0


def read_stderr_tail(path: Path | str, *, limit: int = WORKER_STDERR_LIMIT) -> str:
    """读取 worker stderr 文件末尾；即使文件很大也只读最后 limit 字节，且不会阻塞。"""
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            if size <= 0:
                return ""
            stream.seek(max(0, size - limit), os.SEEK_SET)
            return stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


_WORKER_ENV_KEYS = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USER", "USERPROFILE", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "PYTHONHASHSEED", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONNOUSERSITE",
    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
)


def contract_file_digest(raw: bytes) -> str:
    """契约文件按 LF 规范化后摘要，Windows CRLF 检出与仓库 LF 字节一致。"""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def _worker_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in _WORKER_ENV_KEYS}
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _manifest(value: Any, format: str) -> None:
    require(
        isinstance(value, dict) and value.get("format") == format and validator(manifest=True).is_valid(value),
        "INVALID_REQUEST", "装配清单结构不合法",
    )


def verify_contract(directory: Path, approved_sha256: str) -> dict[str, Any]:
    """批准值来自宿主配置，不能从待装配插件自报值产生。"""
    try:
        manifest = decode_message((directory / CONTRACT_MANIFEST).read_bytes())
        _manifest(manifest, "unilab.scheduler.contract-manifest.v1")
        payload = {key: value for key, value in manifest.items() if key != "contract_sha256"}
        require(
            hashlib.sha256(encode_message(payload)).hexdigest() == manifest["contract_sha256"] == approved_sha256,
            "UNSUPPORTED_SCHEMA", "契约清单不在宿主批准范围",
        )
        for name, digest in manifest["files"].items():
            require(
                contract_file_digest((directory / name).read_bytes()) == digest,
                "ENGINE_UNAVAILABLE", "契约文件摘要不一致", details={"file": name},
            )
        return manifest
    except OSError as error:
        raise EngineError("ENGINE_UNAVAILABLE", "契约文件缺失或不可读") from error


def _pin_artifact(source: Path, target: Path, digest: str) -> None:
    """从同一个已打开 fd 复制并验摘要；后续仅载入私有目录中的固定副本。"""
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode), "ENGINE_UNAVAILABLE", "插件载入物必须为普通文件")
        require(info.st_size <= MAX_ARTIFACT_BYTES, "LIMIT_EXCEEDED", "插件载入物过大")
        actual = hashlib.sha256()
        size = 0
        with target.open("xb") as destination:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                require(size <= MAX_ARTIFACT_BYTES, "LIMIT_EXCEEDED", "插件载入物过大")
                actual.update(chunk)
                destination.write(chunk)
        require(actual.hexdigest() == digest, "ENGINE_UNAVAILABLE", "插件载入物摘要不一致")
    target.chmod(0o400)


class PluginTransport:
    """独立 worker 传输；崩溃后可在 describe/rebuild 限额重启，宿主 shutdown 后不再拉起。"""

    def __init__(
        self, manifest_path: Path | str, *, contract_dir: Path = CONTRACT_DIR,
        approved_contract_sha256: str = CONTRACT_SHA256, startup_timeout_ms: int = 10000,
        required_capabilities: tuple[str, ...] = ("core.v1",),
        max_respawns: int = MAX_WORKER_RESPAWNS, respawn_window_s: float = WORKER_RESPAWN_WINDOW_S,
    ) -> None:
        self._lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._spawn_lock = threading.Lock()
        self._closed = False
        self._retired = False
        self._process_stopped = False
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._channel: socket.socket | None = None
        self._stderr_path: Path | None = None
        self._worker_job: WindowsWorkerJob | None = None
        self._respawn_times: list[float] = []
        self._manifest_path = Path(manifest_path)
        self._contract_dir = contract_dir
        self._approved_contract_sha256 = approved_contract_sha256
        self._startup_timeout_ms = startup_timeout_ms
        self._required_capabilities = required_capabilities
        self._max_respawns = max_respawns
        self._respawn_window_s = respawn_window_s
        try:
            self._launch_worker()
        except BaseException as error:
            self.shutdown()
            if isinstance(error, EngineError):
                error.origin = "adapter"
            if isinstance(error, TimeoutError):
                raise EngineError("DEADLINE_EXCEEDED", "引擎启动超过截止时间") from error
            if isinstance(error, OSError):
                raise EngineError("ENGINE_UNAVAILABLE", "插件载入失败") from error
            raise

    def _launch_worker(self, *, deadline: float | None = None) -> None:
        """核验契约与载入物并拉起新 worker；调用方负责失败清理。"""
        startup_deadline = time.monotonic() + self._startup_timeout_ms / 1000
        deadline = startup_deadline if deadline is None else min(deadline, startup_deadline)
        contract = verify_contract(self._contract_dir, self._approved_contract_sha256)
        path = self._manifest_path
        try:
            manifest = decode_message(path.read_bytes())
        except FileNotFoundError as error:
            raise EngineError("INVALID_REQUEST", "插件缺少装配清单") from error
        plugin_contract = manifest.get("contract") if isinstance(manifest, dict) else None
        if isinstance(plugin_contract, dict) and isinstance(plugin_contract.get("schema_version"), str):
            require(plugin_contract["schema_version"] == contract["schema_version"],
                    "UNSUPPORTED_SCHEMA", "插件协议版本与宿主不同")
        _manifest(manifest, "unilab.scheduler.plugin-manifest.v1")
        require(
            all(manifest["contract"][key] == contract[key] for key in manifest["contract"]),
            "UNSUPPORTED_SCHEMA", "插件草案修订或契约摘要与宿主不同",
        )
        self.manifest = manifest
        self._temporary = tempfile.TemporaryDirectory(prefix="unilab-scheduler-")
        self.artifact_path = Path(self._temporary.name) / manifest["artifact"]["path"]
        _pin_artifact(path.parent / manifest["artifact"]["path"], self.artifact_path, manifest["artifact"]["sha256"])
        child, listener = None, None
        try:
            if os.name != "posix":
                raise NotImplementedError
            self._channel, child = socket.socketpair()
            connector = [str(child.fileno())]
        except (AttributeError, NotImplementedError):
            # Windows 不支持 pass_fds；本机回环通道先认证，再允许载入插件。
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            nonce = uuid4().hex
            connector = ["tcp", str(listener.getsockname()[1]), nonce]
        # -I 禁用 PYTHONPATH 和当前目录自动导入；只加入宿主安装包的准确根目录。
        bootstrap = (
            f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r}); "
            "from unilabos.scheduling.plugin_worker import main; main()"
        )
        require(time.monotonic() < deadline, "DEADLINE_EXCEEDED", "插件装配已超过调用截止时间")
        # stderr 写入私有临时文件而非管道：逃逸孙进程即使占住 fd，关闭也不会阻塞。
        self._stderr_path = Path(self._temporary.name) / "worker.stderr"
        try:
            with open(self._stderr_path, "wb") as stderr_file:
                inherited: dict[str, Any] = {"pass_fds": (child.fileno(),)} if child is not None else {}
                self.process = subprocess.Popen(
                    [sys.executable, "-I", "-c", bootstrap, *connector,
                     str(self.artifact_path), manifest["artifact"]["kind"], manifest["artifact"]["sha256"]],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=stderr_file, cwd=self._temporary.name, start_new_session=os.name == "posix",
                    env=_worker_env(), **inherited,
                )
            if os.name == "nt":
                self._worker_job = WindowsWorkerJob(self.process)
            if listener is not None:
                listener.settimeout(max(0.001, deadline - time.monotonic()))
                self._channel, _ = listener.accept()
                require(receive_frame(self._channel, deadline) == b"H" + nonce.encode(),
                        "ENGINE_UNAVAILABLE", "worker 回环通道身份不匹配")
                send_frame(self._channel, b"G", deadline)
        finally:
            if child is not None:
                child.close()
            if listener is not None:
                listener.close()
        self._receive(deadline)
        request = {
            "protocol": PROTOCOL, "schema_version": SCHEMA_VERSION, "operation": "describe",
            "request_id": str(uuid4()), "timeout_ms": self._startup_timeout_ms,
            "body": {"host_version": "unilab.scheduler.r3", "supported_schema_versions": [SCHEMA_VERSION],
                     "required_capabilities": list(self._required_capabilities)},
        }
        remaining = max(1, int((deadline - time.monotonic()) * 1000))
        wire = encode_message(request)
        response = decode_message(self._communicate(request, wire, timeout_ms=remaining))
        if not response["ok"]:
            raise EngineError(**response["error"])
        description = response["result"]
        require(all(description[key] == manifest[key] for key in ("engine_id", "engine_version", "build_id")),
                "ENGINE_UNAVAILABLE", "describe 身份与插件清单不一致")
        require(SCHEMA_VERSION in description["supported_schema_versions"], "UNSUPPORTED_SCHEMA", "没有共同协议")
        require(set(self._required_capabilities) <= set(description["capabilities"]),
                "UNSUPPORTED_CAPABILITY", "插件缺少必需能力")
        self.description = description
        self._closed = False

    def _receive(self, deadline: float) -> bytes:
        if self._channel is None:
            raise EngineError("ENGINE_UNAVAILABLE", "引擎 worker 未连接")
        frame = receive_frame(self._channel, deadline)
        if frame[:1] == b"E":
            error = decode_message(frame[1:])
            validate_value(error, "Error")
            raise EngineError(**error)
        require(frame[:1] == b"R", "INVALID_REQUEST", "worker 响应帧类型非法")
        return frame[1:]

    def _communicate(self, parsed: dict[str, Any], request: bytes, *, timeout_ms: int) -> bytes:
        channel = self._channel
        if channel is None:
            raise EngineError("ENGINE_UNAVAILABLE", "引擎 worker 未连接")
        deadline = time.monotonic() + min(timeout_ms, parsed["timeout_ms"]) / 1000
        send_frame(channel, b"Q" + request, deadline)
        wire = self._receive(deadline)
        response = decode_message(wire)
        validate_message(response)
        require(all(response[key] == parsed[key] for key in ("protocol", "schema_version", "operation", "request_id")),
                "INVALID_DECISION", "插件响应身份不匹配")
        if response["ok"]:
            validate_value(response, parsed["operation"].capitalize() + "Response", code="INVALID_DECISION")
        require(time.monotonic() <= deadline, "DEADLINE_EXCEEDED", "响应校验后已超过调用截止时间")
        return wire

    def _respawn(self, deadline: float) -> None:
        require(self._spawn_lock.acquire(timeout=max(0, deadline - time.monotonic())),
                "DEADLINE_EXCEEDED", "等待重建 worker 已超过调用截止时间")
        try:
            require(not self._retired, "ENGINE_UNAVAILABLE", "插件传输已关闭")
            if not self._closed and self._channel is not None:
                return
            now = time.monotonic()
            self._respawn_times = [t for t in self._respawn_times if now - t < self._respawn_window_s]
            require(
                len(self._respawn_times) < self._max_respawns,
                "ENGINE_UNAVAILABLE", "插件 worker 重启次数超过限制",
            )
            self._respawn_times.append(now)
            self._abandon_worker()
            self.process = None
            self._process_stopped = False
            try:
                self._launch_worker(deadline=deadline)
            except BaseException:
                self._closed = True
                self._abandon_worker()
                raise
        finally:
            self._spawn_lock.release()

    def _abandon_worker(self) -> None:
        """超时/崩溃只回收当前 worker，不阻止后续 describe 限额重启。"""
        with self._shutdown_lock:
            self._closed = True
            self._teardown_locked()

    def call(self, request: bytes, *, timeout_ms: int) -> bytes:
        started = time.monotonic()
        parsed = decode_message(request)
        require(isinstance(parsed, dict), "INVALID_REQUEST", "消息必须为对象")
        require(
            parsed.get("schema_version") == SCHEMA_VERSION,
            "UNSUPPORTED_SCHEMA", "不支持调度协议版本",
        )
        require(
            isinstance(parsed.get("timeout_ms"), int) and not isinstance(parsed.get("timeout_ms"), bool),
            "INVALID_REQUEST", "调用缺少 timeout_ms",
        )
        require(not self._retired, "ENGINE_UNAVAILABLE", "插件传输已关闭")
        deadline = started + min(timeout_ms, parsed["timeout_ms"]) / 1000
        require(self._lock.acquire(blocking=False), "INSTANCE_BUSY", "worker 已有调用正在执行", retryable=True)
        try:
            if self._closed or self._channel is None:
                require(parsed.get("operation") == "describe", "ENGINE_UNAVAILABLE", "插件传输已关闭")
                self._respawn(deadline)
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            require(remaining_ms > 0, "DEADLINE_EXCEEDED", "worker 重建后已超过调用截止时间")
            return self._communicate(parsed, request, timeout_ms=remaining_ms)
        except BaseException as error:
            self._abandon_worker()
            if isinstance(error, EngineError):
                error.origin = "adapter"
            if isinstance(error, TimeoutError):
                raise EngineError("DEADLINE_EXCEEDED", "引擎调用超过截止时间") from error
            if isinstance(error, OSError):
                raise EngineError("ENGINE_UNAVAILABLE", "引擎 worker 连接失败") from error
            raise
        finally:
            self._lock.release()

    def shutdown(self) -> None:
        with self._spawn_lock:
            with self._shutdown_lock:
                self._retired = True
                self._closed = True
                self._teardown_locked()

    def _teardown_locked(self) -> None:
        channel, self._channel = self._channel, None
        process = self.process
        stderr_path, self._stderr_path = self._stderr_path, None
        temporary, self._temporary = self._temporary, None
        if channel is not None:
            try:
                channel.close()
            except OSError:
                pass
        try:
            if process is not None and not self._process_stopped:
                self._process_stopped = True
                # worker 由本适配器建立独立进程组，超时也回收其求解子进程。
                try:
                    if self._worker_job is not None:
                        self._worker_job.close()
                        self._worker_job = None
                    elif os.name == "posix":
                        # 组长已退出时，其普通求解子进程仍可能占用同一进程组。
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                except (PermissionError, AttributeError, OSError):
                    try:
                        process.kill()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=2)
                except Exception:
                    pass
                if stderr_path is not None:
                    text = read_stderr_tail(stderr_path).strip()
                    if text:
                        logging.getLogger(__name__).warning(
                            "调度插件 worker 标准错误（截断保留）：\n%s", text,
                        )
        finally:
            if temporary is not None:
                try:
                    temporary.cleanup()
                except Exception:
                    pass
