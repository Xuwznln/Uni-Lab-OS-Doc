"""平台调度会话：校验插件响应、提交决策账和管理恢复屏障。"""

from __future__ import annotations

import hashlib
import threading
import time
from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from unilabos.scheduling.engine import reason, validate_config
from unilabos.scheduling.protocol import (
    DEFAULT_APPLY_BUDGET_MS,
    DEFAULT_COMPUTE_BUDGET_MS,
    DEFAULT_LIMITS,
    DEFAULT_TIMEOUT_MS,
    PROTOCOL,
    SCHEMA_VERSION,
    EngineError,
    EngineTransport,
    decode_message,
    encode_message,
    require,
    state_bytes,
    validate_message,
    validate_value,
)
from unilabos.scheduling.validation import validate_decision, validate_snapshot
from unilabos.workflow.scheduling_journal import SchedulingJournal

MAX_DEADLINE_RETRIES = 2
MAX_STALE_RETRIES = 2


def scale_call_budget_ms(base_timeout_ms: int, *, node_count: int, option_count: int = 0) -> int:
    """按快照规模抬高调用时限，避免默认 5 s 在大图上误伤整站。"""
    sized = 3000 + node_count * 20 + option_count * 2
    return max(1, min(600000, max(base_timeout_ms, sized)))


def scale_compute_budget_ms(base_compute_ms: int, timeout_ms: int, *, node_count: int) -> int:
    """计算预算不超过调用时限，并随节点数放大。"""
    sized = max(base_compute_ms, node_count * 10)
    return max(1, min(timeout_ms, sized))


def stale_retry_delay_ms(streak: int) -> int:
    """引擎自报过期后的指数退避，下限 100ms、上限 2s。"""
    return min(2000, 100 * (2 ** max(0, streak - 1)))


def engine_reported_stale(error: EngineError) -> bool:
    """引擎或适配器自报过期，区别于宿主因事实变化而拒绝。"""
    return error.origin in {"engine", "adapter"}


class SchedulingSession:
    """仅通过值协议调用一个引擎；配置或引擎变更必须重新启动会话。

    实际执行仍由调用方完成。decide 的返回仅代表 D，调用方必须在真实准入事务
    中登记 A，并在调用设备前通过一次性开始闸门 S。
    """

    def __init__(
        self, journal: SchedulingJournal, transport: EngineTransport, *,
        domain_id: str, engine_id: str, config: dict[str, Any],
        config_id: str | None = None, limits: dict[str, int] | None = None,
        required_capabilities: tuple[str, ...] = ("core.v1",),
        timeout_ms: int = DEFAULT_TIMEOUT_MS, compute_budget_ms: int = DEFAULT_COMPUTE_BUDGET_MS,
        apply_budget_ms: int = DEFAULT_APPLY_BUDGET_MS,
        monotonic=time.monotonic, wall_time=time.time,
        max_deadline_retries: int = MAX_DEADLINE_RETRIES,
        max_stale_retries: int = MAX_STALE_RETRIES,
    ) -> None:
        self.journal, self.transport = journal, transport
        self.domain_id, self.engine_id = domain_id, engine_id
        self.config = deepcopy(config)
        digest = hashlib.sha256(encode_message({"engine_id": engine_id, "config": config})).hexdigest()
        self.config_id = config_id or str(uuid5(NAMESPACE_URL, f"unilab.scheduler.config:{digest}"))
        self.instance_id, self.run_epoch = str(uuid4()), str(uuid4())
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        validate_value(self.limits, "Limits")
        self.required_capabilities = required_capabilities
        self.timeout_ms, self.compute_budget_ms, self.apply_budget_ms = timeout_ms, compute_budget_ms, apply_budget_ms
        self.max_deadline_retries = max_deadline_retries
        self.max_stale_retries = max_stale_retries
        self._monotonic, self._wall_time = monotonic, wall_time
        self._lock = threading.RLock()
        self._started = self._closed = False
        self.description: dict[str, Any] | None = None
        self.last_error: EngineError | None = None
        self.forecast: dict[str, Any] | None = None
        self._deadline_streak = 0
        self._stale_streak = 0

    def _request(self, operation: str, body: dict[str, Any], *, timeout_ms: int | None = None) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL, "schema_version": SCHEMA_VERSION, "operation": operation,
            "request_id": str(uuid4()), "timeout_ms": self.timeout_ms if timeout_ms is None else timeout_ms,
            "body": body,
        }

    def _identity(self) -> dict[str, str]:
        return {"instance_id": self.instance_id, "domain_id": self.domain_id, "run_epoch": self.run_epoch}

    def _exchange(self, request: dict[str, Any], *, check_schema: bool = True) -> dict[str, Any]:
        if check_schema:
            validate_message(request, request["operation"].capitalize() + "Request")
        timeout_ms = request["timeout_ms"]
        wire = encode_message(request, max_bytes=self.limits["max_request_bytes"])
        started = self._monotonic()
        try:
            response = decode_message(
                self.transport.call(wire, timeout_ms=timeout_ms),
                max_bytes=self.limits["max_response_bytes"],
            )
        except EngineError:
            raise
        except Exception as error:
            raise EngineError("ENGINE_UNAVAILABLE", "调度引擎传输异常") from error
        require(
            (self._monotonic() - started) * 1000 <= timeout_ms,
            "DEADLINE_EXCEEDED", "迟到的引擎响应不能接受", retryable=True,
        )
        if check_schema:
            validate_message(response)
        require(
            all(response[key] == request[key] for key in ("protocol", "schema_version", "operation", "request_id")),
            "INVALID_DECISION", "引擎响应身份不匹配",
        )
        if not response["ok"]:
            raise EngineError(**response["error"])
        if check_schema:
            validate_value(response, request["operation"].capitalize() + "Response", code="INVALID_DECISION")
        return response

    def fault(self, error: EngineError) -> None:
        """保留诊断并立即撤销新推进能力；平台结果及结算入口不受影响。"""
        self.last_error = error
        self.forecast = None
        # start 在描述或配置阶段失败时可能尚无域记录；不得据此覆盖原错误。
        try:
            self.journal.set_lifecycle(self.domain_id, self.run_epoch, "engine_fault")
        except EngineError as marking_error:
            if marking_error.code not in {"INSTANCE_NOT_FOUND", "STALE_DECISION"}:
                raise

    def start(self, *, rebuild: bool = False, allow_model_reset: bool = False) -> dict[str, Any]:
        with self._lock:
            require(not self._started and not self._closed, "INSTANCE_BUSY", "调度会话已经启动或关闭")
            try:
                description = self._exchange(self._request("describe", {
                    "host_version": "unilab.scheduler.r3", "supported_schema_versions": [SCHEMA_VERSION],
                    "required_capabilities": list(self.required_capabilities),
                }))["result"]
                require(description["engine_id"] == self.engine_id, "STATE_INCOMPATIBLE", "描述与装配引擎身份不同")
                require(SCHEMA_VERSION in description["supported_schema_versions"], "UNSUPPORTED_SCHEMA", "没有共同协议版本")
                require(
                    set(self.required_capabilities) <= set(description["capabilities"]),
                    "UNSUPPORTED_CAPABILITY", "引擎未提供必需能力",
                )
                validate_config(self.config, description["config_schema"])
                self.limits = {key: min(value, description["limits"][key]) for key, value in self.limits.items()}
                creation = {
                    **self._identity(), "config_id": self.config_id, "config": self.config,
                    "required_capabilities": list(self.required_capabilities), "limits": self.limits,
                    "restore": {"mode": "rebuild", "checkpoint": None, "feedback_base_seq": "0", "allow_model_reset": allow_model_reset},
                }
                view = self.journal.start(creation, engine_id=self.engine_id, rebuild=rebuild)
                checkpoint = view["checkpoint"]
                if checkpoint["state"] is not None:
                    state_bytes(
                        checkpoint["state"], formats=set(description["checkpoint_formats"]),
                        max_bytes=self.limits["max_state_bytes"],
                    )
                creation["restore"] = {
                    "mode": view["restore_mode"],
                    "checkpoint": checkpoint if view["restore_mode"] == "resume" else None,
                    "feedback_base_seq": checkpoint["feedback_cursor"], "allow_model_reset": allow_model_reset,
                }
                created = self._exchange(self._request("create", creation))["result"]
                require(
                    all(created[key] == creation[key] for key in ("instance_id", "domain_id", "run_epoch", "config_id"))
                    and created["checkpoint"] == checkpoint and created["recovery_mode"] == view["restore_mode"],
                    "STATE_INCOMPATIBLE", "创建结果改变了宿主恢复基线",
                )
                require(
                    set(self.required_capabilities) <= set(created["capabilities"]) <= set(description["capabilities"]),
                    "UNSUPPORTED_CAPABILITY", "实例能力与协商不符",
                )
                require(
                    all(created["limits"][key] <= self.limits[key] for key in self.limits),
                    "LIMIT_EXCEEDED", "实例扩大了协商配额",
                )
                self.description = {**description, "capabilities": created["capabilities"]}
                self.limits = created["limits"]
                self._started = True
                if view["model_reset"] and not any(item["code"] == "MODEL_RESET" for item in created["warnings"]):
                    created["warnings"].append(reason("MODEL_RESET", "已按显式许可重建模型；原始样本待反馈追平"))
                return created
            except EngineError as error:
                self.fault(error)
                raise

    def ready(self) -> None:
        """平台已恢复 A/S、物料、占用和必需样本后调用；仍需新 advance 才能推进。"""
        with self._lock:
            require(self._started and not self._closed, "INSTANCE_CLOSED", "调度会话未运行")
            require(self.last_error is None, "ENGINE_UNAVAILABLE", "故障会话必须显式重建")
            self.journal.set_lifecycle(self.domain_id, self.run_epoch, "running")

    def recover(self) -> None:
        """显式恢复建立新世代，保留已提交检查点并重新装配故障 worker。"""
        with self._lock:
            require(self._started and not self._closed, "INSTANCE_CLOSED", "调度会话未运行")
            self._restart(rebuild=False, allow_model_reset=False)

    def rebuild(self, *, allow_model_reset: bool = False) -> dict[str, Any]:
        """关闭旧引擎实例并在同一会话对象上重建，供故障后进程内恢复。"""
        return self._restart(rebuild=True, allow_model_reset=allow_model_reset)

    def _restart(self, *, rebuild: bool, allow_model_reset: bool) -> dict[str, Any]:
        with self._lock:
            require(not self._closed, "INSTANCE_CLOSED", "调度会话已关闭")
            if self._started:
                try:
                    self._exchange(self._request("close", {**self._identity(), "reason": "reconfigure"}))
                except EngineError:
                    pass
            self.last_error = None
            self._deadline_streak = 0
            self._stale_streak = 0
            self.forecast = None
            self._started = False
            self.instance_id, self.run_epoch = str(uuid4()), str(uuid4())
            return self.start(rebuild=rebuild, allow_model_reset=allow_model_reset)

    def decide(
        self, snapshot: dict[str, Any], *,
        timeout_ms: int | None = None, compute_budget_ms: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            require(self._started and not self._closed, "INSTANCE_CLOSED", "调度会话未运行")
            require(self.last_error is None, "ENGINE_UNAVAILABLE", "故障会话不能继续调度")
            if self.description is None:
                raise EngineError("INSTANCE_CLOSED", "调度会话尚未协商引擎能力")
            try:
                started = self._monotonic()
                captured = self.journal.capture(
                    self.domain_id, self.run_epoch, {**snapshot, "captured_at_ms": round(self._wall_time() * 1000)},
                    feedback_limit=self.limits["max_feedback_events"],
                )
                nodes = captured["snapshot"]["task_view"]["nodes"]
                options = captured["snapshot"]["resource_view"]["options"]
                call_timeout = scale_call_budget_ms(
                    self.timeout_ms if timeout_ms is None else timeout_ms,
                    node_count=len(nodes), option_count=len(options),
                )
                compute_ms = scale_compute_budget_ms(
                    self.compute_budget_ms if compute_budget_ms is None else compute_budget_ms,
                    call_timeout, node_count=len(nodes),
                )
                remain_ms = call_timeout - (self._monotonic() - started) * 1000
                require(remain_ms > 1, "DEADLINE_EXCEEDED", "快照捕获已超过调用时限", retryable=True)
                request = self._request("decide", {
                    **self._identity(), **captured, "compute_budget_ms": max(1, min(compute_ms, int(remain_ms))),
                    "apply_budget_ms": self.apply_budget_ms,
                }, timeout_ms=max(1, int(remain_ms)))
                validate_snapshot(request, limits=self.limits, check_schema=False)
                remain_ms = call_timeout - (self._monotonic() - started) * 1000
                require(remain_ms > 1, "DEADLINE_EXCEEDED", "快照校验已超过调用时限", retryable=True)
                request["timeout_ms"] = max(1, int(remain_ms))
                request["body"]["compute_budget_ms"] = max(
                    1, min(request["body"]["compute_budget_ms"], int(remain_ms)),
                )
                response = self._exchange(request, check_schema=False)
                validate_decision(
                    request, response, capabilities=self.description["capabilities"],
                    state_formats=set(self.description["checkpoint_formats"]), limits=self.limits,
                    check_schema=False,
                )
                require(
                    (self._monotonic() - started) * 1000 <= call_timeout,
                    "DEADLINE_EXCEEDED", "候选校验后已超过接受预算", retryable=True,
                )
                receipt = self.journal.accept(request, response, accepted_at_ms=round(self._wall_time() * 1000))
                self.forecast = deepcopy(response["result"]["forecast"])
                self._deadline_streak = 0
                self._stale_streak = 0
                return {**receipt, "decision": response["result"]}
            except EngineError as error:
                if error.code == "STALE_DECISION":
                    if engine_reported_stale(error):
                        self._stale_streak += 1
                        error.details.setdefault("stale_streak", self._stale_streak)
                        error.details.setdefault("retry_after_ms", stale_retry_delay_ms(self._stale_streak))
                        if self._stale_streak <= self.max_stale_retries:
                            raise
                    else:
                        raise
                elif error.code == "DEADLINE_EXCEEDED":
                    self._deadline_streak += 1
                    if self._deadline_streak <= self.max_deadline_retries:
                        raise
                self.fault(error)
                raise

    def close(self, *, reason: str = "shutdown") -> None:
        with self._lock:
            if self._closed:
                return
            try:
                if self._started:
                    # 故障关闭不能降成 drain；后者允许已准入人工动作的受控续行。
                    lifecycle = "engine_fault" if self.last_error is not None else "draining"
                    self.journal.set_lifecycle(self.domain_id, self.run_epoch, lifecycle)
                    # 故障传输可能已经终止，不能因再次调用它中断平台其余资源的回收。
                    if self.last_error is None:
                        self._exchange(self._request("close", {**self._identity(), "reason": reason}))
            finally:
                self._closed = True
                self.transport.shutdown()
