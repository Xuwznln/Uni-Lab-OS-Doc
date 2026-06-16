"""导轨类机械臂实验室布局 — 核心纯函数模块（公共基础设施 / 阶段0）。

与 ``optimizer.py`` 的差分进化（DE）随机寻优不同，本模块走的是**确定性解析布局**：
距离参数定死后，机械臂、堆栈、仪器的坐标唯一确定，算出来即可，无需搜索。

适用范围（与逻辑文档一致）：**无多台同类型仪器的线性实验流程**。

设计约定：

- 本模块刻意保持**无重副作用的导入**（不创建日志文件、不构建 FastAPI app），
  以便被多进程 worker 与单测安全导入，范式参照 ``feasibility.py``。
- 三阶段算法（``check_feasibility`` / ``place_arms_and_stacks`` /
  ``assign_and_place_instruments`` / ``validate_placements``）将在后续里程碑
  M1~M3 填充；本文件（M0 公共前置）只提供共享基础设施：默认参数、工作半径默认值、
  共享数据结构与函数骨架。

里程碑对照（见 ``导轨机械臂布局实现计划.md``）：

- M0（本文件骨架）：``DEFAULT_PARAMS`` + 工作半径默认 0.3m + 数据结构 + 函数签名。
- M1：``check_feasibility`` 可行性检查。
- M2：``place_arms_and_stacks`` 布局机械臂与堆栈。
- M3：``assign_and_place_instruments`` + ``validate_placements`` 仪器布置与校验。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from typing import Any

    from .models import Device, Lab

_EPS = 1e-9

# ---------- 工作半径默认值 ----------
# TODO(rail_arm_models.json)：正式实现必须新建「机械臂型号 → (导轨长度 L, 工作半径,
# bbox)」对应表，按型号查表替换此默认值（见实现计划 0.3）。当前阶段为先跑通主流程，
# 工作半径统一取 0.3m，导轨长度 L / bbox 暂从 /devices 的 bbox 推或用占位值。
DEFAULT_WORKING_RADIUS: float = 0.3

# ---------- 默认距离参数（米），允许用户覆盖 ----------
# a：机械臂较短一侧到墙的垂直距离
# b：机械臂较长一侧到仪器的垂直距离（规定同侧所有仪器到机械臂的距离 b 一致）
# c：仪器之间的距离（一个仪器长边到另一个仪器长边的垂直距离）
# d：仪器到墙的垂直距离（仪器宽边到墙）
# e：机械臂到堆栈的垂直距离
#
# 硬约束（可达性约定，见逻辑文档 3.2）：默认 b < 工作半径、e < 工作半径。
DEFAULT_PARAMS: dict[str, float] = {
    "a": 0.5,
    "b": 0.2,
    "c": 0.3,
    "d": 0.3,
    "e": 0.2,
}

# 默认堆栈型号：从 footprints.json 取真实 bbox/openings（见实现计划 0.3 第 2 项）。
# 用户可通过 stack_model 入参指定其他型号；解析失败时回落 DEFAULT_STACK_BBOX。
DEFAULT_STACK_MODEL: str = "thermo_stacker"

# 堆栈占地 bbox 的最终兜底值（米），仅在 footprints 加载失败且无任何可识别堆栈时使用。
DEFAULT_STACK_BBOX: tuple[float, float] = (0.4, 0.4)

# 机械臂默认占地 bbox（米），仅在既无 arm_model 又无法从 devices 识别机械臂时回落。
DEFAULT_ARM_BBOX: tuple[float, float] = (0.2, 1.0)

# 识别机械臂 / 堆栈的关键词（设备 id / name 命中即视为对应类型）。
_ARM_KEYWORDS = ("arm", "slider", "rail", "gantry", "robot", "导轨", "机械臂")
_STACK_KEYWORDS = ("stack", "hotel", "buffer", "堆栈", "缓存", "转运")


@dataclass
class RailParams:
    """导轨布局的距离参数集合（含工作半径），集中管理、允许用户覆盖。

    通过 :meth:`from_overrides` 在 ``DEFAULT_PARAMS`` / ``DEFAULT_WORKING_RADIUS``
    基础上叠加用户传入的覆盖值，缺省项回落默认值。
    """

    a: float = DEFAULT_PARAMS["a"]
    b: float = DEFAULT_PARAMS["b"]
    c: float = DEFAULT_PARAMS["c"]
    d: float = DEFAULT_PARAMS["d"]
    e: float = DEFAULT_PARAMS["e"]
    working_radius: float = DEFAULT_WORKING_RADIUS

    @classmethod
    def from_overrides(cls, overrides: dict[str, float] | None = None) -> RailParams:
        """在默认参数上叠加用户覆盖，返回新的 ``RailParams``。

        Args:
            overrides: 形如 ``{"a": 0.6, "b": 0.25, ...}`` 的覆盖字典；
                ``None`` 或缺失键回落默认值，值为 ``None`` 的键也忽略。
        """
        merged: dict[str, float] = dict(DEFAULT_PARAMS)
        merged["working_radius"] = DEFAULT_WORKING_RADIUS
        if overrides:
            for key, value in overrides.items():
                if key in merged and value is not None:
                    merged[key] = float(value)
        return cls(**merged)

    def reachability_violations(self) -> list[str]:
        """返回违反可达性参数级约束（``b < r`` 且 ``e < r``）的原因列表。

        为空表示参数满足可达性约定。
        """
        reasons: list[str] = []
        if self.b >= self.working_radius - _EPS:
            reasons.append(
                f"距离参数 b={self.b:.3f}m 不小于工作半径 {self.working_radius:.3f}m，"
                "机械臂可能够不到该侧仪器（要求 b < 工作半径）。"
            )
        if self.e >= self.working_radius - _EPS:
            reasons.append(
                f"距离参数 e={self.e:.3f}m 不小于工作半径 {self.working_radius:.3f}m，"
                "相邻机械臂可能够不到中间堆栈（要求 e < 工作半径）。"
            )
        return reasons


# ---------- 共享数据结构（三阶段通用） ----------


@dataclass
class RailConflict:
    """一条导轨布局的确定性冲突 / 不可行原因。

    范式对齐 ``feasibility.py`` 的 ``Conflict``：kind + message + suggestion。
    """

    kind: str  # 冲突类别，如 "area" / "long_side" / "reachability"
    message: str  # 人类可读的解释
    suggestion: str = ""  # 放宽建议


@dataclass
class FeasibilityReport:
    """阶段一可行性检查的结构化报告。"""

    feasible: bool
    n_arm: int = 0  # 粗估机械臂台数
    n_stack: int = 0  # 堆栈台数 = n_arm - 1
    n_max: int = 0  # 长边几何上限可容纳的最大臂数
    l_max: float = 0.0  # 最长仪器的「伸出长度」L_out，供阶段二靠墙模式定位复用
    mode_hint: Literal["near_wall", "centered"] = "near_wall"
    reasons: list[str] = field(default_factory=list)  # 不可行原因
    suggestions: list[str] = field(default_factory=list)  # 放宽建议（与 reasons 对应）


@dataclass
class ArmPlacement:
    """单台机械臂的布局结果。

    ``corners`` 按 [左下, 右下, 右上, 左上] 顺序输出四个角坐标，
    供阶段三反推仪器坐标（左侧用左下角、右侧用右上角）。
    """

    id: str
    center: tuple[float, float]  # 中心点 (x, y)，单位 m
    theta: float  # 旋转角，弧度
    corners: list[tuple[float, float]]  # 四个角坐标
    bbox: tuple[float, float]  # (width, depth)


@dataclass
class StackPlacement:
    """单台堆栈（机械臂间转运交接点）的布局结果。"""

    id: str
    center: tuple[float, float]
    bbox: tuple[float, float]


@dataclass
class InstrumentPlacement:
    """单台仪器的布局结果（含朝向）。"""

    device_id: str
    center: tuple[float, float]
    theta: float  # 旋转角，弧度，使仪器开口面向导轨
    arm_id: str = ""  # 所归属的机械臂 id
    side: Literal["left", "right"] = "left"  # 位于机械臂的哪一侧
    bbox: tuple[float, float] = (0.0, 0.0)  # 仪器原生 bbox(width, depth)，供碰撞校验/前端用


@dataclass
class RailLayoutResult:
    """上层编排 :func:`layout_rail` 的完整布局结果。"""

    arms: list[ArmPlacement] = field(default_factory=list)
    stacks: list[StackPlacement] = field(default_factory=list)
    placements: list[InstrumentPlacement] = field(default_factory=list)
    report: FeasibilityReport | None = None
    leftover: list[str] = field(default_factory=list)  # 未能布置的仪器 id（理论上为空）
    conflicts: list[RailConflict] = field(default_factory=list)


# ---------- 几何解析辅助（阶段一/二/三共用） ----------


@dataclass
class _ArmGeo:
    """解析后的机械臂几何。"""

    L: float  # 导轨长度（沿房间长边方向）
    width: float  # 机械臂较短一侧尺寸（沿房间短边方向）
    working_radius: float
    area: float
    bbox: tuple[float, float]


def _resolve_arm(
    devices: list["Device"],
    arm_model: dict | None,
    params: RailParams,
) -> _ArmGeo:
    """确定机械臂几何：优先用 arm_model，否则从 devices 识别。

    Raises:
        ValueError: 既无 arm_model 又无法从 devices 识别出机械臂。
    """
    if arm_model:
        bbox = tuple(float(x) for x in arm_model.get("bbox", DEFAULT_ARM_BBOX))
        L = float(arm_model.get("L", max(bbox)))
        wr = float(arm_model.get("working_radius", params.working_radius))
        return _ArmGeo(L=L, width=min(bbox), working_radius=wr, area=bbox[0] * bbox[1], bbox=bbox)

    arm_dev = None
    for d in devices:
        if getattr(d, "device_type", "") == "articulation":
            arm_dev = d
            break
    if arm_dev is None:
        for d in devices:
            ident = f"{d.id} {getattr(d, 'name', '')}".lower()
            if any(k in ident for k in _ARM_KEYWORDS):
                arm_dev = d
                break
    if arm_dev is None:
        raise ValueError(
            "无法识别导轨机械臂：请在 devices 中提供机械臂，"
            "或传入 arm_model={L, working_radius, bbox}。"
        )
    bbox = tuple(float(x) for x in arm_dev.bbox)
    return _ArmGeo(
        L=max(bbox),
        width=min(bbox),
        working_radius=params.working_radius,
        area=bbox[0] * bbox[1],
        bbox=bbox,
    )


def _load_footprints_safe() -> dict:
    """惰性加载 footprints.json；失败返回空 dict（保持本模块导入无副作用）。"""
    try:
        from .device_catalog import load_footprints

        return load_footprints()
    except Exception:  # noqa: BLE001 — footprints 不可用时降级
        return {}


def _resolve_stack(
    stack_model: str | dict | None,
    devices: list["Device"] | None,
    footprints: dict | None = None,
) -> tuple[tuple[float, float], list[tuple[float, float]]]:
    """确定堆栈几何 ``(bbox, openings)``。

    解析顺序（见实现计划 0.3 第 2 项）：
    ① 显式 ``stack_model``（dict 直接给 bbox/openings，或 str 查 footprints）
    → ② ``devices`` 中按堆栈关键词识别（兜底）
    → ③ 默认型号 :data:`DEFAULT_STACK_MODEL`（thermo_stacker，查 footprints）
    → ④ 最终回落 :data:`DEFAULT_STACK_BBOX`。
    """

    def _from_fp_entry(entry: dict) -> tuple[tuple[float, float], list]:
        bbox = tuple(float(x) for x in entry.get("bbox", DEFAULT_STACK_BBOX))
        openings = [tuple(o["direction"]) for o in entry.get("openings", [])]
        return bbox, openings

    # ① 显式 stack_model
    if isinstance(stack_model, dict):
        bbox = tuple(float(x) for x in stack_model.get("bbox", DEFAULT_STACK_BBOX))
        openings = [tuple(o) for o in stack_model.get("openings", [])]
        return bbox, openings
    fp = footprints if footprints is not None else _load_footprints_safe()
    if isinstance(stack_model, str) and stack_model:
        if stack_model in fp:
            return _from_fp_entry(fp[stack_model])
        # stack_model 给了 id 但 footprints 没有 → 尝试在 devices 里按 id 命中
        if devices:
            for d in devices:
                if d.id == stack_model:
                    openings = [tuple(o.direction) for o in getattr(d, "openings", [])]
                    return tuple(float(x) for x in d.bbox), openings

    # ② devices 关键词识别（兜底）
    if devices:
        for d in devices:
            ident = f"{d.id} {getattr(d, 'name', '')}".lower()
            if any(k in ident for k in _STACK_KEYWORDS):
                openings = [tuple(o.direction) for o in getattr(d, "openings", [])]
                return tuple(float(x) for x in d.bbox), openings

    # ③ 默认型号 thermo_stacker
    if DEFAULT_STACK_MODEL in fp:
        return _from_fp_entry(fp[DEFAULT_STACK_MODEL])

    # ④ 最终兜底
    return DEFAULT_STACK_BBOX, []


def _instrument_dims(dev: "Device") -> tuple[float, float, float]:
    """返回单台仪器 (W_rail, L_out, area)。

    - W_rail：与朝向垂直的尺寸（沿导轨方向），即逻辑文档的「宽 W」。
    - L_out：沿朝向的尺寸（垂直导轨方向，向墙/臂伸出），即「仪器长度 L」。
    - area：占地面积。

    朝向取 ``openings[0].direction``（设备局部坐标系），缺省为 (0,-1)。
    bbox 为 (沿 X 的 width, 沿 Y 的 depth)。
    """
    w, h = float(dev.bbox[0]), float(dev.bbox[1])
    if getattr(dev, "openings", None):
        dx, dy = dev.openings[0].direction
    else:
        dx, dy = 0.0, -1.0
    if abs(dy) >= abs(dx):
        # 朝向沿 Y：伸出长度 = depth(h)，沿导轨宽 = width(w)
        return w, h, w * h
    # 朝向沿 X：伸出长度 = width(w)，沿导轨宽 = depth(h)
    return h, w, w * h


# ---------- 阶段一：可行性检查 ----------


def check_feasibility(
    devices: list["Device"],
    ordered_instruments: list[str],
    lab: "Lab",
    arm_model: dict | None = None,
    params: RailParams | None = None,
    stack_model: str | dict | None = DEFAULT_STACK_MODEL,
) -> FeasibilityReport:
    """阶段一：可行性检查。

    依次跑全部子判据（共享 ``sum_W / L_max / L_2nd / 参数`` 等中间量）：
    ① 面积检查 ② 算台数 n_arm/n_stack（粗估）③ 短边不等式 1/2
    ④ 长边不等式 + 反解 n_max ⑤ 可达性（b<工作半径、e<工作半径）。

    Args:
        devices: 设备列表（提供 bbox / openings）。
        ordered_instruments: 按实验流程排序的仪器 id 列表。
        lab: 实验室平面图（width/depth/obstacles）。
        arm_model: 机械臂型号信息 ``{L, working_radius, bbox}``；为 ``None`` 时
            从 devices 识别机械臂，工作半径回落 :data:`DEFAULT_WORKING_RADIUS`。
        params: 距离参数；为 ``None`` 时使用 :data:`DEFAULT_PARAMS`。
        stack_model: 堆栈型号（id 或 ``{bbox, openings}``），默认
            :data:`DEFAULT_STACK_MODEL`（thermo_stacker）。``stack_h`` 取
            ``max(bbox)`` 作保守估计，须与阶段二一致。

    Returns:
        :class:`FeasibilityReport`。

    Raises:
        ValueError: devices 中缺少某个 ordered_instruments 仪器，或无法识别机械臂。
    """
    params = params or RailParams()
    arm = _resolve_arm(devices, arm_model, params)
    # 让可达性按解析出的工作半径判定（arm_model 可能覆盖 working_radius）
    eff_params = replace(params, working_radius=arm.working_radius)

    if arm.L <= _EPS:
        raise ValueError(f"机械臂导轨长度 L={arm.L} 非法（须为正）。")

    device_map = {d.id: d for d in devices}
    stack_bbox, _ = _resolve_stack(stack_model, devices)
    stack_h = max(stack_bbox)  # 沿长边方向取保守（较大）尺寸，与阶段二一致
    stack_area = stack_bbox[0] * stack_bbox[1]

    a, b, c, d_param, e = (
        eff_params.a,
        eff_params.b,
        eff_params.c,
        eff_params.d,
        eff_params.e,
    )

    # --- 解析仪器尺寸 ---
    w_list: list[float] = []
    lout_list: list[float] = []
    inst_area = 0.0
    for iid in ordered_instruments:
        dev = device_map.get(iid)
        if dev is None:
            raise ValueError(f"仪器 '{iid}' 不在 devices 中，无法解析尺寸。")
        w_rail, l_out, area = _instrument_dims(dev)
        w_list.append(w_rail)
        lout_list.append(l_out)
        inst_area += area

    k = len(ordered_instruments)
    lab_long = max(lab.width, lab.depth)
    lab_short = min(lab.width, lab.depth)

    # --- ② 算台数（粗估）---
    sum_w = sum(w_list) + max(k - 1, 0) * c
    if k == 0:
        n_arm = 0
    else:
        n_arm = max(int(math.ceil(sum_w / (2 * arm.L) - _EPS)), 1)
    n_stack = max(n_arm - 1, 0)

    l_max = max(lout_list) if lout_list else 0.0
    sorted_lout = sorted(lout_list, reverse=True)
    l_2nd = sorted_lout[1] if k >= 2 else 0.0

    # --- ④ 长边不等式 + 反解 n_max ---
    denom = arm.L + 2 * e + stack_h
    n_max = max(int(math.floor((lab_long - 2 * a + 2 * e + stack_h) / denom + _EPS)), 0)

    reasons: list[str] = []
    suggestions: list[str] = []

    # --- ① 面积检查 ---
    total_area = inst_area + n_arm * arm.area + n_stack * stack_area
    lab_area = lab.width * lab.depth
    if total_area > lab_area + _EPS:
        reasons.append(
            f"设备占地总面积 {total_area:.3f}㎡（仪器+{n_arm}臂+{n_stack}堆栈）"
            f"超过房间面积 {lab_area:.3f}㎡（{lab.width:.2f}×{lab.depth:.2f}）。"
        )
        suggestions.append("扩大房间尺寸、减少仪器数量，或更换更小占地的设备。")

    # --- ⑤ 可达性（参数级）---
    for r in eff_params.reachability_violations():
        reasons.append(r)
        suggestions.append("更换工作半径更大的机械臂型号，或减小距离参数 b/e。")

    # --- ③ 短边不等式 2（不成立 → 不可行）---
    short_need_2 = 2 * d_param + b + l_max + arm.width
    ineq2_ok = short_need_2 <= lab_short + _EPS
    # 短边不等式 1（成立 → 居中模式可用）
    short_need_1 = 2 * d_param + 2 * b + l_max + l_2nd + arm.width
    ineq1_ok = short_need_1 <= lab_short + _EPS

    if k > 0 and not ineq2_ok:
        reasons.append(
            f"短边方向放不下：2d+b+最长仪器{l_max:.3f}+臂宽{arm.width:.3f} = "
            f"{short_need_2:.3f}m > 房间短边 {lab_short:.3f}m。"
        )
        suggestions.append("扩大房间短边、更换伸出更短的仪器，或减小距离参数 b、d。")

    # --- ④ 长边台数检查 ---
    if k > 0 and n_arm > n_max:
        reasons.append(
            f"长边方向放不下 {n_arm} 台机械臂（长边几何上限 n_max={n_max}）："
            f"2a+{n_arm}·L+{n_stack}·(2e+堆栈) 超过房间长边 {lab_long:.3f}m。"
        )
        suggestions.append("扩大房间长边、更换更短导轨的机械臂，或减少仪器数量。")

    feasible = len(reasons) == 0
    mode_hint: Literal["near_wall", "centered"] = "centered" if ineq1_ok else "near_wall"

    return FeasibilityReport(
        feasible=feasible,
        n_arm=n_arm,
        n_stack=n_stack,
        n_max=n_max,
        l_max=l_max,
        mode_hint=mode_hint,
        reasons=reasons,
        suggestions=suggestions,
    )


# ---------- 阶段二：布局机械臂与堆栈 ----------


def _to_xy(long_c: float, short_c: float, long_axis: str) -> tuple[float, float]:
    """把 (长边坐标, 短边坐标) 映射为房间 (x, y)。

    导轨沿房间长边摆放：``long_axis='y'`` 时长边即 Y、短边即 X；反之亦然。
    """
    if long_axis == "y":
        return (short_c, long_c)
    return (long_c, short_c)


def place_arms_and_stacks(
    lab: "Lab",
    n_arm: int,
    arm_model: dict | None = None,
    params: RailParams | None = None,
    mode: Literal["near_wall", "centered"] = "near_wall",
    stack_model: str | dict | None = DEFAULT_STACK_MODEL,
    l_max: float = 0.0,
    devices: list["Device"] | None = None,
) -> dict[str, list]:
    """阶段二：布局机械臂与堆栈。

    导轨沿房间长边摆放（臂长边 ∥ 房间长边）。沿长边从下墙到上墙依次为：
    下墙 — a — [臂1, 长 L] — (2e+stack_h) — [臂2] — … — [臂n] — a — 上墙。
    第一台短边方向位置按 ``mode`` 确定，后续臂/堆栈为纯几何偏移：相邻臂中心
    间距固定为 ``L + 2e + stack_h``；堆栈中轴线对齐臂中轴线、夹在相邻臂之间、
    距臂 ``e``（即位于相邻两臂中点）。

    Args:
        lab: 实验室平面图。
        n_arm: 机械臂台数（通常取自 :func:`check_feasibility` 的报告）。
        arm_model: 机械臂型号 ``{L, working_radius, bbox}``；为 ``None`` 时从
            ``devices`` 识别。
        params: 距离参数；为 ``None`` 时使用默认值。
        mode: ``near_wall``（默认，靠墙）/ ``centered``（居中，需满足短边不等式 1）。
        stack_model: 堆栈型号，默认 thermo_stacker（见 0.3 第 2 项）。
        l_max: 最长仪器的伸出长度 L_out（靠墙模式定位用），取自 report.l_max。
        devices: 设备列表，供 arm_model/stack_model 缺省时识别。

    Returns:
        ``{"arms": [ArmPlacement], "stacks": [StackPlacement]}``。每台机械臂含
        四个角坐标（[左下, 右下, 右上, 左上]），供阶段三反推仪器坐标。

    Raises:
        ValueError: 无法识别机械臂。
    """
    params = params or RailParams()
    if n_arm < 1:
        return {"arms": [], "stacks": []}

    arm = _resolve_arm(devices or [], arm_model, params)
    if arm.L <= _EPS:
        raise ValueError(f"机械臂导轨长度 L={arm.L} 非法（须为正）。")

    stack_bbox, _ = _resolve_stack(stack_model, devices)
    stack_h = max(stack_bbox)  # 与阶段一保持一致（保守取较大维）

    a, b, d_param, e = params.a, params.b, params.d, params.e
    L, w_arm = arm.L, arm.width

    long_len = max(lab.width, lab.depth)
    short_len = min(lab.width, lab.depth)
    long_axis = "y" if lab.depth >= lab.width else "x"

    # 短边方向：机械臂中轴线所在的短边坐标
    if mode == "centered":
        arm_short = short_len / 2.0
    else:  # near_wall：臂较长一侧到墙距离 = d + b + L_max
        arm_short = d_param + b + l_max + w_arm / 2.0

    # 朝向：使导轨长度 L 沿房间长边；native 长边与目标长边不一致则转 90°
    native_long = "x" if arm.bbox[0] >= arm.bbox[1] else "y"
    theta = 0.0 if native_long == long_axis else math.pi / 2.0

    step = L + 2 * e + stack_h  # 相邻臂中心间距
    short_lo, short_hi = arm_short - w_arm / 2.0, arm_short + w_arm / 2.0

    arms: list[ArmPlacement] = []
    for i in range(n_arm):
        long_c = a + L / 2.0 + i * step
        long_lo, long_hi = long_c - L / 2.0, long_c + L / 2.0
        corners = [
            _to_xy(long_lo, short_lo, long_axis),  # 左下
            _to_xy(long_lo, short_hi, long_axis),  # 右下
            _to_xy(long_hi, short_hi, long_axis),  # 右上
            _to_xy(long_hi, short_lo, long_axis),  # 左上
        ]
        arms.append(
            ArmPlacement(
                id=f"arm{i + 1}",
                center=_to_xy(long_c, arm_short, long_axis),
                theta=theta,
                corners=corners,
                bbox=arm.bbox,
            )
        )

    stacks: list[StackPlacement] = []
    for i in range(n_arm - 1):
        # 夹在臂 i 与臂 i+1 中点，短边坐标与臂中轴线重合
        long_c = a + L / 2.0 + i * step + step / 2.0
        stacks.append(
            StackPlacement(
                id=f"stack{i + 1}",
                center=_to_xy(long_c, arm_short, long_axis),
                bbox=stack_bbox,
            )
        )

    return {"arms": arms, "stacks": stacks}


# ---------- 阶段三：按实验步骤顺序布置各仪器 ----------


def _sub(p: tuple[float, float], q: tuple[float, float]) -> tuple[float, float]:
    return (p[0] - q[0], p[1] - q[1])


def _norm(v: tuple[float, float]) -> float:
    return math.hypot(v[0], v[1])


def _unit(v: tuple[float, float]) -> tuple[float, float]:
    n = _norm(v)
    if n <= _EPS:
        return (0.0, 0.0)
    return (v[0] / n, v[1] / n)


def _theta_to(dev: "Device", target: tuple[float, float]) -> float:
    """计算使仪器开口方向旋转到世界目标方向 ``target`` 所需的旋转角（弧度）。

    仪器局部开口方向取 ``openings[0].direction``，缺省 ``(0, -1)``。
    导轨场景里 target 为 ±u_short（朝向导轨），故结果通常是 90° 的整数倍。
    """
    if getattr(dev, "openings", None):
        lx, ly = dev.openings[0].direction
    else:
        lx, ly = 0.0, -1.0
    theta = math.atan2(target[1], target[0]) - math.atan2(ly, lx)
    # 归一化到 (-pi, pi]
    while theta <= -math.pi - _EPS:
        theta += 2 * math.pi
    while theta > math.pi + _EPS:
        theta -= 2 * math.pi
    return theta


def _pack_side(
    remaining: list["Device"],
    start: int,
    rail_len: float,
    c: float,
) -> list[tuple["Device", float, float]]:
    """贪心装箱：从 ``remaining[start:]`` 取最长前缀，使其铺满单侧导轨而不超长。

    判据 ``cost_w(i) = Σ W_rail + (i-1)·c ≤ L``。返回 ``[(dev, w_rail, l_out), ...]``。
    """
    chosen: list[tuple[Device, float, float]] = []
    cost = 0.0
    j = start
    while j < len(remaining):
        dev = remaining[j]
        w_rail, l_out, _ = _instrument_dims(dev)
        add = w_rail if not chosen else (c + w_rail)
        if cost + add <= rail_len + _EPS:
            cost += add
            chosen.append((dev, w_rail, l_out))
            j += 1
        else:
            break
    return chosen


def assign_and_place_instruments(
    arms: list[ArmPlacement],
    ordered_instruments: list["Device"],
    params: RailParams | None = None,
) -> list[InstrumentPlacement]:
    """阶段三：按实验步骤顺序把仪器布置到给定机械臂周围，返回坐标 + 朝向。

    确定性流程（不交给 agent）：

    1. 「哪台臂哪一侧」顺序：从下到上遍历机械臂，每台先左侧后右侧
       （bin 序列：臂1左、臂1右、臂2左、臂2右…）。
    2. 贪心装箱填满单侧：``cost_w(i)=ΣW_rail+(i-1)c ≤ L``。
    3. 四角反推坐标：左侧用左下角自下而上、右侧用右上角自上而下，
       同侧内边缘统一距臂 ``b``（围绕单臂呈逆时针）。
    4. 朝向 theta：据仪器 ``openings`` 使开口面向导轨（左侧朝 +u_short、右侧朝 −u_short）。

    本函数只对**给定的** arms 做确定性排布；「多退少补」（删空臂 / 补臂重排）
    由上层 :func:`layout_rail` 串联阶段二/三完成。装不下的仪器不会出现在结果里
    （留待上层据 ``len(placements) < len(ordered_instruments)`` 触发补臂）。

    Args:
        arms: 阶段二输出的机械臂列表（每台含四个角坐标）。
        ordered_instruments: 按实验流程排序的 :class:`Device` 列表（提供 bbox/openings）。
        params: 距离参数；为 ``None`` 时使用默认值。

    Returns:
        :class:`InstrumentPlacement` 列表（含 center/theta/arm_id/side/bbox）。
    """
    params = params or RailParams()
    if not arms or not ordered_instruments:
        return []

    b, c = params.b, params.c
    placements: list[InstrumentPlacement] = []

    bins: list[tuple[ArmPlacement, str]] = []
    for arm in arms:
        bins.append((arm, "left"))
        bins.append((arm, "right"))

    idx = 0
    n_inst = len(ordered_instruments)
    for arm, side in bins:
        if idx >= n_inst:
            break
        lb, rb, rt, _lt = arm.corners[0], arm.corners[1], arm.corners[2], arm.corners[3]
        u_long = _unit(_sub(arm.corners[3], lb))  # 左下 → 左上：沿导轨（从下到上）
        u_short = _unit(_sub(rb, lb))  # 左下 → 右下：从左到右（指向右侧）
        rail_len = _norm(_sub(arm.corners[3], lb))

        chosen = _pack_side(ordered_instruments, idx, rail_len, c)
        if not chosen:
            # 单台仪器宽度即超过导轨长度，换臂也无济于事 → 交由上层判定 leftover
            break

        if side == "left":
            anchor = lb  # 左下角，自下而上
            sign_long = 1.0
            short_sign = -1.0  # 向左远离臂
            target = u_short  # 开口朝右（指向导轨）
        else:  # right
            anchor = rt  # 右上角，自上而下
            sign_long = -1.0
            short_sign = 1.0  # 向右远离臂
            target = (-u_short[0], -u_short[1])  # 开口朝左（指向导轨）

        cum = 0.0
        for dev, w_rail, l_out in chosen:
            off_long = cum + w_rail / 2.0
            off_short = b + l_out / 2.0
            cx = anchor[0] + sign_long * off_long * u_long[0] + short_sign * off_short * u_short[0]
            cy = anchor[1] + sign_long * off_long * u_long[1] + short_sign * off_short * u_short[1]
            placements.append(
                InstrumentPlacement(
                    device_id=dev.id,
                    center=(cx, cy),
                    theta=_theta_to(dev, target),
                    arm_id=arm.id,
                    side=side,  # type: ignore[arg-type]
                    bbox=(float(dev.bbox[0]), float(dev.bbox[1])),
                )
            )
            cum += w_rail + c
        idx += len(chosen)

    return placements


def _rotated_half_extents(bbox: tuple[float, float], theta: float) -> tuple[float, float]:
    """返回旋转 theta 后的 AABB 半宽/半高。"""
    w, d = bbox
    cos_t = abs(math.cos(theta))
    sin_t = abs(math.sin(theta))
    return (w * cos_t + d * sin_t) / 2.0, (w * sin_t + d * cos_t) / 2.0


def validate_placements(
    placements: list[InstrumentPlacement],
    lab: "Lab",
    obstacles: list[Any] | None = None,
    arms: list[ArmPlacement] | None = None,
) -> list[RailConflict]:
    """阶段三：碰撞校验守卫。

    构造上仪器之间、仪器与机械臂之间靠固定间隙（b/c/d）不会碰，故**不做**搜索式避碰，
    仅做两类后置断言：

    ① 仪器 AABB vs 环境障碍物（``obstacles``，缺省取 ``lab.obstacles``）。
    ② 越墙断言：仪器 AABB 须落在房间矩形 ``[0,width]×[0,depth]`` 内。

    Args:
        placements: 阶段三输出（须含 ``bbox`` 与 ``theta`` 才能算 AABB）。
        lab: 实验室平面图。
        obstacles: 障碍物列表（``Obstacle``）；``None`` 时回落 ``lab.obstacles``。
        arms: 机械臂列表（预留，当前不参与判定）。

    Returns:
        :class:`RailConflict` 列表，为空表示通过校验。
    """
    conflicts: list[RailConflict] = []
    obs = obstacles if obstacles is not None else list(getattr(lab, "obstacles", []) or [])

    for p in placements:
        half_w, half_h = _rotated_half_extents(p.bbox, p.theta)
        x0, x1 = p.center[0] - half_w, p.center[0] + half_w
        y0, y1 = p.center[1] - half_h, p.center[1] + half_h

        # ② 越墙
        if x0 < -_EPS or y0 < -_EPS or x1 > lab.width + _EPS or y1 > lab.depth + _EPS:
            conflicts.append(
                RailConflict(
                    kind="out_of_bounds",
                    message=(
                        f"仪器 '{p.device_id}' 越界：AABB [{x0:.3f},{x1:.3f}]×"
                        f"[{y0:.3f},{y1:.3f}] 超出房间 {lab.width:.2f}×{lab.depth:.2f}。"
                    ),
                    suggestion="扩大房间、减小距离参数 b/d，或更换伸出更短的仪器。",
                )
            )

        # ① 环境障碍物碰撞
        for ob in obs:
            ox0 = float(getattr(ob, "x"))
            oy0 = float(getattr(ob, "y"))
            ox1 = ox0 + float(getattr(ob, "width"))
            oy1 = oy0 + float(getattr(ob, "depth"))
            if x0 < ox1 - _EPS and x1 > ox0 + _EPS and y0 < oy1 - _EPS and y1 > oy0 + _EPS:
                conflicts.append(
                    RailConflict(
                        kind="obstacle_collision",
                        message=(
                            f"仪器 '{p.device_id}' 与环境障碍物 "
                            f"[{ox0:.2f},{ox1:.2f}]×[{oy0:.2f},{oy1:.2f}] 重叠。"
                        ),
                        suggestion="移动障碍物、调整机械臂位置，或更换布局模式/参数。",
                    )
                )

    return conflicts


# ---------- 上层编排：串联阶段一/二/三 + 多退少补 ----------


def layout_rail(
    devices: list["Device"],
    ordered_instruments: list[str],
    lab: "Lab",
    arm_model: dict | None = None,
    params: RailParams | None = None,
    mode: Literal["near_wall", "centered"] = "near_wall",
    stack_model: str | dict | None = DEFAULT_STACK_MODEL,
) -> RailLayoutResult:
    """上层编排：可行性 → 布臂/堆栈 → 布仪器 → 多退少补 → 校验。

    「多退少补」反馈环全部收敛在本函数（不让 agent 中途手动判断）：

    - **补**：若按当前臂数装不下全部仪器，且未达长边几何上限 ``n_max``，则
      ``n_arm += 1`` 回阶段二重排后重算仪器坐标，直到装完或触顶。
    - **退**：装完后删除末尾「周围无仪器」的空臂（及其相邻堆栈）。

    Args:
        devices: 设备列表（含机械臂/仪器，提供 bbox/openings）。
        ordered_instruments: 按实验流程排序的仪器 id 列表。
        lab: 实验室平面图。
        arm_model: 机械臂型号 ``{L, working_radius, bbox}``；``None`` 时从 devices 识别。
        params: 距离参数；``None`` 时用默认值。
        mode: 第一台机械臂的摆放模式（``near_wall`` / ``centered``）。
        stack_model: 堆栈型号，默认 thermo_stacker。

    Returns:
        :class:`RailLayoutResult`。
    """
    params = params or RailParams()
    report = check_feasibility(
        devices, ordered_instruments, lab, arm_model, params, stack_model
    )

    if not ordered_instruments:
        return RailLayoutResult(report=report)

    # 不可行：返回原因（转为冲突），不强行布局
    if not report.feasible:
        conflicts = [
            RailConflict(kind="infeasible", message=msg, suggestion=sug)
            for msg, sug in zip(report.reasons, report.suggestions)
        ]
        return RailLayoutResult(
            report=report, leftover=list(ordered_instruments), conflicts=conflicts
        )

    device_map = {d.id: d for d in devices}
    ordered_devs: list[Device] = []
    for iid in ordered_instruments:
        dev = device_map.get(iid)
        if dev is None:
            raise ValueError(f"仪器 '{iid}' 不在 devices 中，无法布置。")
        ordered_devs.append(dev)

    n_cap = max(report.n_max, 1)
    n = max(min(report.n_arm, n_cap), 1)
    prev_placed = -1
    arm_stack: dict[str, list] = {"arms": [], "stacks": []}
    placements: list[InstrumentPlacement] = []

    while True:
        arm_stack = place_arms_and_stacks(
            lab, n, arm_model, params, mode, stack_model,
            l_max=report.l_max, devices=devices,
        )
        placements = assign_and_place_instruments(arm_stack["arms"], ordered_devs, params)
        placed = len(placements)
        if placed >= len(ordered_devs):
            break  # 全部布置完成
        if n >= n_cap:
            break  # 已达长边几何上限，无法再补臂
        if placed <= prev_placed:
            break  # 无进展（如单台仪器宽度超过导轨长度），停止补臂
        prev_placed = placed
        n += 1

    # 退：裁掉末尾「周围无仪器」的空臂及其相邻堆栈
    used_arm_ids = {p.arm_id for p in placements}
    last_used = 0
    for i, arm in enumerate(arm_stack["arms"], start=1):
        if arm.id in used_arm_ids:
            last_used = i
    arms_final = arm_stack["arms"][:last_used]
    stacks_final = arm_stack["stacks"][: max(last_used - 1, 0)]

    placed_ids = {p.device_id for p in placements}
    leftover = [d.id for d in ordered_devs if d.id not in placed_ids]

    conflicts = validate_placements(placements, lab, lab.obstacles, arms_final)
    if leftover:
        conflicts.append(
            RailConflict(
                kind="unplaced_instruments",
                message=(
                    f"仍有 {len(leftover)} 台仪器未能布置（{', '.join(leftover)}）："
                    f"长边几何上限 n_max={report.n_max}，补臂后仍装不下。"
                ),
                suggestion="扩大房间长边、更换更短导轨/更小占地的设备，或减少仪器数量。",
            )
        )

    return RailLayoutResult(
        arms=arms_final,
        stacks=stacks_final,
        placements=placements,
        report=report,
        leftover=leftover,
        conflicts=conflicts,
    )


# ---------- 序列化辅助 ----------


def conflicts_to_dicts(conflicts: list[RailConflict]) -> list[dict[str, Any]]:
    """``RailConflict`` 列表 → 可 JSON 序列化的 dict 列表。"""
    return [asdict(c) for c in conflicts]
