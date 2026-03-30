"""从 STL/GLB 网格提取设备足迹（碰撞包围盒）。

运行方式:
    conda activate phase3
    python -m layout_optimizer.extract_footprints

输出 footprints.json 供 device_catalog.py 和 2D 规划器使用。

GLB root node rotation:
    每个设备的 GLB 文件包含根节点旋转四元数，定义 STL 原生坐标到 glTF Y-up
    约定的轴映射。extract_one_device() 读取 GLB JSON，提取旋转矩阵，
    应用到 STL 包围盒后按 glTF 约定提取 2D 足迹 (X=width, Z=depth, Y=height)。
    GLB scale 不应用——STL 文件已是米制坐标。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

# 测试设备的开口方向（手动标注）
# direction 为设备局部坐标系中的单位向量，[0, -1] 表示设备正前方
MANUAL_OPENINGS: dict[str, list[dict]] = {
    "agilent_bravo": [{"direction": [0, -1], "label": "front_plate_slot"}],
    "opentrons_liquid_handler": [{"direction": [0, -1], "label": "front_deck"}],
    "opentrons_flex": [{"direction": [0, -1], "label": "front_deck"}],
    "thermo_orbitor_rs2_hotel": [{"direction": [0, -1], "label": "front_door"}],
    "hamilton_star": [{"direction": [0, -1], "label": "front_deck"}],
    "tecan_spark_plate_reader": [{"direction": [0, -1], "label": "front_slot"}],
    "highres_bio_plate_hotel_12": [{"direction": [0, -1], "label": "front_shelf"}],
    "beckman_coulter_orbital_shaker_alp": [],
    "liconic_str44_incubator": [{"direction": [0, -1], "label": "front_door"}],
    "elite_robot": [],  # 机械臂，无开口
}

# 手动尺寸后备（trimesh 提取失败时使用）
FALLBACK_SIZES: dict[str, tuple[float, float, float]] = {
    "elite_robot": (0.20, 0.20, 0.10),
    "elite_cs66_arm": (0.20, 0.20, 0.10),
    "elite_cs612_arm": (0.20, 0.20, 0.10),
}


def extract_openings_from_xacro(
    xacro_path: Path,
    bbox_center_xy: tuple[float, float],
    bbox_size_xy: tuple[float, float],
) -> list[dict]:
    """从 XACRO 文件自动提取设备开口方向。

    解析 fixed joint 中包含 "socket" 的关节，计算其 XY 质心，与包围盒中心比较，
    映射到最近的基本方向。

    Args:
        xacro_path: modal.xacro 文件路径
        bbox_center_xy: 包围盒 XY 中心 (cx, cy)
        bbox_size_xy: 包围盒 XY 尺寸 (w, d)

    Returns:
        [{"direction": [dx, dy], "label": "auto_xacro"}] 或 []
    """
    # --- 方法1: ElementTree 解析（忽略 xacro 命名空间） ---
    socket_positions: list[tuple[float, float]] = []

    try:
        xacro_text = xacro_path.read_text(encoding="utf-8", errors="replace")

        # 去掉 xacro 命名空间前缀，避免 ElementTree 解析失败
        xacro_text_clean = re.sub(r'\bxacro:', '', xacro_text)

        root = ET.fromstring(xacro_text_clean)

        for joint in root.iter("joint"):
            joint_name = joint.get("name", "")
            joint_type = joint.get("type", "")
            if "socket" not in joint_name.lower():
                continue
            if joint_type != "fixed":
                continue
            origin = joint.find("origin")
            if origin is None:
                continue
            xyz_str = origin.get("xyz", "")
            if not xyz_str:
                continue
            parts = xyz_str.split()
            if len(parts) < 2:
                continue
            try:
                x = float(parts[0])
                y = float(parts[1])
                socket_positions.append((x, y))
            except ValueError:
                continue

    except ET.ParseError as e:
        logger.debug("ElementTree parse error for %s: %s — falling back to regex", xacro_path, e)

    # --- 方法2: 正则表达式后备（当 ElementTree 失败或无结果时） ---
    if not socket_positions:
        try:
            xacro_text = xacro_path.read_text(encoding="utf-8", errors="replace")
            # 匹配包含 "socket" 的 joint 块，提取 origin xyz
            joint_blocks = re.findall(
                r'<joint\s[^>]*name=["\'][^"\']*socket[^"\']*["\'][^>]*>.*?</joint>',
                xacro_text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            for block in joint_blocks:
                # 只处理 fixed 类型
                if 'type="fixed"' not in block and "type='fixed'" not in block:
                    continue
                xyz_match = re.search(r'<origin[^>]*xyz=["\']([^"\']+)["\']', block)
                if not xyz_match:
                    continue
                parts = xyz_match.group(1).split()
                if len(parts) < 2:
                    continue
                try:
                    x = float(parts[0])
                    y = float(parts[1])
                    socket_positions.append((x, y))
                except ValueError:
                    continue
        except Exception as e:
            logger.debug("Regex fallback also failed for %s: %s", xacro_path, e)

    if not socket_positions:
        return []

    # 计算 socket XY 质心
    cx_sock = sum(p[0] for p in socket_positions) / len(socket_positions)
    cy_sock = sum(p[1] for p in socket_positions) / len(socket_positions)

    # 方向向量：从包围盒中心指向 socket 质心
    dx = cx_sock - bbox_center_xy[0]
    dy = cy_sock - bbox_center_xy[1]

    # 如果 socket 质心非常靠近包围盒中心（<5% 尺寸），判断为顶部装载
    threshold = 0.05 * max(bbox_size_xy[0], bbox_size_xy[1], 1e-6)
    if math.hypot(dx, dy) < threshold:
        logger.debug(
            "%s: socket centroid too close to bbox center (dist=%.4f, threshold=%.4f) → top-loading",
            xacro_path.parent.name,
            math.hypot(dx, dy),
            threshold,
        )
        return []

    # 映射到最近基本方向
    # socket 质心指示交互区在设备哪一侧，而 opening direction 是从该面
    # 向外的法线方向（与质心偏移同向），这里的 dx/dy 已经是从包围盒中心
    # 指向 socket 区域的方向，即 opening 朝外的方向
    # 注意：在 uni-lab-assets 中，大多数设备 front 在 Y=0 而 body 在 -Y，
    # 所以 socket 集中在 +Y 侧（靠近 Y=0 前端），bbox 中心在 -Y/2。
    # 方向 center→socket = +Y，但 "opening faces front" 在手动标注中
    # 写作 [0, -1]（法线向外=向操作者方向）。
    # 因此需要取反：opening direction = -(center→socket)
    if abs(dx) >= abs(dy):
        cardinal = [-1, 0] if dx > 0 else [1, 0]
    else:
        cardinal = [0, -1] if dy > 0 else [0, 1]

    logger.debug(
        "%s: %d socket joints → centroid=(%.3f, %.3f) dir=%s",
        xacro_path.parent.name,
        len(socket_positions),
        cx_sock,
        cy_sock,
        cardinal,
    )
    return [{"direction": cardinal, "label": "auto_xacro"}]


def _find_mesh_files(device_dir: Path) -> list[Path]:
    """查找设备目录中的所有 STL/GLB 网格文件。"""
    mesh_files: list[Path] = []
    meshes_dir = device_dir / "meshes"
    if not meshes_dir.exists():
        return mesh_files

    # uni-lab-assets 结构: meshes/*.stl, meshes/*.glb
    for f in meshes_dir.iterdir():
        if f.suffix.lower() in (".stl", ".glb"):
            mesh_files.append(f)

    # registry 结构: meshes/<variant>/collision/*.stl
    if not mesh_files:
        for variant_dir in meshes_dir.iterdir():
            if variant_dir.is_dir():
                collision_dir = variant_dir / "collision"
                if collision_dir.exists():
                    for f in collision_dir.iterdir():
                        if f.suffix.lower() == ".stl":
                            mesh_files.append(f)
                    if mesh_files:
                        break  # 使用找到的第一个变体

    return sorted(mesh_files)


def _find_best_model_file(device_dir: Path) -> tuple[str, str]:
    """找到最佳可展示的模型文件。优先 GLB > STL。

    Returns:
        (relative_path, model_type) e.g. ("meshes/0_base.glb", "gltf")
    """
    meshes_dir = device_dir / "meshes"
    if not meshes_dir.exists():
        return "", ""

    glbs = sorted(meshes_dir.glob("*.glb"))
    if glbs:
        return f"meshes/{glbs[0].name}", "gltf"

    stls = sorted(f for f in meshes_dir.glob("*.stl") if f.suffix == ".stl")
    if not stls:
        stls = sorted(f for f in meshes_dir.glob("*.STL"))
    if stls:
        return f"meshes/{stls[0].name}", "stl"

    return "", ""


def _find_thumbnail(device_dir: Path) -> str:
    """查找设备目录中的第一个 PNG 缩略图。"""
    pngs = sorted(device_dir.glob("*.png"))
    if pngs:
        return pngs[0].name
    return ""


def _read_glb_json(glb_path: Path) -> dict | None:
    """Read the JSON chunk from a GLB (Binary glTF) file.

    GLB structure: 12-byte header + chunks. Chunk 0 is JSON.
    Returns parsed dict or None on failure.
    """
    try:
        with open(glb_path, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return None
            magic, version, length = struct.unpack("<III", header)
            if magic != 0x46546C67:  # 'glTF'
                return None
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                return None
            chunk_length, chunk_type = struct.unpack("<II", chunk_header)
            if chunk_type != 0x4E4F534A:  # 'JSON'
                return None
            json_bytes = f.read(chunk_length)
            return json.loads(json_bytes)
    except Exception as e:
        logger.debug("Failed to read GLB JSON from %s: %s", glb_path, e)
        return None


def _quat_to_matrix(q: list[float]) -> list[list[float]]:
    """Convert quaternion [x, y, z, w] to 3×3 rotation matrix."""
    x, y, z, w = q
    return [
        [1 - 2*(y*y + z*z),     2*(x*y - z*w),     2*(x*z + y*w)],
        [    2*(x*y + z*w), 1 - 2*(x*x + z*z),     2*(y*z - x*w)],
        [    2*(x*z - y*w),     2*(y*z + x*w), 1 - 2*(x*x + y*y)],
    ]


def _get_glb_root_rotation(device_dir: Path) -> list[list[float]] | None:
    """Extract root node rotation matrix from the first GLB in device_dir/meshes/.

    Only rotation is extracted — GLB scale is NOT applied because STL files
    are already in meters while GLB scale converts GLB mesh units (often mm)
    to scene units. Since we read STL directly, scale is irrelevant.

    Returns 3×3 rotation matrix or None if no GLB or no rotation found.
    """
    meshes_dir = device_dir / "meshes"
    if not meshes_dir.exists():
        return None
    glbs = sorted(meshes_dir.glob("*.glb"))
    if not glbs:
        return None

    gltf = _read_glb_json(glbs[0])
    if gltf is None:
        return None

    nodes = gltf.get("nodes", [])
    if not nodes:
        return None

    root = nodes[0]
    rotation = root.get("rotation")
    if rotation is None:
        return None

    # Skip identity quaternion [0,0,0,1]
    x, y, z, w = rotation
    if abs(x) < 1e-9 and abs(y) < 1e-9 and abs(z) < 1e-9 and abs(w - 1.0) < 1e-9:
        return None

    return _quat_to_matrix(rotation)


def _apply_rotation_to_bbox(
    stl_min: list[float], stl_max: list[float],
    rot: list[list[float]],
) -> tuple[list[float], list[float]]:
    """Apply rotation to an axis-aligned bounding box.

    Transforms all 8 corners of the STL AABB through rotation,
    then computes the new AABB in glTF space.
    """
    corners = []
    for x in (stl_min[0], stl_max[0]):
        for y in (stl_min[1], stl_max[1]):
            for z in (stl_min[2], stl_max[2]):
                tx = rot[0][0]*x + rot[0][1]*y + rot[0][2]*z
                ty = rot[1][0]*x + rot[1][1]*y + rot[1][2]*z
                tz = rot[2][0]*x + rot[2][1]*y + rot[2][2]*z
                corners.append((tx, ty, tz))

    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    zs = [c[2] for c in corners]
    return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]


def extract_one_device(device_dir: Path) -> dict | None:
    """提取单个设备的足迹信息。"""
    try:
        import trimesh
    except ImportError:
        logger.error("trimesh not installed. Run: pip install trimesh")
        return None

    mesh_files = _find_mesh_files(device_dir)
    if not mesh_files:
        return None

    # 加载所有网格部件并计算联合包围盒
    meshes = []
    for f in mesh_files:
        try:
            m = trimesh.load(str(f), force="mesh")
            if hasattr(m, "bounds") and m.bounds is not None:
                meshes.append(m)
        except Exception as e:
            logger.warning("Failed to load %s: %s", f, e)

    if not meshes:
        return None

    if len(meshes) == 1:
        combined = meshes[0]
    else:
        combined = trimesh.util.concatenate(meshes)

    bounds = combined.bounds
    stl_min = [float(bounds[0][i]) for i in range(3)]
    stl_max = [float(bounds[1][i]) for i in range(3)]

    # 应用 GLB 根节点旋转到 STL 包围盒（scale 不应用 — STL 已是米制）
    # glTF 约定: X=right, Y=up, Z=forward → 2D 足迹取 X 和 Z, 高度取 Y
    rot = _get_glb_root_rotation(device_dir)
    if rot is not None:
        t_min, t_max = _apply_rotation_to_bbox(stl_min, stl_max, rot)
        t_size = [t_max[i] - t_min[i] for i in range(3)]
        t_center = [(t_min[i] + t_max[i]) / 2 for i in range(3)]
        # glTF Y-up: X=width, Z=depth, Y=height
        bbox_w = round(t_size[0], 4)
        bbox_d = round(t_size[2], 4)
        height = round(t_size[1], 4)
        origin_offset = [round(t_center[0], 4), round(t_center[2], 4)]
        logger.debug(
            "%s: GLB rotation applied → bbox=[%.3f, %.3f] height=%.3f",
            device_dir.name, bbox_w, bbox_d, height,
        )
    else:
        # 无 GLB 或 identity rotation → 沿用原始 STL 坐标 (X=width, Y=depth, Z=height)
        size = [stl_max[i] - stl_min[i] for i in range(3)]
        center = [(stl_min[i] + stl_max[i]) / 2 for i in range(3)]
        bbox_w = round(size[0], 4)
        bbox_d = round(size[1], 4)
        height = round(size[2], 4)
        origin_offset = [round(center[0], 4), round(center[1], 4)]

    model_file, model_type = _find_best_model_file(device_dir)
    thumbnail_file = _find_thumbnail(device_dir)

    device_id = device_dir.name

    # 确定 openings：手动标注优先，否则尝试从 XACRO 自动提取
    # 注意：XACRO socket 坐标是 STL 原生坐标系，这里传入变换后的 bbox
    if device_id in MANUAL_OPENINGS:
        openings = MANUAL_OPENINGS[device_id]
    else:
        xacro_path = device_dir / "modal.xacro"
        if xacro_path.exists():
            openings = extract_openings_from_xacro(
                xacro_path,
                bbox_center_xy=(origin_offset[0], origin_offset[1]),
                bbox_size_xy=(bbox_w, bbox_d),
            )
        else:
            openings = []

    result: dict = {
        "bbox": [bbox_w, bbox_d],
        "height": height,
        "origin_offset": origin_offset,
        "model_file": model_file,
        "model_type": model_type,
        "thumbnail_file": thumbnail_file,
        "openings": openings,
    }
    return result


def extract_all(
    assets_dir: Path | None = None,
    registry_dir: Path | None = None,
    device_ids: list[str] | None = None,
) -> dict[str, dict]:
    """提取所有（或指定）设备的足迹。

    Args:
        assets_dir: uni-lab-assets/device_models/ 路径
        registry_dir: Uni-Lab-OS/unilabos/device_mesh/devices/ 路径
        device_ids: 仅提取指定设备（None = 全部扫描）

    Returns:
        {device_id: footprint_dict}
    """
    results: dict[str, dict] = {}

    dirs_to_scan: list[tuple[Path, str]] = []

    if assets_dir and assets_dir.exists():
        for d in sorted(assets_dir.iterdir()):
            if d.is_dir() and (device_ids is None or d.name in device_ids):
                dirs_to_scan.append((d, "assets"))

    if registry_dir and registry_dir.exists():
        for d in sorted(registry_dir.iterdir()):
            if d.is_dir() and (device_ids is None or d.name in device_ids):
                if d.name not in results:  # assets 已有的不重复扫描
                    dirs_to_scan.append((d, "registry"))

    for device_dir, source in dirs_to_scan:
        device_id = device_dir.name
        if device_id in results:
            continue

        footprint = extract_one_device(device_dir)
        if footprint:
            footprint["source"] = source
            results[device_id] = footprint
            logger.info(
                "Extracted %s: bbox=%s height=%.3f source=%s",
                device_id,
                footprint["bbox"],
                footprint["height"],
                source,
            )

    # 统计自动提取的 openings 数量
    auto_xacro_count = sum(
        1
        for fp in results.values()
        if any(o.get("label") == "auto_xacro" for o in fp.get("openings", []))
    )
    logger.info(
        "Auto-extracted openings from XACRO for %d / %d devices",
        auto_xacro_count,
        len(results),
    )

    # 手动后备
    for dev_id, (w, d, h) in FALLBACK_SIZES.items():
        if dev_id not in results:
            results[dev_id] = {
                "bbox": [w, d],
                "height": h,
                "origin_offset": [0.0, 0.0],
                "model_file": "",
                "model_type": "",
                "thumbnail_file": "",
                "openings": MANUAL_OPENINGS.get(dev_id, []),
                "source": "manual",
            }

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract device footprints from STL/GLB meshes"
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "uni-lab-assets" / "device_models",
        help="Path to uni-lab-assets/device_models/",
    )
    parser.add_argument(
        "--registry-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "Uni-Lab-OS" / "unilabos" / "device_mesh" / "devices",
        help="Path to Uni-Lab-OS device_mesh/devices/",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "footprints.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--devices",
        nargs="*",
        default=None,
        help="Only extract these device IDs (default: all)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logger.info("Assets dir: %s (exists=%s)", args.assets_dir, args.assets_dir.exists())
    logger.info("Registry dir: %s (exists=%s)", args.registry_dir, args.registry_dir.exists())

    results = extract_all(
        assets_dir=args.assets_dir,
        registry_dir=args.registry_dir,
        device_ids=args.devices,
    )

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    logger.info("Wrote %d devices to %s", len(results), args.output)


if __name__ == "__main__":
    main()
