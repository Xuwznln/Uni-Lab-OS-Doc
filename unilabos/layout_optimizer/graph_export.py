"""布局结果 -> edge 的 Material graph（响应返回 + 上传 /edge/material 同一份）。

纯函数，无 ROS / 网络依赖。以 edge 真标准（``labortest.json`` 的 ``Material``/WSNode 模式）
为唯一基准：``placements_to_graph`` 把已归一化的 placed 设备合成「仅设备」的 edge graph
``{nodes:[Material...], edges:[]}``。每个节点字段：

- ``uuid``         实例唯一 id（material_node_id / 自动生成）
- ``parent_uuid``  挂载父 uuid（自动布局无挂载 -> ""）
- ``id``           数据库 name（type 规整后；同 type 多个时追加数字后缀）
- ``name``         显示名（display_name 缺省回退到 id/type）
- ``type``         固定 "device"
- ``class``        设备类（= 输入 type）
- ``parent``       父类字符串（无挂载 -> ""）
- ``pose``         完整对象：``layout`` / ``position``(毫米) / ``position_3d`` / ``size`` /
                   ``scale`` / ``rotation``(**弧度**) / ``extra`` / ``cross_section_type``；
                   其中 ``extra`` 固定为 ``{parent_link, mount_point}`` 两键（可空串）
- ``config`` / ``data`` / ``schema``  缺省空 dict
- ``description``  缺省 ""
- ``model``        ``{mesh, path, type, format}``（注册表解析所得）
- ``position``     顶层冗余坐标(毫米)，与 ``pose.position`` 一致
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# 米 -> 毫米（API/DB 位置单位）
M_TO_MM = 1000.0

_ID_SANITIZE_RE = re.compile(r"[^0-9A-Za-z_]+")


def _sanitize_id(raw: str) -> str:
    """type/class -> 数据库 name 风格 id：非 [0-9A-Za-z_] 一律转 ``_``。

    例：``"robotic_arm.SCARA_with_slider.moveit.virtual"`` ->
        ``"robotic_arm_SCARA_with_slider_moveit_virtual"``。
    """
    return _ID_SANITIZE_RE.sub("_", raw).strip("_")


def _normalize_model(model: Dict[str, Any] | None, cls: str) -> Dict[str, Any]:
    """确保 model 始终是完整 4 键结构（与 graph_10_format_example 一致）。"""
    source = dict(model or {})
    return {
        "mesh": str(source.get("mesh") or cls or ""),
        "path": str(source.get("path") or ""),
        "type": str(source.get("type") or "device"),
        "format": str(source.get("format") or "xacro"),
    }


def _normalize_extra(entry: Dict[str, Any]) -> Dict[str, str]:
    """把挂载语义统一到 pose.extra.{parent_link,mount_point}。"""
    extra = entry.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}
    parent_link = (
        extra.get("parent_link")
        or extra.get("parentLink")
        or entry.get("parent_link")
        or entry.get("parentLink")
        or ""
    )
    mount_point = (
        extra.get("mount_point")
        or extra.get("mountPoint")
        or entry.get("mount_point")
        or entry.get("mountPoint")
        or ""
    )
    return {"parent_link": str(parent_link), "mount_point": str(mount_point)}


def placements_to_graph(placed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """已归一化的 placed 设备 -> 仅设备的 edge Material graph ``{nodes, edges}``。

    Args:
        placed: 每项需含：
            - ``uuid`` / ``material_node_id`` (str)：实例唯一 id
            - ``id`` (str)：catalog type（= class，用于生成 id/class）
            - ``x`` / ``y`` / ``z`` (米)：building 坐标(已含 origin 偏移)
            - ``theta`` (弧度)：yaw
            - ``display_name`` (str, 可选)：显示名
            - ``model`` (dict, 可选)：{mesh, path, type, format}
            - ``config`` / ``data`` (dict, 可选)
            - ``parent_uuid`` / ``parent`` (str, 可选)：挂载关系
    """
    nodes: List[Dict[str, Any]] = []
    id_counts: Dict[str, int] = {}
    class_by_uuid: Dict[str, str] = {}
    for entry in placed:
        raw_uuid = str(
            entry.get("uuid") or entry.get("material_node_id") or entry.get("id") or ""
        )
        raw_cls = str(entry.get("id") or "")
        if raw_uuid:
            class_by_uuid[raw_uuid] = raw_cls

    for entry in placed:
        uuid = str(
            entry.get("uuid") or entry.get("material_node_id") or entry.get("id") or ""
        )
        cls = str(entry.get("id") or "")
        base_id = _sanitize_id(cls) or uuid
        # 同 type 多实例：第 1 个用 base，其后追加数字后缀（与 labortest 的 serial/serial1 一致）
        seen = id_counts.get(base_id, 0)
        node_id = base_id if seen == 0 else f"{base_id}{seen}"
        id_counts[base_id] = seen + 1

        x_mm = float(entry.get("x", 0.0)) * M_TO_MM
        y_mm = float(entry.get("y", 0.0)) * M_TO_MM
        z_mm = float(entry.get("z", 0.0)) * M_TO_MM
        position = {"x": x_mm, "y": y_mm, "z": z_mm}
        parent_uuid = str(entry.get("parent_uuid") or entry.get("parentUuid") or "")
        parent = str(entry.get("parent") or class_by_uuid.get(parent_uuid) or "")
        extra = _normalize_extra(entry)
        model = _normalize_model(entry.get("model"), cls)

        nodes.append(
            {
                "uuid": uuid,
                "parent_uuid": parent_uuid,
                "id": node_id,
                "name": str(entry.get("display_name") or node_id or cls),
                "type": "device",
                "class": cls,
                "parent": parent,
                "pose": {
                    "layout": "x-y",
                    "position": dict(position),
                    "position_3d": dict(position),
                    "size": {"width": 0.0, "height": 0.0, "depth": 0.0},
                    "scale": {"x": 1.0, "y": 1.0, "z": 1.0},
                    # yaw 用弧度（edge graph 标准）
                    "rotation": {"x": 0.0, "y": 0.0, "z": float(entry.get("theta", 0.0))},
                    "extra": extra,
                    "cross_section_type": "rectangle",
                },
                "config": dict(entry.get("config") or {}),
                "data": dict(entry.get("data") or {}),
                "schema": {},
                "description": "",
                "model": model,
                "position": dict(position),
            }
        )
    return {"nodes": nodes, "edges": []}
