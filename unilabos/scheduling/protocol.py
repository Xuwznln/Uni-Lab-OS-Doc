"""调度引擎的 JSON 值协议；此模块不访问运行时、驱动或数据库。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

PROTOCOL = "unilab.scheduler"
SCHEMA_VERSION = "1.0"
CONTRACT_DIR = Path(__file__).with_name("contract")
CONTRACT_SHA256 = "972d73fe8ee3c03d4ca91c19699d3d00db67b566d5b28550d708405d20a31a13"
MAX_WIRE_BYTES = 16 * 1024 * 1024
MAX_COUNTER = 2**64 - 1
DEFAULT_TIMEOUT_MS = 5000
DEFAULT_COMPUTE_BUDGET_MS = 500
DEFAULT_APPLY_BUDGET_MS = 2000

DEFAULT_LIMITS = {
    "max_request_bytes": MAX_WIRE_BYTES,
    "max_response_bytes": MAX_WIRE_BYTES,
    "max_tasks": 10000,
    "max_nodes": 100000,
    "max_options": 200000,
    "max_feedback_events": 1000,
    "max_state_bytes": 4 * 1024 * 1024,
}


class EngineError(Exception):
    """可跨 Python/native 边界保留错误分类，文本不含执行能力。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        origin: str = "host",
        retryable: bool = False,
        field_path: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.origin = origin
        self.retryable = retryable
        self.field_path = field_path
        self.details = dict(details or {})

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "origin": self.origin,
            "retryable": self.retryable,
            "field_path": self.field_path,
            "details": self.details,
        }


class EngineTransport(Protocol):
    """传输只携带 UTF-8 JSON；引擎无法取得宿主可变对象。"""

    def call(self, request: bytes, *, timeout_ms: int) -> bytes: ...

    def shutdown(self) -> None: ...


def require(condition: bool, code: str, message: str, **kwargs: Any) -> None:
    if not condition:
        raise EngineError(code, message, **kwargs)


def _portable_json(value: Any) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON 对象键必须是字符串")
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, int) and not isinstance(item, bool):
            if abs(item) > 2**53 - 1:
                raise ValueError("整数超过跨语言精确范围，请使用协议 Counter")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("JSON 数值必须有限")


def encode_message(value: Any, *, max_bytes: int = MAX_WIRE_BYTES) -> bytes:
    """固定编码供幂等摘要和跨语言传输使用，拒绝非 JSON 数值。"""
    try:
        wire = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        _portable_json(value)
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise EngineError("INVALID_REQUEST", "内容不是有限的 JSON 值") from error
    require(len(wire) <= max_bytes, "LIMIT_EXCEEDED", "消息超过字节上限")
    return wire


def decode_message(wire: bytes, *, max_bytes: int = MAX_WIRE_BYTES) -> Any:
    """长度先于解析校验；重复键和 NaN 不允许在不同语言中产生不同解释。"""
    require(isinstance(wire, bytes), "INVALID_REQUEST", "消息必须为字节串")
    require(len(wire) <= max_bytes, "LIMIT_EXCEEDED", "消息超过字节上限")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("重复 JSON 键")
            result[key] = value
        return result

    def invalid_constant(value: str) -> Any:
        raise ValueError(f"非有限 JSON 数值：{value}")

    try:
        result = json.loads(
            wire.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant
        )
        _portable_json(result)
        return result
    except (ValueError, UnicodeError, RecursionError) as error:
        raise EngineError("INVALID_REQUEST", "消息不是有效 UTF-8 JSON") from error


@lru_cache(maxsize=32)
def validator(type_name: str | None = None, *, manifest: bool = False) -> Draft202012Validator:
    name = "scheduler-engine-v1-manifest.schema.json" if manifest else "scheduler-engine-v1.schema.json"
    schema = json.loads((CONTRACT_DIR / name).read_bytes())
    if type_name is not None:
        schema = {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{type_name}",
        }
    return Draft202012Validator(schema)


def validate_value(value: Any, type_name: str | None = None, *, code: str = "INVALID_REQUEST") -> None:
    """校验严格字段集合；对象中的 extensions 是唯一通用扩展位置。"""
    encode_message(value)
    error = next(validator(type_name).iter_errors(value), None)
    if error is not None:
        raise EngineError(
            code,
            "消息不符合调度引擎协议",
            field_path="/" + "/".join(str(part) for part in error.absolute_path),
            details={"validation": error.message},
        )


def validate_message(message: Any, type_name: str | None = None) -> None:
    require(isinstance(message, dict), "INVALID_REQUEST", "消息必须为对象")
    require(
        message.get("schema_version") == SCHEMA_VERSION,
        "UNSUPPORTED_SCHEMA",
        "不支持调度协议版本",
    )
    validate_value(message, type_name)


def counter(value: str) -> int:
    """uint64 计数器通过十进制字符串传输，不能在 JS 中丢失精度。"""
    if (
        not isinstance(value, str)
        or len(value) > 20
        or not (value == "0" or (value[:1] != "0" and value.isdigit()))
    ):
        raise EngineError("INVALID_REQUEST", "消息不符合调度引擎协议")
    result = int(value)
    require(result <= MAX_COUNTER, "INVALID_REQUEST", "计数器超出 uint64")
    return result


def pack_state(value: Any, *, format: str, max_bytes: int) -> dict[str, Any]:
    raw = encode_message(value, max_bytes=max_bytes)
    return {
        "format": format,
        "data_base64": base64.b64encode(raw).decode("ascii"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def state_bytes(payload: dict[str, Any], *, formats: set[str], max_bytes: int) -> bytes:
    """校验 opaque 原始字节；公共协议不要求算法状态采用 JSON。"""
    validate_value(payload, "EnginePayload", code="STATE_CORRUPT")
    require(payload["format"] in formats, "STATE_INCOMPATIBLE", "不支持检查点格式")
    require(
        len(payload["data_base64"]) <= 4 * ((max_bytes + 2) // 3),
        "LIMIT_EXCEEDED",
        "检查点超过字节上限",
    )
    try:
        raw = base64.b64decode(payload["data_base64"], validate=True)
    except (ValueError, binascii.Error) as error:
        raise EngineError("STATE_CORRUPT", "检查点 Base64 损坏") from error
    require(len(raw) <= max_bytes, "LIMIT_EXCEEDED", "检查点超过字节上限")
    require(
        hashlib.sha256(raw).hexdigest() == payload["sha256"],
        "STATE_CORRUPT",
        "检查点摘要不一致",
    )
    return raw


def unpack_state(payload: dict[str, Any], *, formats: set[str], max_bytes: int) -> Any:
    """内置 Python 引擎使用 JSON 状态格式，原生插件可以使用自己的格式。"""
    raw = state_bytes(payload, formats=formats, max_bytes=max_bytes)
    try:
        return decode_message(raw, max_bytes=max_bytes)
    except EngineError as error:
        raise EngineError("STATE_CORRUPT", "检查点不是有效 JSON") from error


def success(request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: request[key] for key in ("protocol", "schema_version", "operation", "request_id")},
        "ok": True,
        "result": result,
    }


def failure(request: Any, error: EngineError) -> dict[str, Any]:
    # 只有已验证的标识才可以回显；解析失败的标识一律为空。
    identity: dict[str, Any] = {}
    for name, type_name in (("request_id", "Uuid"),):
        value = request.get(name) if isinstance(request, dict) else None
        identity[name] = value if validator(type_name).is_valid(value) else None
    operation = request.get("operation") if isinstance(request, dict) else None
    return {
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "operation": operation if operation in {"describe", "create", "decide", "close"} else None,
        **identity,
        "ok": False,
        "error": error.payload(),
    }
