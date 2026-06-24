"""Pascal 场景坐标 ↔ Open-RMF 坐标的集中换算（#18 §4.2 / §6.6）。

这是全链路一致性的唯一坐标入口，纯函数、无外部依赖，必须全单测覆盖。

差异来源：
- 单位：Pascal `pose.position` 为 mm；RMF runtime / nav_graph 为 m。
- Y 轴：Pascal/编辑器 Y 向下（屏幕系）；RMF Cartesian Y 向上 → 需翻转。
- 朝向：RMF `Location.yaw` 为弧度（atan2 约定）；Y 翻转时 yaw 取负。

策略：编译器首选直接输出 `coordinate_system: cartesian_meters`，在写盘前完成
mm→m 与 Y 翻转，绕开 `reference_image` 像素 + measurement 标定链路。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Tuple

DEFAULT_SCALE = 0.001  # mm -> m
DEFAULT_Y_FLIP = True


@dataclass(frozen=True)
class TransformConfig:
    """坐标换算参数，来源 RMFConfig.coordinate_scale / coordinate_y_flip。"""

    scale: float = DEFAULT_SCALE
    y_flip: bool = DEFAULT_Y_FLIP


def pascal_to_rmf(
    x_mm: float,
    y_mm: float,
    rotation_z: float = 0.0,
    scale: float = DEFAULT_SCALE,
    y_flip: bool = DEFAULT_Y_FLIP,
) -> Tuple[float, float, float]:
    """Pascal (mm, 屏幕系, rotation.z 弧度) → RMF (m, Cartesian, yaw 弧度)。

    返回 ``(x_m, y_m, yaw_rad)``。
    """
    x_m = x_mm * scale
    y_m = -(y_mm * scale) if y_flip else (y_mm * scale)
    yaw = -rotation_z if y_flip else rotation_z
    return (x_m, y_m, normalize_yaw(yaw))


def rmf_to_pascal(
    x_m: float,
    y_m: float,
    yaw_rad: float = 0.0,
    scale: float = DEFAULT_SCALE,
    y_flip: bool = DEFAULT_Y_FLIP,
) -> Tuple[float, float, float]:
    """RMF (m) → Pascal (mm) 的逆变换，用于 round-trip 校验与回流标注。

    返回 ``(x_mm, y_mm, rotation_z)``。
    """
    if scale == 0:
        raise ValueError("scale must be non-zero")
    x_mm = x_m / scale
    y_mm = -(y_m / scale) if y_flip else (y_m / scale)
    rotation_z = -yaw_rad if y_flip else yaw_rad
    return (x_mm, y_mm, rotation_z)


def pascal_point_to_rmf(
    point_mm: Iterable[float],
    scale: float = DEFAULT_SCALE,
    y_flip: bool = DEFAULT_Y_FLIP,
) -> List[float]:
    """换算一个 (x, y[, z]) 点（mm → m）。z 仅缩放、不翻转。"""
    coords = list(point_mm)
    if len(coords) < 2:
        raise ValueError("point must have at least x, y")
    x_m, y_m, _ = pascal_to_rmf(coords[0], coords[1], 0.0, scale=scale, y_flip=y_flip)
    out = [x_m, y_m]
    if len(coords) >= 3:
        out.append(coords[2] * scale)
    return out


def normalize_yaw(yaw_rad: float) -> float:
    """把 yaw 归一化到 (-π, π]。"""
    two_pi = 2.0 * math.pi
    yaw = math.fmod(yaw_rad, two_pi)
    if yaw <= -math.pi:
        yaw += two_pi
    elif yaw > math.pi:
        yaw -= two_pi
    return yaw


def deg_to_rad(deg: float) -> float:
    """度 → 弧度（RMF go_to orientation 的换算，与 dispatch_go_to_place.py 一致）。"""
    return deg * math.pi / 180.0
