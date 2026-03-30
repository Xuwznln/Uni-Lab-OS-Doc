"""数据模型定义：Device, Lab, Placement, Constraint 及 API 请求/响应。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Opening:
    """设备的访问开口（用于方向约束）。"""

    # 设备局部坐标系中的方向单位向量，如 (0, -1) = 正前方
    direction: tuple[float, float] = (0.0, -1.0)
    label: str = ""


@dataclass
class Device:
    """设备描述。"""

    id: str
    name: str
    # 碰撞包围盒 (width along X, depth along Y)，单位：米
    bbox: tuple[float, float] = (0.6, 0.4)
    device_type: Literal["static", "articulation", "rigid"] = "static"
    # 以下为可选扩展字段（向后兼容）
    height: float = 0.4
    origin_offset: tuple[float, float] = (0.0, 0.0)
    openings: list[Opening] = field(default_factory=list)
    source: Literal["registry", "assets", "manual"] = "manual"
    model_path: str = ""
    model_type: str = ""
    thumbnail_url: str = ""


@dataclass
class Obstacle:
    """实验室内固定障碍物（矩形）。"""

    x: float
    y: float
    width: float
    depth: float


@dataclass
class Lab:
    """实验室平面图。"""

    width: float  # X 方向，单位：米
    depth: float  # Y 方向，单位：米
    obstacles: list[Obstacle] = field(default_factory=list)


@dataclass
class Placement:
    """单个设备的布局位姿。"""

    device_id: str
    x: float
    y: float
    theta: float  # 旋转角，弧度
    uuid: str = ""  # 前端分配的唯一标识，透传不生成

    def rotated_bbox(self, device: Device) -> tuple[float, float]:
        """返回旋转后的 AABB 尺寸 (half_w, half_h)。"""
        w, d = device.bbox
        cos_t = abs(math.cos(self.theta))
        sin_t = abs(math.sin(self.theta))
        half_w = (w * cos_t + d * sin_t) / 2
        half_h = (w * sin_t + d * cos_t) / 2
        return half_w, half_h


@dataclass
class Constraint:
    """约束规则。"""

    type: Literal["hard", "soft"]
    rule_name: str
    # 规则参数，含义因 rule_name 而异
    params: dict = field(default_factory=dict)
    # 仅 soft 约束使用
    weight: float = 1.0


@dataclass
class Intent:
    """LLM 可生成的语义化意图，由 interpreter 翻译为 Constraint 列表。"""

    intent: str  # 意图类型，如 "reachable_by", "close_together"
    params: dict = field(default_factory=dict)
    description: str = ""  # 可选的自然语言描述（用于审计/调试）
