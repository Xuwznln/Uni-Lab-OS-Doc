"""P1 v5 — PRCXI 8 通道 → 1 通道扁平化工具函数（PLR-free 模块）。

把 ``use_channels=[0..7]`` 的「一次性 8 通道并行 aspirate/dispense」入参展开为
``8 × M`` 次「按列从 A→H 顺序的单通道操作」入参。PRCXI 单头硬件（9300 / 9320）
物理上无 8 通道并行能力，扁平化后由抽象层单通道循环顺序执行。

设计文档：``product_designs/protocol_convert/01-multi-channel-flatten.md`` §11.3。

本模块刻意不 import ``pylabrobot``，便于在本地 PLR 版本不匹配的环境下也能
对扁平化逻辑做单元测试（与 ``liquid_history.py`` 的 P10 v2 helper 同策略）。
``PRCXI9300Handler._flatten_multi_channel_kwargs`` 是本模块函数的薄包装，保留
"helper 与 PRCXI 静态方法聚在一起"的设计意图（§11.3）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


__all__ = ["flatten_multi_channel_kwargs"]


def flatten_multi_channel_kwargs(
    *,
    sources: Sequence,
    targets: Sequence,
    asp_vols: Sequence[float],
    dis_vols: Sequence[float],
    asp_flow_rates: Any = None,
    dis_flow_rates: Any = None,
    offsets: Any = None,
    liquid_height: Any = None,
    blow_out_air_volume: Any = None,
    blow_out_air_volume_before: Any = None,
    delays: Any = None,
    pre_aspirate_from_target: Any = None,
) -> Dict[str, Any]:
    """把 v4 抽象层 8 通道形态展开为 8 × M 次单通道顺序操作的入参。

    展开规则（详见 ``product_designs/protocol_convert/01-multi-channel-flatten.md`` §11.3）：

      - ``asp_vols`` / ``dis_vols`` 必须长度 ``= 8 × M`` 且 ``> 0``；以此为基准长度 ``N``。
      - ``sources`` / ``targets``：``len == N`` 透传；``len == M``（reservoir 共享）→
        每元素重复 8 次。
      - 其它 per-well 参数（flow_rates / offsets / liquid_height / blow_out_*
        / delays / pre_aspirate_from_target）同上规则；``None`` / 标量透传。
      - 任何长度异常 → ``raise ValueError``，避免静默错位。
    """
    n_total = len(asp_vols)
    if n_total != len(dis_vols):
        raise ValueError(
            f"asp_vols / dis_vols 长度必须一致，"
            f"实际 asp_vols={n_total} / dis_vols={len(dis_vols)}"
        )
    if n_total == 0 or n_total % 8 != 0:
        raise ValueError(
            f"v4 多通道入参 asp_vols 长度必须是 8 的正倍数，实际 {n_total}"
        )
    m_cols = n_total // 8

    def _expand_per_well(value: Any, name: str) -> Any:
        """长度 N 透传；长度 M → 每元素 ×8；长度 1 → 广播 N；None / 标量 → 透传。"""
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            return value
        n = len(value)
        if n == n_total:
            return list(value)
        if n == m_cols:
            out: List[Any] = []
            for v in value:
                out.extend([v] * 8)
            return out
        if n == 1:
            return [value[0]] * n_total
        raise ValueError(
            f"参数 {name} 长度 {n} 不匹配 8 通道扁平化要求（应为 {n_total} / {m_cols} / 1）"
        )

    return {
        "sources": _expand_per_well(sources, "sources"),
        "targets": _expand_per_well(targets, "targets"),
        "asp_vols": list(asp_vols),
        "dis_vols": list(dis_vols),
        "asp_flow_rates": _expand_per_well(asp_flow_rates, "asp_flow_rates"),
        "dis_flow_rates": _expand_per_well(dis_flow_rates, "dis_flow_rates"),
        "offsets": _expand_per_well(offsets, "offsets"),
        "liquid_height": _expand_per_well(liquid_height, "liquid_height"),
        "blow_out_air_volume": _expand_per_well(blow_out_air_volume, "blow_out_air_volume"),
        "blow_out_air_volume_before": _expand_per_well(
            blow_out_air_volume_before, "blow_out_air_volume_before"
        ),
        "delays": _expand_per_well(delays, "delays"),
        "pre_aspirate_from_target": _expand_per_well(
            pre_aspirate_from_target, "pre_aspirate_from_target"
        ),
    }
