"""生产启动装配：默认 Task 串行队列，第三方/商业算法通过插件清单接入。"""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from unilabos.scheduling.engine import validate_config
from unilabos.scheduling.metadata import timing_profile_catalog
from unilabos.scheduling.plugin import PluginTransport
from unilabos.scheduling.protocol import (
    DEFAULT_APPLY_BUDGET_MS,
    DEFAULT_COMPUTE_BUDGET_MS,
    DEFAULT_TIMEOUT_MS,
    EngineError,
    EngineTransport,
    decode_message,
    require,
)
from unilabos.scheduling.queue import QUEUE_CONFIG, QueueEngine

_OBSOLETE_ALGORITHM_SETTINGS = (
    "UNILABOS_TIME_SCHEDULING_SOLVE_SECONDS", "UNILABOS_TIME_SCHEDULING_MAX_NODES",
    "ULAB_SCHEDULER_AGING_INTERVAL_SECONDS", "ULAB_ESTIMATE_MODE", "ULAB_ESTIMATE_DEFAULT_S",
    "UNILABOS_TIMING_RULES_FILE",
)


@dataclass(frozen=True)
class SelectedEngine:
    """单次启动的装配结果；会话建立后配置由会话持有独立副本。"""

    transport: EngineTransport
    engine_id: str
    config: dict[str, Any]
    restore_mode: str = "resume"
    allow_model_reset: bool = False
    timing_profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    compute_budget_ms: int = DEFAULT_COMPUTE_BUDGET_MS
    apply_budget_ms: int = DEFAULT_APPLY_BUDGET_MS

    def bridge_arguments(self) -> dict[str, Any]:
        arguments = {
            "scheduling_engine": self.transport,
            "scheduling_engine_id": self.engine_id,
            "scheduling_engine_config": deepcopy(self.config),
            "scheduling_restore_mode": self.restore_mode,
            "scheduling_allow_model_reset": self.allow_model_reset,
            "scheduling_timeout_ms": self.timeout_ms,
            "scheduling_compute_budget_ms": self.compute_budget_ms,
            "scheduling_apply_budget_ms": self.apply_budget_ms,
        }
        if self.timing_profiles:
            arguments["scheduling_timing_profiles"] = deepcopy(self.timing_profiles)
        return arguments


def configured_engine(environment: Mapping[str, str] | None = None) -> SelectedEngine:
    """读取一次启动配置，验证后创建一个传输；任何错误均不降级到其他引擎。"""
    env = os.environ if environment is None else environment
    selected = env.get("UNILABOS_SCHEDULING_ENGINE", "queue").strip()
    require(selected in {"queue", "plugin"}, "CONFIG_INVALID", "UNILABOS_SCHEDULING_ENGINE 必须为 queue 或 plugin")
    restore_mode = env.get("UNILABOS_SCHEDULING_RESTORE_MODE", "resume").strip()
    reset = env.get("UNILABOS_SCHEDULING_ALLOW_MODEL_RESET", "false").strip().lower()
    require(restore_mode in {"resume", "rebuild"}, "CONFIG_INVALID", "恢复模式必须为 resume 或 rebuild")
    require(reset in {"true", "false"}, "CONFIG_INVALID", "模型重置许可必须为 true 或 false")
    require(reset == "false" or restore_mode == "rebuild", "CONFIG_INVALID", "模型重置许可只能与显式 rebuild 一起使用")
    allow_model_reset = reset == "true"
    try:
        profiles = timing_profile_catalog(decode_message(env.get("UNILABOS_SCHEDULING_TIMING_PROFILES", "{}").encode("utf-8")))
    except (UnicodeError, EngineError) as error:
        raise EngineError("CONFIG_INVALID", "UNILABOS_SCHEDULING_TIMING_PROFILES 不是有效计时元数据目录") from error
    old_enabled = env.get("UNILABOS_TIME_SCHEDULING_ENABLED", "").strip().lower()
    require(
        not old_enabled or (old_enabled in {"false", "0"} and selected == "queue"),
        "CONFIG_INVALID", "旧时间排程开关不能选择引擎，请使用 UNILABOS_SCHEDULING_ENGINE 和插件清单",
    )
    obsolete = [key for key in _OBSOLETE_ALGORITHM_SETTINGS if env.get(key, "").strip()]
    require(
        not obsolete, "CONFIG_INVALID", "旧算法参数须迁入 UNILABOS_SCHEDULING_CONFIG",
        details={"settings": obsolete},
    )
    try:
        timeout_ms = int(env.get("UNILABOS_SCHEDULING_STARTUP_TIMEOUT_MS", "10000"))
        call_timeout_ms = int(env.get("UNILABOS_SCHEDULING_TIMEOUT_MS", str(DEFAULT_TIMEOUT_MS)))
        compute_budget_ms = int(env.get("UNILABOS_SCHEDULING_COMPUTE_BUDGET_MS", str(DEFAULT_COMPUTE_BUDGET_MS)))
        apply_budget_ms = int(env.get("UNILABOS_SCHEDULING_APPLY_BUDGET_MS", str(DEFAULT_APPLY_BUDGET_MS)))
    except ValueError as error:
        raise EngineError("CONFIG_INVALID", "引擎超时或预算必须为整数毫秒") from error
    require(1 <= timeout_ms <= 600000, "CONFIG_INVALID", "引擎启动超时必须在 1～600000 毫秒")
    require(1 <= call_timeout_ms <= 600000, "CONFIG_INVALID", "调度调用超时必须在 1～600000 毫秒")
    require(1 <= compute_budget_ms <= 600000, "CONFIG_INVALID", "调度计算预算必须在 1～600000 毫秒")
    require(1 <= apply_budget_ms <= 600000, "CONFIG_INVALID", "调度准入预算必须在 1～600000 毫秒")
    require(compute_budget_ms <= call_timeout_ms, "CONFIG_INVALID", "计算预算不能超过调用超时")
    config_text = env.get("UNILABOS_SCHEDULING_CONFIG")
    if config_text is None:
        config = deepcopy(QUEUE_CONFIG) if selected == "queue" else {}
    else:
        try:
            config = decode_message(config_text.encode("utf-8"))
            require(isinstance(config, dict), "CONFIG_INVALID", "引擎配置必须是 JSON 对象")
        except (UnicodeError, EngineError) as error:
            raise EngineError("CONFIG_INVALID", "UNILABOS_SCHEDULING_CONFIG 不是有效 JSON 对象") from error
    manifest = env.get("UNILABOS_SCHEDULING_MANIFEST", "").strip()
    if selected == "queue":
        validate_config(config, QueueEngine.config_schema)
        path = Path(manifest).expanduser().resolve() if manifest else Path(__file__).with_name("queue_native") / "plugin.json"
        transport = PluginTransport(path, startup_timeout_ms=timeout_ms)
        if transport.description["engine_id"] != QueueEngine.engine_id or transport.manifest["artifact"]["kind"] != "native":
            transport.shutdown()
            raise EngineError("CONFIG_INVALID", "串行模式必须装配开源串行动态库")
        return SelectedEngine(
            transport, QueueEngine.engine_id, config, restore_mode, allow_model_reset, profiles,
            call_timeout_ms, compute_budget_ms, apply_budget_ms,
        )
    require(bool(manifest), "CONFIG_INVALID", "插件引擎必须配置 UNILABOS_SCHEDULING_MANIFEST")
    transport = PluginTransport(Path(manifest).expanduser().resolve(), startup_timeout_ms=timeout_ms)
    try:
        validate_config(config, transport.description["config_schema"])
    except BaseException:
        transport.shutdown()
        raise
    return SelectedEngine(
        transport, transport.description["engine_id"], config, restore_mode, allow_model_reset, profiles,
        call_timeout_ms, compute_budget_ms, apply_budget_ms,
    )
