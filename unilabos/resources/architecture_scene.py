"""实验室建筑场景 JSON -> world_scene xacro / STL 生成。

输入为前端 / 楼层平面编辑器导出的建筑场景图 ``{nodes, rootNodeIds}``
（节点类型 site/building/level/wall/slab）。本模块把墙体与地板转换为
URDF 几何：墙与矩形地板用 ``<box>``，凹 / 三角 / 带洞地板拉伸为 STL 用
``<mesh>``，并产出可被 ``resource_visalization`` 像设备一样 ``<xacro:include>``
+ 实例化的 ``world_scene`` 宏（含 ``world_scene_srdf`` 宏）。

纯函数模块，不依赖 ROS。``shapely`` / ``trimesh`` 仅在非矩形地板分支懒加载，
缺失时退化为轴对齐包围盒，墙体与矩形板不受影响。

单位与 URDF 一致：米 / 弧度，不做 mm 换算。
"""

from __future__ import annotations

import itertools
import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from unilabos.utils import logger

# 默认几何参数（米）
SLAB_THICKNESS = 0.12
WALL_DEFAULT_THICKNESS = 0.12
WALL_DEFAULT_HEIGHT = 3.0

Element = Dict[str, Any]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def load_scene(src: Any) -> Optional[dict]:
    """接受 dict 或文件路径，返回场景 dict ``{nodes, rootNodeIds}``，失败返回 None。"""
    if src is None:
        return None
    if isinstance(src, dict):
        return src
    if isinstance(src, (str, os.PathLike)):
        path = os.fspath(src)
        if not os.path.exists(path):
            logger.warning(f"[scene] 场景文件不存在: {path}")
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[scene] 解析场景文件失败 {path}: {e}")
            return None
    logger.warning(f"[scene] 不支持的场景输入类型: {type(src)}")
    return None


def _sanitize(raw: str) -> str:
    """把任意 id 规整为合法的 URDF link / joint 名片段。"""
    s = re.sub(r"[^0-9a-zA-Z_]", "_", str(raw))
    if s and s[0].isdigit():
        s = "_" + s
    return s or "x"


def _rotate(x: float, y: float, yaw: float) -> Tuple[float, float]:
    """绕 Z 轴旋转 2D 点。"""
    c = math.cos(yaw)
    s = math.sin(yaw)
    return x * c - y * s, x * s + y * c


def _xy(pt: Any) -> Tuple[float, float]:
    """从 [x, y] 或 {x, y} 取 2D 坐标。"""
    if isinstance(pt, dict):
        return float(pt.get("x", 0.0)), float(pt.get("y", 0.0))
    return float(pt[0]), float(pt[1])


def _xyz(val: Any) -> Tuple[float, float, float]:
    """从 [x, y, z] 或 {x, y, z} 取 3D 坐标，缺省补 0。"""
    if val is None:
        return 0.0, 0.0, 0.0
    if isinstance(val, dict):
        return float(val.get("x", 0.0)), float(val.get("y", 0.0)), float(val.get("z", 0.0))
    seq = list(val)
    x = float(seq[0]) if len(seq) > 0 else 0.0
    y = float(seq[1]) if len(seq) > 1 else 0.0
    z = float(seq[2]) if len(seq) > 2 else 0.0
    return x, y, z


def _f(v: float) -> str:
    """紧凑浮点格式化（去掉无意义的尾部 0）。"""
    return f"{float(v):.6g}"


def _resolve(ref: Any, nodes: Dict[str, Any]) -> Optional[dict]:
    """children 项可能是 id 字符串、内联 dict，或仅含 id 的引用 dict。

    顶层 ``nodes`` 字典为权威来源：内联 dict 可能是导出时的过时 / 残缺副本
    （例如只携带部分 children），因此只要 id 命中 ``nodes`` 就优先取顶层节点。
    """
    if isinstance(ref, str):
        return nodes.get(ref)
    if isinstance(ref, dict):
        rid = ref.get("id")
        if rid is not None and rid in nodes:
            return nodes[rid]
        return ref
    return None


# ---------------------------------------------------------------------------
# 几何映射
# ---------------------------------------------------------------------------
def wall_to_box(
    wall: dict,
    base_xy: Tuple[float, float] = (0.0, 0.0),
    base_yaw: float = 0.0,
    z_offset: float = 0.0,
) -> Optional[Element]:
    """墙体 2D 线段 + 厚 + 高 -> box（叠加祖先 building 平移 / Z 偏航）。"""
    try:
        x0, y0 = _xy(wall["start"])
        x1, y1 = _xy(wall["end"])
    except (KeyError, TypeError, IndexError):
        logger.warning(f"[scene] wall {wall.get('id')} 缺少有效 start/end，跳过")
        return None
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        logger.warning(f"[scene] wall {wall.get('id')} 长度为 0，跳过")
        return None
    thickness = float(wall.get("thickness") or WALL_DEFAULT_THICKNESS)
    height = float(wall.get("height") or WALL_DEFAULT_HEIGHT)
    local_yaw = math.atan2(dy, dx)
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = _rotate(mx, my, base_yaw)
    z_base = float(wall.get("elevation") or 0.0)
    cz = z_offset + z_base + height / 2.0
    return {
        "link": "arch_" + _sanitize(wall.get("id", "wall")),
        "kind": "box",
        "size": (length, thickness, height),
        "xyz": (base_xy[0] + rx, base_xy[1] + ry, cz),
        "rpy": (0.0, 0.0, base_yaw + local_yaw),
        "category": "wall",
    }


def _rectangle(pts: List[Tuple[float, float]]) -> Optional[Tuple[float, float, float, float, float]]:
    """判定一组点是否构成（可旋转）矩形，返回 (cx, cy, w, d, yaw) 否则 None。"""
    p = list(pts)
    if len(p) >= 2 and abs(p[0][0] - p[-1][0]) < 1e-6 and abs(p[0][1] - p[-1][1]) < 1e-6:
        p = p[:-1]
    if len(p) != 4:
        return None

    def sub(a, b):
        return (b[0] - a[0], b[1] - a[1])

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1]

    def norm(a):
        return math.hypot(a[0], a[1])

    e0, e1, e2, e3 = sub(p[0], p[1]), sub(p[1], p[2]), sub(p[2], p[3]), sub(p[3], p[0])
    l0, l1, l2, l3 = norm(e0), norm(e1), norm(e2), norm(e3)
    if min(l0, l1, l2, l3) < 1e-6:
        return None
    # 对边等长
    if abs(l0 - l2) > 1e-3 * max(l0, l2, 1.0) or abs(l1 - l3) > 1e-3 * max(l1, l3, 1.0):
        return None
    # 相邻边垂直
    if abs(dot(e0, e1) / (l0 * l1)) > 1e-3:
        return None
    cx = sum(q[0] for q in p) / 4.0
    cy = sum(q[1] for q in p) / 4.0
    yaw = math.atan2(e0[1], e0[0])
    return cx, cy, l0, l1, yaw


def _bbox_box(
    pts: List[Tuple[float, float]],
    link: str,
    base_xy: Tuple[float, float],
    base_yaw: float,
    z_offset: float,
    elevation: float,
    thickness: float,
) -> Element:
    """无法生成 mesh 时退化为局部轴对齐包围盒。"""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    rx, ry = _rotate(cx, cy, base_yaw)
    return {
        "link": link,
        "kind": "box",
        "size": (max(maxx - minx, 1e-3), max(maxy - miny, 1e-3), thickness),
        "xyz": (base_xy[0] + rx, base_xy[1] + ry, z_offset + elevation + thickness / 2.0),
        "rpy": (0.0, 0.0, base_yaw),
        "category": "slab",
    }


def slab_to_geometry(
    slab: dict,
    temp_mesh_dir: Optional[str] = None,
    base_xy: Tuple[float, float] = (0.0, 0.0),
    base_yaw: float = 0.0,
    z_offset: float = 0.0,
    thickness: float = SLAB_THICKNESS,
) -> Optional[Element]:
    """地板多边形 -> 矩形用 box，凹 / 三角 / 带洞拉伸为 STL 用 mesh。"""
    polygon = slab.get("polygon")
    if not polygon or len(polygon) < 3:
        logger.warning(f"[scene] slab {slab.get('id')} polygon 无效，跳过")
        return None
    pts = [_xy(p) for p in polygon]
    holes = slab.get("holes") or []
    elevation = float(slab.get("elevation") or 0.0)
    thickness = float(slab.get("thickness") or thickness)
    link = "arch_" + _sanitize(slab.get("id", "slab"))

    if not holes:
        rect = _rectangle(pts)
        if rect is not None:
            cx, cy, w, d, yaw = rect
            rx, ry = _rotate(cx, cy, base_yaw)
            return {
                "link": link,
                "kind": "box",
                "size": (w, d, thickness),
                "xyz": (base_xy[0] + rx, base_xy[1] + ry, z_offset + elevation + thickness / 2.0),
                "rpy": (0.0, 0.0, base_yaw + yaw),
                "category": "slab",
            }

    # 非矩形 / 带洞 -> mesh
    if temp_mesh_dir is None:
        logger.warning(f"[scene] slab {slab.get('id')} 需要 mesh 但未提供 temp_mesh_dir，退化为包围盒")
        return _bbox_box(pts, link, base_xy, base_yaw, z_offset, elevation, thickness)
    try:
        from shapely.geometry import Polygon  # 懒加载，仅 mesh 分支需要
        import trimesh
    except ImportError as e:
        logger.warning(f"[scene] slab {slab.get('id')} 需要 shapely/trimesh（{e}），退化为包围盒")
        return _bbox_box(pts, link, base_xy, base_yaw, z_offset, elevation, thickness)

    try:
        hole_pts = [[_xy(h) for h in hole] for hole in holes] if holes else None
        poly = Polygon(pts, hole_pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        mesh = trimesh.creation.extrude_polygon(poly, height=thickness)
        centroid = mesh.centroid.copy()
        mesh.apply_translation(-centroid)  # 平移到局部原点，绝对位置交给 joint origin
        os.makedirs(temp_mesh_dir, exist_ok=True)
        mesh_file = f"slab_{_sanitize(slab.get('id', 'slab'))}.stl"
        mesh.export(os.path.join(temp_mesh_dir, mesh_file))
        rx, ry = _rotate(float(centroid[0]), float(centroid[1]), base_yaw)
        return {
            "link": link,
            "kind": "mesh",
            "mesh_file": mesh_file,
            "xyz": (base_xy[0] + rx, base_xy[1] + ry, z_offset + elevation + thickness / 2.0),
            "rpy": (0.0, 0.0, base_yaw),
            "category": "slab",
        }
    except Exception as e:  # noqa: BLE001 - 几何后端异常种类多，统一兜底退化
        logger.warning(f"[scene] slab {slab.get('id')} mesh 生成失败（{e}），退化为包围盒")
        return _bbox_box(pts, link, base_xy, base_yaw, z_offset, elevation, thickness)


# ---------------------------------------------------------------------------
# 场景遍历
# ---------------------------------------------------------------------------
def parse_scene(scene: Optional[dict], temp_mesh_dir: Optional[str] = None) -> List[Element]:
    """遍历 rootNodeIds + nodes，按 type 分流，累积 building 平移 / Z 偏航与 level 高度，
    返回统一 element 列表（每项含 link / kind / size 或 mesh_file / xyz / rpy）。"""
    if not scene:
        return []
    nodes = scene.get("nodes") or {}
    roots = scene.get("rootNodeIds") or []
    elements: List[Element] = []
    visited: set = set()

    def visit(node_ref: Any, base_xy: Tuple[float, float], base_yaw: float, z_offset: float) -> None:
        node = _resolve(node_ref, nodes)
        if not isinstance(node, dict):
            return
        nid = node.get("id")
        if nid is not None:
            if nid in visited:
                return
            visited.add(nid)
        ntype = node.get("type")
        children = node.get("children") or []

        if ntype == "building":
            px, py, pz = _xyz(node.get("position"))
            _, _, rz = _xyz(node.get("rotation"))
            wx, wy = _rotate(px, py, base_yaw)
            new_base = (base_xy[0] + wx, base_xy[1] + wy)
            new_yaw = base_yaw + rz
            new_z = z_offset + pz
            for ch in children:
                visit(ch, new_base, new_yaw, new_z)
            return

        if ntype == "level":
            lvl_z = z_offset
            for key in ("elevation", "base", "z"):
                if key in node:
                    try:
                        lvl_z = z_offset + float(node[key])
                        break
                    except (TypeError, ValueError):
                        pass
            for ch in children:
                visit(ch, base_xy, base_yaw, lvl_z)
            return

        if ntype == "wall":
            el = wall_to_box(node, base_xy, base_yaw, z_offset)
            if el:
                elements.append(el)
        elif ntype == "slab":
            el = slab_to_geometry(node, temp_mesh_dir, base_xy, base_yaw, z_offset)
            if el:
                elements.append(el)
        # site 仅作场地边界，不产几何；其余未知类型也继续向下遍历
        for ch in children:
            visit(ch, base_xy, base_yaw, z_offset)

    if roots:
        for r in roots:
            visit(r, (0.0, 0.0), 0.0, 0.0)
    else:
        for nid in list(nodes.keys()):
            visit(nid, (0.0, 0.0), 0.0, 0.0)
    return elements


# ---------------------------------------------------------------------------
# xacro / SRDF 生成
# ---------------------------------------------------------------------------
def generate_world_scene_xacro(elements: List[Element], mesh_path: str, out_path: str) -> str:
    """产出 world_scene 宏：world_base + 每个 wall/slab 的 link(box/mesh) + fixed joint。"""
    lines: List[str] = [
        '<?xml version="1.0" ?>',
        '<robot xmlns:xacro="http://ros.org/wiki/xacro" name="world_scene">',
        "  <xacro:macro name=\"world_scene\" "
        "params=\"mesh_path:='' parent_link:='world' x:=0 y:=0 z:=0 rx:=0 ry:=0 r:=0\">",
        '    <link name="world_base"/>',
        '    <joint name="world_base_joint" type="fixed">',
        '      <parent link="${parent_link}"/>',
        '      <child link="world_base"/>',
        '      <origin xyz="${x} ${y} ${z}" rpy="${rx} ${ry} ${r}"/>',
        "    </joint>",
    ]
    for el in elements:
        link = el["link"]
        if el["kind"] == "box":
            sx, sy, sz = el["size"]
            geom = f'<box size="{_f(sx)} {_f(sy)} {_f(sz)}"/>'
        else:
            geom = f'<mesh filename="file://${{mesh_path}}/temp_mesh/{el["mesh_file"]}"/>'
        color = "0.8 0.8 0.8 1" if el.get("category") == "wall" else "0.6 0.6 0.65 1"
        x, y, z = el["xyz"]
        rr, pp, yy = el["rpy"]
        lines += [
            f'    <link name="{link}">',
            "      <visual>",
            '        <origin xyz="0 0 0" rpy="0 0 0"/>',
            f"        <geometry>{geom}</geometry>",
            f'        <material name="{link}_mat"><color rgba="{color}"/></material>',
            "      </visual>",
            "      <collision>",
            '        <origin xyz="0 0 0" rpy="0 0 0"/>',
            f"        <geometry>{geom}</geometry>",
            "      </collision>",
            "    </link>",
            f'    <joint name="{link}_joint" type="fixed">',
            '      <parent link="world_base"/>',
            f'      <child link="{link}"/>',
            f'      <origin xyz="{_f(x)} {_f(y)} {_f(z)}" rpy="{_f(rr)} {_f(pp)} {_f(yy)}"/>',
            "    </joint>",
        ]
    lines += ["  </xacro:macro>", "</robot>", ""]
    content = "\n".join(lines)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


def generate_world_scene_srdf_xacro(elements: List[Element], out_path: str) -> str:
    """产出 world_scene_srdf 宏：建筑 link 两两 disable_collisions（静态互不检测，
    保留 robot↔arch 碰撞用于规划）。"""
    links = [el["link"] for el in elements]
    lines: List[str] = [
        '<?xml version="1.0" ?>',
        '<robot xmlns:xacro="http://ros.org/wiki/xacro" name="world_scene">',
        '  <xacro:macro name="world_scene_srdf" params="">',
    ]
    for a, b in itertools.combinations(links, 2):
        lines.append(f'    <disable_collisions link1="{a}" link2="{b}" reason="Adjacent"/>')
    if not links:
        lines.append("    <!-- no architecture links -->")
    lines += ["  </xacro:macro>", "</robot>", ""]
    content = "\n".join(lines)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path
