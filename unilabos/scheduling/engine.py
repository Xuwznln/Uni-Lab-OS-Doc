"""内置引擎共有的实例、幂等、配置和状态边界。"""

from __future__ import annotations

import hashlib
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from unilabos.scheduling.protocol import (
    DEFAULT_LIMITS,
    SCHEMA_VERSION,
    EngineError,
    counter,
    decode_message,
    encode_message,
    failure,
    pack_state,
    require,
    success,
    unpack_state,
    validate_message,
    validate_value,
)
from unilabos.scheduling.validation import SnapshotIndex, validate_decision, validate_snapshot


def reason(code: str, message: str, *, task_id: str | None = None, node: dict[str, str] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "task_id": task_id, "node": node, "resource_ids": []}


PREPARATION_ERROR_PREFIX = "vendor.unilab.preparation."


def preparation_error(node: dict[str, Any]) -> dict[str, Any] | None:
    """读取平台已确认的节点准备错误；它不代表可派发的资源候选。"""
    return next(
        (item for item in node["blockers"] if item["code"].startswith(PREPARATION_ERROR_PREFIX)),
        None,
    )


def validate_config(config: dict[str, Any], schema: dict[str, Any]) -> None:
    """配置 schema 只允许文内引用，不触发远程 schema 下载。"""
    pending: list[Any] = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for name in ("$ref", "$dynamicRef"):
                if name in value:
                    require(
                        isinstance(value[name], str) and value[name].startswith("#"),
                        "CONFIG_INVALID", "配置 schema 仅允许文内引用",
                    )
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    try:
        Draft202012Validator.check_schema(schema)
        valid = Draft202012Validator(schema).is_valid(config)
    except (SchemaError, ValueError) as error:
        raise EngineError("CONFIG_INVALID", "配置 schema 无效") from error
    require(valid, "CONFIG_INVALID", "引擎配置不符合 config_schema")


@dataclass
class _Instance:
    create_request: bytes
    create_response: dict[str, Any]
    body: dict[str, Any]
    closed: bool = False
    cache: dict[str, tuple[str, str, dict[str, Any]]] = field(default_factory=dict)


class JsonEngine:
    """子类只实现 decide_values；提交权始终在宿主。

    引擎不自行推进 checkpoint。每次计算从输入 checkpoint 恢复算法状态，
    未被宿主接受的候选不会污染下一轮。缓存只保留每类调用最近一次响应。
    """

    engine_id = ""
    engine_version = "1.0.0"
    build_id = "unilab-scheduler-r3"
    capabilities: tuple[str, ...] = ("core.v1",)
    state_format: str | None = None
    compatible_state_formats: tuple[str, ...] = ()
    config_schema: dict[str, Any] = {"type": "object", "additionalProperties": False}

    def __init__(self) -> None:
        self._instances: dict[str, _Instance] = {}
        self._closed_instances: dict[str, _Instance] = {}
        self._lock = threading.Lock()
        self._stopped = False

    @property
    def checkpoint_formats(self) -> tuple[str, ...]:
        """显式声明可读取的旧格式；新状态始终使用 state_format 写出。"""
        return tuple(dict.fromkeys((self.state_format, *self.compatible_state_formats))) if self.state_format else ()

    def describe(self, request: dict[str, Any]) -> dict[str, Any]:
        body = request["body"]
        require(SCHEMA_VERSION in body["supported_schema_versions"], "UNSUPPORTED_SCHEMA", "没有共同协议版本")
        require(set(body["required_capabilities"]) <= set(self.capabilities), "UNSUPPORTED_CAPABILITY", "缺少必需能力")
        return {
            "engine_id": self.engine_id, "engine_version": self.engine_version, "build_id": self.build_id,
            "supported_schema_versions": [SCHEMA_VERSION], "capabilities": list(self.capabilities),
            "config_schema": deepcopy(self.config_schema),
            "checkpoint_formats": list(self.checkpoint_formats),
            "limits": dict(DEFAULT_LIMITS),
        }

    def create(self, request: dict[str, Any], raw: bytes) -> dict[str, Any]:
        body = request["body"]
        require(body["instance_id"] not in self._closed_instances, "INSTANCE_CLOSED", "实例已关闭，不能复活")
        instance = self._instances.get(body["instance_id"])
        if instance is not None:
            require(instance.create_request == raw, "IDEMPOTENCY_CONFLICT", "实例身份已经绑定其他创建请求")
            require(not instance.closed, "INSTANCE_CLOSED", "实例已关闭")
            return deepcopy(instance.create_response)
        self._reap_closed()
        require(len(self._instances) < 1024, "LIMIT_EXCEEDED", "当前引擎实例数量超过上限")
        require(set(body["required_capabilities"]) <= set(self.capabilities), "UNSUPPORTED_CAPABILITY", "缺少必需能力")
        require(
            all(value <= DEFAULT_LIMITS[key] for key, value in body["limits"].items()),
            "LIMIT_EXCEEDED", "创建配额超过引擎上限",
        )
        validate_config(body["config"], self.config_schema)
        restore = body["restore"]
        counter(restore["feedback_base_seq"])
        if restore["mode"] == "resume":
            checkpoint = restore["checkpoint"]
            require(checkpoint is not None, "STATE_INCOMPATIBLE", "resume 缺少检查点")
            self._check_checkpoint(checkpoint, body)
            require(checkpoint["feedback_cursor"] == restore["feedback_base_seq"], "FEEDBACK_GAP", "恢复基线与检查点游标不一致")
        else:
            require(restore["checkpoint"] is None, "STATE_INCOMPATIBLE", "rebuild 不得携带旧检查点")
            checkpoint = {
                "revision": "0", "feedback_cursor": restore["feedback_base_seq"],
                "engine_id": self.engine_id, "config_id": body["config_id"], "state": None,
            }
        result = {
            **{key: body[key] for key in ("instance_id", "domain_id", "run_epoch", "config_id")},
            "capabilities": list(self.capabilities), "limits": deepcopy(body["limits"]),
            "recovery_mode": restore["mode"], "checkpoint": deepcopy(checkpoint), "warnings": [],
        }
        self._instances[body["instance_id"]] = _Instance(raw, deepcopy(result), deepcopy(body))
        return result

    def _reap_closed(self) -> None:
        """释放关闭实例的大状态，保留最小身份及最近 close 响应以满足幂等。"""
        for identity in [key for key, instance in self._instances.items() if instance.closed]:
            instance = self._instances.pop(identity)
            instance.create_request = b""
            instance.create_response = {}
            instance.body = {key: instance.body[key] for key in ("domain_id", "run_epoch", "limits")}
            instance.cache = {key: value for key, value in instance.cache.items() if key == "close"}
            self._closed_instances[identity] = instance

    def _check_checkpoint(self, checkpoint: dict[str, Any], creation: dict[str, Any]) -> Any:
        require(
            checkpoint["engine_id"] == self.engine_id and checkpoint["config_id"] == creation["config_id"],
            "STATE_INCOMPATIBLE", "检查点与引擎或配置不匹配",
        )
        counter(checkpoint["revision"])
        counter(checkpoint["feedback_cursor"])
        if checkpoint["state"] is None:
            return None
        return unpack_state(
            checkpoint["state"], formats=set(self.checkpoint_formats),
            max_bytes=creation["limits"]["max_state_bytes"],
        )

    def _instance(self, request: dict[str, Any], *, allow_closed: bool = False) -> _Instance:
        body = request["body"]
        instance = self._instances.get(body["instance_id"]) or self._closed_instances.get(body["instance_id"])
        if instance is None:
            raise EngineError("INSTANCE_NOT_FOUND", "引擎实例不存在")
        require(
            all(body[key] == instance.body[key] for key in ("domain_id", "run_epoch")),
            "STATE_INCOMPATIBLE", "实例调度域或运行世代不匹配",
        )
        require(allow_closed or not instance.closed, "INSTANCE_CLOSED", "实例已关闭")
        return instance

    def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        instance = self._instance(request)
        body = request["body"]
        facts = validate_snapshot(request, limits=instance.body["limits"], check_schema=False)
        state = self._check_checkpoint(body["checkpoint"], instance.body)
        choices, next_state = self.decide_values(request, facts, instance.body["config"], state)
        result = {
            "decision_id": request["request_id"],
            **{key: body[key] for key in ("instance_id", "domain_id", "run_epoch")},
            "base_snapshot_revision": body["snapshot"]["revision"],
            "base_checkpoint_revision": body["checkpoint"]["revision"],
            "consumed_feedback_seq": body["feedback"]["through_seq"],
            "next_state": pack_state(
                next_state, format=self.state_format,
                max_bytes=instance.body["limits"]["max_state_bytes"],
            ) if self.state_format is not None and next_state is not None else None,
            **choices,
        }
        validate_decision(
            request, success(request, result), capabilities=self.capabilities,
            state_formats=set(self.checkpoint_formats), limits=instance.body["limits"],
            check_schema=False,
        )
        return result

    def decide_values(
        self, request: dict[str, Any], facts: SnapshotIndex, config: dict[str, Any], state: Any
    ) -> tuple[dict[str, Any], Any]:
        raise NotImplementedError

    def close(self, request: dict[str, Any]) -> dict[str, Any]:
        instance = self._instance(request, allow_closed=True)
        instance.closed = True
        return {"instance_id": request["body"]["instance_id"], "status": "closed"}

    def call(self, request: bytes, *, timeout_ms: int) -> bytes:
        """统一传输入口：错误返回协议 envelope，异常不越过插件边界。"""
        parsed: Any = None
        acquired = False
        started = time.monotonic()
        try:
            parsed = decode_message(request)
            require(isinstance(parsed, dict), "INVALID_REQUEST", "消息必须为对象")
            operation = parsed.get("operation")
            require(
                operation in {"describe", "create", "decide", "close"},
                "INVALID_REQUEST", "不支持的调度操作",
            )
            validate_message(parsed, operation.capitalize() + "Request")
            acquired = self._lock.acquire(blocking=False)
            require(acquired, "INSTANCE_BUSY", "引擎已有调用正在执行", retryable=True)
            require(not self._stopped, "ENGINE_UNAVAILABLE", "引擎传输已关闭")
            instance = None
            digest = hashlib.sha256(request).hexdigest()
            if operation in {"decide", "close"}:
                instance = self._instance(parsed, allow_closed=operation == "close")
                require(len(request) <= instance.body["limits"]["max_request_bytes"], "LIMIT_EXCEEDED", "请求超过协商配额")
                for _, (identity, previous_digest, cached) in instance.cache.items():
                    if identity == parsed["request_id"]:
                        require(digest == previous_digest, "IDEMPOTENCY_CONFLICT", "同一请求身份的内容不同")
                        return encode_message(cached, max_bytes=instance.body["limits"]["max_response_bytes"])
            if operation == "create":
                result = self.create(parsed, request)
            else:
                result = getattr(self, operation)(parsed)
            require(
                (time.monotonic() - started) * 1000 <= min(timeout_ms, parsed["timeout_ms"]),
                "DEADLINE_EXCEEDED", "引擎调用超过预算", retryable=True,
            )
            response = success(parsed, result)
            validate_value(response, operation.capitalize() + "Response", code="ENGINE_INTERNAL")
            wire = encode_message(
                response, max_bytes=instance.body["limits"]["max_response_bytes"] if instance else DEFAULT_LIMITS["max_response_bytes"],
            )
            if instance is not None:
                instance.cache[operation] = (parsed["request_id"], digest, response)
            return wire
        except EngineError as error:
            error.origin = "engine"
            return encode_message(failure(parsed, error))
        except ImportError as error:
            return encode_message(failure(parsed, EngineError(
                "ENGINE_UNAVAILABLE", "调度引擎缺少必需依赖", origin="engine",
                details={"dependency": getattr(error, "name", None)},
            )))
        except Exception:
            # 不向插件调用者泄露 traceback 或本地路径，宿主将停止普通推进。
            return encode_message(failure(parsed, EngineError("ENGINE_INTERNAL", "引擎内部异常", origin="engine")))
        finally:
            if acquired:
                self._lock.release()

    def shutdown(self) -> None:
        with self._lock:
            self._stopped = True
            self._instances.clear()
