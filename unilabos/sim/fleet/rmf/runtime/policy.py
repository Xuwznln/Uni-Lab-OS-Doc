"""rmf.coordinator 的 graph 启动策略解析。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


def _as_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"1", "true", "yes", "y", "on"}:
            return True
        if s in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _derive_runtime_root(map_dir: str) -> str:
    """从 map_dir 推导 runtime_root。"""
    if not map_dir:
        return ""
    p = Path(map_dir).expanduser().resolve()
    # 约定：<runtime_root>/maps/latest
    if p.name == "latest" and p.parent.name == "maps":
        return str(p.parent.parent)
    if p.parent.name == "maps":
        return str(p.parent.parent)
    return str(p.parent)


@dataclass(frozen=True)
class RmfStartupPolicy:
    auto_compile_map: bool = False
    auto_start_runtime: bool = False
    runtime_mode: str = "headless"  # "headless" | "sim"
    runtime_use_sim_time: bool = False
    layout_optimizer_dir: str = ""
    runtime_root: str = ""
    map_dir: str = ""
    start_api_server: bool = True
    stop_before_start: bool = False

    @property
    def should_bootstrap(self) -> bool:
        return bool(self.auto_compile_map or self.auto_start_runtime)


def build_startup_policy(
    config: Mapping[str, Any],
    *,
    generated_map_dir: str = "",
    default_runtime_root: str = "",
) -> RmfStartupPolicy:
    """从 rmf.coordinator 的图配置解析启动策略。"""
    map_dir = (
        _as_str(config.get("generated_map_dir"))
        or _as_str(config.get("generatedMapDir"))
        or _as_str(generated_map_dir)
    )
    runtime_root = (
        _as_str(config.get("runtime_root"))
        or _as_str(config.get("runtimeRoot"))
        or _derive_runtime_root(map_dir)
        or _as_str(default_runtime_root)
    )

    layout_optimizer_dir = (
        _as_str(config.get("layout_optimizer_dir"))
        or _as_str(config.get("layoutOptimizerDir"))
    )

    auto_compile_map = _parse_bool(config.get("auto_compile_map"))
    if auto_compile_map is None:
        auto_compile_map = _parse_bool(config.get("autoCompileMap"))
    if auto_compile_map is None:
        auto_compile_map = False

    auto_start_runtime = _parse_bool(config.get("auto_start_runtime"))
    if auto_start_runtime is None:
        auto_start_runtime = _parse_bool(config.get("autoStartRuntime"))
    if auto_start_runtime is None:
        auto_start_runtime = False

    runtime_mode = _as_str(config.get("runtime_mode")) or _as_str(config.get("runtimeMode")) or "headless"
    runtime_mode = runtime_mode.lower()
    if runtime_mode not in {"headless", "sim"}:
        runtime_mode = "headless"

    runtime_use_sim_time = _parse_bool(config.get("runtime_use_sim_time"))
    if runtime_use_sim_time is None:
        runtime_use_sim_time = _parse_bool(config.get("runtimeUseSimTime"))
    if runtime_use_sim_time is None:
        runtime_use_sim_time = _parse_bool(config.get("use_sim_time"))
    if runtime_use_sim_time is None:
        runtime_use_sim_time = runtime_mode == "sim"

    start_api_server = _parse_bool(config.get("start_api_server"))
    if start_api_server is None:
        start_api_server = _parse_bool(config.get("startApiServer"))
    if start_api_server is None:
        start_api_server = True

    stop_before_start = _parse_bool(config.get("stop_before_start"))
    if stop_before_start is None:
        stop_before_start = _parse_bool(config.get("stopBeforeStart"))
    if stop_before_start is None:
        stop_before_start = False

    return RmfStartupPolicy(
        auto_compile_map=bool(auto_compile_map),
        auto_start_runtime=bool(auto_start_runtime),
        runtime_mode=runtime_mode,
        runtime_use_sim_time=bool(runtime_use_sim_time),
        layout_optimizer_dir=layout_optimizer_dir,
        runtime_root=runtime_root,
        map_dir=map_dir,
        start_api_server=bool(start_api_server),
        stop_before_start=bool(stop_before_start),
    )

