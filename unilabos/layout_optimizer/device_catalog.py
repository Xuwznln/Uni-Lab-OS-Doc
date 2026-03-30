"""双源设备目录：从 uni-lab-assets 和 Uni-Lab-OS registry 加载设备。

数据流：
  footprints.json (离线提取) + data.json (资产树) + registry device_mesh dirs
  → merge → Device 列表

footprints.json 由 extract_footprints.py 生成，包含碰撞包围盒、开口方向等。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import Device, Opening

logger = logging.getLogger(__name__)

# 默认路径（相对于本文件）
_THIS_DIR = Path(__file__).resolve().parent
_DEFAULT_FOOTPRINTS = _THIS_DIR / "footprints.json"

# 手动后备尺寸（trimesh 不可用时）
KNOWN_SIZES: dict[str, tuple[float, float]] = {
    "elite_cs66_arm": (0.20, 0.20),
    "elite_cs612_arm": (0.20, 0.20),
    "ot2": (0.62, 0.50),
    "agilent_bravo": (0.80, 0.65),
    "thermo_orbitor_rs2": (0.45, 0.55),
    "hplc_station": (0.60, 0.50),
    "1_3m_hamilton_table": (1.30, 0.75),
}

DEFAULT_BBOX: tuple[float, float] = (0.6, 0.4)

# ---------- footprints.json 加载 ----------

_footprints_cache: dict[str, dict] | None = None


def load_footprints(path: str | Path = _DEFAULT_FOOTPRINTS) -> dict[str, dict]:
    """加载 footprints.json 并缓存。"""
    global _footprints_cache
    if _footprints_cache is not None:
        return _footprints_cache

    p = Path(path)
    if not p.exists():
        logger.warning("footprints.json not found at %s", p)
        _footprints_cache = {}
        return _footprints_cache

    with open(p) as f:
        _footprints_cache = json.load(f)
    logger.info("Loaded %d footprints from %s", len(_footprints_cache), p)
    return _footprints_cache


def reset_footprints_cache() -> None:
    """清除缓存（测试用）。"""
    global _footprints_cache
    _footprints_cache = None


# ---------- 从 footprints 构建 Device ----------


def _footprint_to_device(
    device_id: str,
    fp: dict,
    name: str = "",
    models_url_prefix: str = "/models",
) -> Device:
    """从 footprints.json 条目创建 Device。"""
    bbox = tuple(fp.get("bbox", DEFAULT_BBOX))
    openings = [
        Opening(direction=tuple(o["direction"]), label=o.get("label", ""))
        for o in fp.get("openings", [])
    ]

    model_file = fp.get("model_file", "")
    model_path = f"{models_url_prefix}/{device_id}/{model_file}" if model_file else ""
    model_type = fp.get("model_type", "")

    thumb_file = fp.get("thumbnail_file", "")
    thumbnail_url = f"{models_url_prefix}/{device_id}/{thumb_file}" if thumb_file else ""

    return Device(
        id=device_id,
        name=name or device_id.replace("_", " ").title(),
        bbox=bbox,
        device_type="articulation" if "robot" in device_id or "arm" in device_id or "flex" in device_id else "static",
        height=fp.get("height", 0.4),
        origin_offset=tuple(fp.get("origin_offset", [0.0, 0.0])),
        openings=openings,
        source=fp.get("source", "manual"),
        model_path=model_path,
        model_type=model_type,
        thumbnail_url=thumbnail_url,
    )


# ---------- 从 data.json 加载 ----------


def load_devices_from_assets(
    data_json_path: str | Path,
    footprints: dict[str, dict] | None = None,
    models_url_prefix: str = "/models",
) -> list[Device]:
    """从 uni-lab-assets 的 data.json 加载设备列表。

    如果设备在 footprints 中有条目，使用真实尺寸；否则使用默认值。
    """
    path = Path(data_json_path)
    if not path.exists():
        logger.warning("data.json not found at %s, returning empty list", path)
        return []

    if footprints is None:
        footprints = load_footprints()

    with open(path) as f:
        data = json.load(f)

    devices: list[Device] = []
    _flatten_tree(data, devices, footprints, models_url_prefix)
    return devices


def _flatten_tree(
    nodes: list[dict],
    result: list[Device],
    footprints: dict[str, dict],
    models_url_prefix: str,
) -> None:
    """递归遍历树形结构，提取叶节点为 Device。"""
    for node in nodes:
        if "children" in node:
            _flatten_tree(node["children"], result, footprints, models_url_prefix)
        elif "id" in node:
            device_id = node["id"]
            name = node.get("label", device_id)

            if device_id in footprints:
                dev = _footprint_to_device(
                    device_id, footprints[device_id], name, models_url_prefix
                )
            else:
                bbox = KNOWN_SIZES.get(device_id, DEFAULT_BBOX)
                dev = Device(id=device_id, name=name, bbox=bbox, source="assets")

            result.append(dev)


# ---------- 从 registry 加载 ----------


def load_devices_from_registry(
    device_mesh_dir: str | Path,
    footprints: dict[str, dict] | None = None,
    models_url_prefix: str = "/models",
) -> list[Device]:
    """从 Uni-Lab-OS device_mesh/devices/ 加载 registry 设备。"""
    d = Path(device_mesh_dir)
    if not d.exists():
        logger.warning("Registry dir not found at %s", d)
        return []

    if footprints is None:
        footprints = load_footprints()

    devices: list[Device] = []
    for entry in sorted(d.iterdir()):
        if not entry.is_dir():
            continue
        device_id = entry.name
        if device_id in footprints:
            dev = _footprint_to_device(
                device_id, footprints[device_id], models_url_prefix=models_url_prefix
            )
            dev.source = "registry"
        else:
            bbox = KNOWN_SIZES.get(device_id, DEFAULT_BBOX)
            dev = Device(id=device_id, name=device_id.replace("_", " ").title(), bbox=bbox, source="registry")
        devices.append(dev)

    return devices


# ---------- 合并与去重 ----------


def merge_device_lists(
    registry_devices: list[Device],
    asset_devices: list[Device],
) -> list[Device]:
    """合并双源设备列表，registry 优先。

    对于同时存在于两个源的设备，使用 registry 条目的元数据，
    但优先使用 assets 的 3D 模型路径和缩略图。
    """
    merged: dict[str, Device] = {}

    for dev in asset_devices:
        merged[dev.id] = dev

    for dev in registry_devices:
        if dev.id in merged:
            # registry 元数据优先，但保留 assets 的模型/缩略图
            asset_dev = merged[dev.id]
            dev.model_path = dev.model_path or asset_dev.model_path
            dev.model_type = dev.model_type or asset_dev.model_type
            dev.thumbnail_url = dev.thumbnail_url or asset_dev.thumbnail_url
            if dev.bbox == DEFAULT_BBOX and asset_dev.bbox != DEFAULT_BBOX:
                dev.bbox = asset_dev.bbox
                dev.height = asset_dev.height
                dev.origin_offset = asset_dev.origin_offset
                dev.openings = asset_dev.openings
            dev.source = "registry"
        merged[dev.id] = dev

    return list(merged.values())


# ---------- 统一解析器 ----------


def resolve_device(
    device_id: str,
    footprints: dict[str, dict] | None = None,
    models_url_prefix: str = "/models",
) -> Device | None:
    """按 ID 查找单个设备。先查 footprints，再查 KNOWN_SIZES。"""
    if footprints is None:
        footprints = load_footprints()

    if device_id in footprints:
        return _footprint_to_device(
            device_id, footprints[device_id], models_url_prefix=models_url_prefix
        )

    if device_id in KNOWN_SIZES:
        bbox = KNOWN_SIZES[device_id]
        return Device(id=device_id, name=device_id.replace("_", " ").title(), bbox=bbox, source="manual")

    return None


# ---------- 向后兼容 ----------


def create_devices_from_list(
    device_specs: list[dict],
) -> list[Device]:
    """从 API 请求中的设备列表创建 Device 对象（向后兼容）。

    Args:
        device_specs: [{"id": str, "name": str, "size": [w, d], "uuid": str}, ...]
            size 可选，缺失时使用 footprints 或默认值。
    """
    footprints = load_footprints()
    devices = []
    for spec in device_specs:
        catalog_id = spec["id"]
        # Use uuid as internal ID when available to ensure uniqueness
        # (multiple instances of the same catalog device get different IDs)
        dev_id = spec.get("uuid") or catalog_id
        size = spec.get("size")
        if size:
            bbox = (float(size[0]), float(size[1]))
        elif catalog_id in footprints:
            bbox = tuple(footprints[catalog_id].get("bbox", DEFAULT_BBOX))
        else:
            bbox = KNOWN_SIZES.get(catalog_id, DEFAULT_BBOX)

        openings = []
        if catalog_id in footprints:
            openings = [
                Opening(direction=tuple(o["direction"]), label=o.get("label", ""))
                for o in footprints[catalog_id].get("openings", [])
            ]

        devices.append(
            Device(
                id=dev_id,
                name=spec.get("name", catalog_id),
                bbox=bbox,
                device_type=spec.get("device_type", "static"),
                openings=openings,
            )
        )
    return devices
