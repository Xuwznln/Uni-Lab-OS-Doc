"""P1 v5 — PRCXI 8 通道 → 1 通道扁平化工具函数（PLR-free 模块）。

把 ``use_channels=[0..7]`` 的「一次性 8 通道并行 aspirate/dispense」入参展开为
``8 × M`` 次「按列从 A→H 顺序的单通道操作」入参。PRCXI 单头硬件（9300 / 9320）
物理上无 8 通道并行能力，扁平化后由抽象层单通道循环顺序执行。

设计文档：``product_designs/protocol_convert/01-multi-channel-flatten.md``
  - §0  framework convention：8 通道 pipette 方向恒为 A~H column（governing rule）
  - §11.3 / §13  扁平化 helper 落地实现（length-8 → tile M 次为前置规则）

本模块刻意不 import ``pylabrobot``，便于在本地 PLR 版本不匹配的环境下也能
对扁平化逻辑做单元测试（与 ``liquid_history.py`` 的 P10 v2 helper 同策略）。
``PRCXI9300Handler._flatten_multi_channel_kwargs`` 是本模块函数的薄包装，保留
"helper 与 PRCXI 静态方法聚在一起"的设计意图（§11.3）。
"""

from __future__ import annotations

from typing import Any, Dict, Sequence


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

    展开规则（**governing rule，详见
    ``product_designs/protocol_convert/01-multi-channel-flatten.md`` §0.2**）：

      - ``asp_vols`` / ``dis_vols`` 必须长度 ``= 8 × M`` 且 ``> 0``；以此为基准长度 ``N``。
      - 其它 per-well 入参（``sources`` / ``targets`` / ``asp_flow_rates`` /
        ``dis_flow_rates`` / ``offsets`` / ``liquid_height`` /
        ``blow_out_air_volume`` / ``blow_out_air_volume_before`` / ``delays`` /
        ``pre_aspirate_from_target``）按以下 4 条 rule **顺序判断，先匹配先返回**：

        1. ``n == N``      → passthrough          （逐 op 显式给值，最特定）
        2. ``n == 8``      → tile M 次            （A~H channel column 唯一语义，§0.1 hardware fact）
        3. ``n == 1``      → broadcast            （单一共享）
        4. 其它            → ``raise ValueError``（强制 disambiguate）

      - **不存在 ``n == M``（repeat-each by 8）的合法语义**：8 通道模式的最小
        操作单元就是「1 op = 8 通道并行」，长度 M 的输入隐含「每 op 只移液 M
        个」，与硬件物理事实冲突。多 reservoir 跨列场景应改用：(a) 同液池
        ``n == 1`` 广播；(b) 异液池 ``n == N`` 显式逐 op；(c) 跨多板同 reagent
        走 §02 cross-slot-merge。
      - ``None`` / 标量（非 list/tuple）透传，不被 4 条 rule 处理。
      - **空 list / tuple 等价于 ``None``**（"未填值"语义）：caller 不传 per-well
        offsets / flow_rates / delays 等可选参数时上游可能下发 ``[]``，应等同
        "不限制 / 走默认"，不应触发 rule 4 报错。
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
        """按 §0.2 governing rule 展开：N 透传 → 8 tile M → 1 广播（无 ``n == M`` 分支）。"""
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            return value
        n = len(value)
        # 空 list/tuple 等价于 None（"未填值"）：caller 不传可选 per-well 参数时常下发 ``[]``，
        # 按"走默认"语义返回 None，避免触发下方 rule 4 报错。
        if n == 0:
            return None
        # rule 1：逐 op 显式给值（最特定，最优先）
        if n == n_total:
            return list(value)
        # rule 2：8 通道 A~H channel-column tile（§0 framework convention）。
        # 8-channel pipette 物理方向恒为 A~H，length 8 唯一表示 "一整列 = 8 channels"，
        # 循环复用给 M 个目标列。M=1 (n_total=8) 时上一条 passthrough 已拦截，
        # 永远走不到这里；m_cols == 8 碰撞带也优先此分支（§13.4 关键不变量表）。
        if n == 8:
            return list(value) * m_cols
        # rule 3：单一共享值广播到所有 N 次操作
        if n == 1:
            return [value[0]] * n_total
        # rule 4：长度异常 → 强制 disambiguate，避免静默错位。
        # 注：``n == m_cols`` 故意**不**在合法表中——8 通道模式下，length=M 隐含
        # "每 op 只移液 M 个"语义，与硬件物理事实（1 op = 8 通道并行）冲突。
        raise ValueError(
            f"参数 {name} 长度 {n} 不匹配 8 通道扁平化要求（应为 {n_total} / 8 / 1）"
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
