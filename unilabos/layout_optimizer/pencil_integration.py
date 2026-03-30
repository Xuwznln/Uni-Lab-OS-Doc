"""初始布局生成：Pencil MCP 接口 + 行列式回退。

策略：
1. 尝试调用 Pencil AI MCP 生成初始布局
2. 若 Pencil 不可用或失败，回退到行列式放置算法

行列式回退逻辑：
- 设备按面积从大到小排序
- 沿 X 轴逐个放置，行满（超出 lab.width）则换行
- 设备间保留 margin 间距
- 所有设备 θ=0（朝向不变）
"""

from __future__ import annotations

import logging

from .models import Device, Lab, Placement

logger = logging.getLogger(__name__)

# 设备间最小间距（米）
DEFAULT_MARGIN = 0.3


def generate_initial_layout(
    devices: list[Device],
    lab: Lab,
    margin: float = DEFAULT_MARGIN,
) -> list[Placement]:
    """生成初始布局方案。

    优先尝试 Pencil MCP，失败则回退到行列式放置。

    Args:
        devices: 待放置的设备列表
        lab: 实验室平面图
        margin: 设备间最小间距

    Returns:
        初始布局 Placement 列表
    """
    # 尝试 Pencil MCP
    pencil_result = _try_pencil(devices, lab)
    if pencil_result is not None:
        logger.info("Using Pencil AI generated layout")
        return pencil_result

    # 回退到行列式
    logger.info("Pencil unavailable, using row-based fallback layout")
    return generate_fallback(devices, lab, margin)


def _try_pencil(
    devices: list[Device],
    lab: Lab,
) -> list[Placement] | None:
    """尝试通过 Pencil AI MCP 生成布局。

    当前 Pencil MCP 不可用，返回 None 触发回退。
    未来集成时，此函数应：
    1. 将设备 2D 投影 + 实验室平面图序列化为 Pencil 输入格式
    2. 调用 mcp__pencil_* 工具
    3. 解析返回的布局方案为 Placement 列表

    预留接口参数：
        - devices: 设备列表（id, bbox）
        - lab: 实验室尺寸
    """
    # TODO: 当 Pencil MCP 可用时实现
    # 预期调用方式：
    #   pencil_input = {
    #       "floor_plan": {"width": lab.width, "depth": lab.depth},
    #       "items": [{"id": d.id, "width": d.bbox[0], "depth": d.bbox[1]} for d in devices],
    #   }
    #   result = mcp__pencil_layout(pencil_input)
    #   return [Placement(device_id=r["id"], x=r["x"], y=r["y"], theta=r["theta"]) for r in result]
    return None


def generate_fallback(
    devices: list[Device],
    lab: Lab,
    margin: float = DEFAULT_MARGIN,
) -> list[Placement]:
    """行列式回退布局：按面积从大到小排序，逐行放置。

    放置规则：
    - 设备中心坐标，从左上角开始
    - 每行从 margin + half_width 开始
    - 行满（下一个设备右边缘超出 lab.width - margin）则换行
    - 行高取该行最大设备深度

    Args:
        devices: 待放置的设备列表
        lab: 实验室平面图
        margin: 设备间最小间距

    Returns:
        Placement 列表。若实验室空间不足，剩余设备堆叠在右下角并记录警告。
    """
    if not devices:
        return []

    # 按面积从大到小排序
    sorted_devices = sorted(devices, key=lambda d: d.bbox[0] * d.bbox[1], reverse=True)

    placements: list[Placement] = []
    cursor_x = margin
    cursor_y = margin
    row_height = 0.0

    for dev in sorted_devices:
        w, d = dev.bbox
        half_w = w / 2
        half_d = d / 2

        # 检查当前行是否放得下
        if cursor_x + half_w + margin > lab.width and placements:
            # 换行
            cursor_x = margin
            cursor_y += row_height + margin
            row_height = 0.0

        # 设备中心位置
        cx = cursor_x + half_w
        cy = cursor_y + half_d

        # 检查是否超出实验室深度
        if cy + half_d + margin > lab.depth:
            logger.warning(
                "Lab space insufficient for device '%s' (%s), "
                "placing at overflow position",
                dev.id,
                dev.bbox,
            )

        placements.append(Placement(device_id=dev.id, x=cx, y=cy, theta=0.0))

        # 更新游标
        cursor_x = cx + half_w + margin
        row_height = max(row_height, d)

    return placements
