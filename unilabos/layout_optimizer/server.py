"""FastAPI 开发服务器。

开发阶段独立运行于 localhost:8000，前端通过 CORS 调用。
集成阶段合并到 Uni-Lab-OS 的 FastAPI 服务中。

运行方式：
    uvicorn layout_optimizer.server:app --host 0.0.0.0 --port 8000 --reload

前端访问：
    http://localhost:8000/
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .constraints import DEFAULT_WEIGHT_ANGLE
from .device_catalog import (
    create_devices_from_list,
    load_devices_from_assets,
    load_devices_from_registry,
    load_footprints,
    merge_device_lists,
)
from .lab_parser import parse_lab
from .intent_interpreter import InterpretResult, interpret_intents
from .models import Constraint, Intent
from .optimizer import optimize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# 可配置路径
# __file__ -> Uni-Lab-OS/unilabos/layout_optimizer/server.py
_UNILABOS_DIR = Path(__file__).resolve().parent.parent   # .../Uni-Lab-OS/unilabos/

UNI_LAB_ASSETS_DIR = Path(
    os.getenv("UNI_LAB_ASSETS_DIR", str(_UNILABOS_DIR.parent.parent.parent / "uni-lab-assets"))
)
UNI_LAB_ASSETS_MODELS_DIR = UNI_LAB_ASSETS_DIR / "device_models"
UNI_LAB_ASSETS_DATA_JSON = UNI_LAB_ASSETS_DIR / "data.json"
UNI_LAB_OS_DEVICE_MESH_DIR = Path(
    os.getenv(
        "UNI_LAB_OS_DEVICE_MESH_DIR",
        str(_UNILABOS_DIR / "device_mesh" / "devices"),
    )
)

app = FastAPI(title="Layout Optimizer", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 开发阶段允许所有来源
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 挂载 3D 模型和缩略图
if UNI_LAB_ASSETS_MODELS_DIR.exists():
    app.mount("/models", StaticFiles(directory=str(UNI_LAB_ASSETS_MODELS_DIR)), name="models")
    logger.info("Mounted /models from %s", UNI_LAB_ASSETS_MODELS_DIR)
else:
    logger.warning("uni-lab-assets models dir not found: %s", UNI_LAB_ASSETS_MODELS_DIR)


# ---------- 设备目录缓存 ----------

_device_cache: list[dict] | None = None


# 消耗品/配件关键词（不独立放置于实验台）
_CONSUMABLE_KEYWORDS = {
    "plate", "well", "tube", "tip", "reservoir", "carrier", "nest",
    "adapter", "trough", "magnet_module", "magnet_plate", "rack", "lid",
    "seal", "cap", "vial", "flask", "dish", "block", "strip", "insert",
    "gasket", "pad", "grid_segment", "spacer", "diti_tray",
}
# 但包含这些关键词的是独立设备，不是消耗品
_DEVICE_KEYWORDS = {
    "reader", "handler", "hotel", "washer", "stacker", "sealer", "labeler",
    "centrifuge", "incubator", "shaker", "robot", "arm", "flex", "dispenser",
    "printer", "scanner", "analyzer", "fluorometer", "spectrophotometer",
    "thermocycler", "module",
}


def _is_standalone_device(device_id: str, bbox: tuple[float, float]) -> bool:
    """判断设备是否独立放置于实验台（非消耗品/配件）。"""
    mx = max(bbox[0], bbox[1])
    mn = min(bbox[0], bbox[1])
    if mx >= 0.30:
        return True  # 大于 30cm 一定是独立设备
    if mx < 0.05:
        return False  # 小于 5cm 一定是消耗品
    lower = device_id.lower()
    # 非常扁平（一维 < 3cm）的几乎都是配件/载具，即使名称匹配设备关键词
    if mn < 0.03:
        return False
    # 先检查消耗品关键词（如果匹配，再看是否有设备关键词覆盖）
    is_consumable_name = any(kw in lower for kw in _CONSUMABLE_KEYWORDS)
    is_device_name = any(kw in lower for kw in _DEVICE_KEYWORDS)
    if is_consumable_name and not is_device_name:
        return False
    if is_device_name:
        return True
    # 默认：>= 15cm 视为设备
    return mx >= 0.15


def _build_device_list() -> list[dict]:
    """构建合并后的设备列表（缓存）。"""
    global _device_cache
    if _device_cache is not None:
        return _device_cache

    footprints = load_footprints()

    registry = load_devices_from_registry(UNI_LAB_OS_DEVICE_MESH_DIR, footprints)
    assets = load_devices_from_assets(UNI_LAB_ASSETS_DATA_JSON, footprints)

    merged = merge_device_lists(registry, assets)

    _device_cache = [
        {
            "id": d.id,
            "name": d.name,
            "device_type": d.device_type,
            "source": d.source,
            "bbox": list(d.bbox),
            "height": d.height,
            "origin_offset": list(d.origin_offset),
            "openings": [
                {"direction": list(o.direction), "label": o.label}
                for o in d.openings
            ],
            "model_path": d.model_path,
            "model_type": d.model_type,
            "thumbnail_url": d.thumbnail_url,
            "is_standalone": _is_standalone_device(d.id, d.bbox),
        }
        for d in merged
    ]
    standalone = sum(1 for d in _device_cache if d["is_standalone"])
    logger.info("Built device catalog: %d devices (%d standalone)", len(_device_cache), standalone)
    return _device_cache


# ---------- 路由 ----------


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/lab3d")


@app.get("/lab3d", include_in_schema=False)
async def lab3d_ui():
    return FileResponse(STATIC_DIR / "lab3d.html")


@app.get("/devices")
async def list_devices(source: str = "all"):
    """返回合并后的设备目录。?source=registry|assets|all"""
    devices = _build_device_list()
    if source != "all":
        devices = [d for d in devices if d["source"] == source]
    return devices


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------- 意图解释 API ----------


class IntentSpec(BaseModel):
    intent: str
    params: dict = {}
    description: str = ""


class TranslationEntry(BaseModel):
    source_intent: str
    source_description: str
    source_params: dict
    generated_constraints: list[dict]
    explanation: str
    confidence: str = "high"


class InterpretRequest(BaseModel):
    intents: list[IntentSpec]


class InterpretResponse(BaseModel):
    constraints: list[dict]
    translations: list[TranslationEntry]
    workflow_edges: list[list[str]]
    errors: list[str]


@app.post("/interpret", response_model=InterpretResponse)
async def run_interpret(request: InterpretRequest):
    """将语义化意图翻译为约束列表，供用户确认后传入 /optimize。"""
    logger.info("Interpret request: %d intents", len(request.intents))

    intents = [
        Intent(
            intent=i.intent,
            params=i.params,
            description=i.description,
        )
        for i in request.intents
    ]

    result: InterpretResult = interpret_intents(intents)

    return InterpretResponse(
        constraints=[
            {"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight}
            for c in result.constraints
        ],
        translations=[
            TranslationEntry(
                source_intent=t["source_intent"],
                source_description=t.get("source_description", ""),
                source_params=t.get("source_params", {}),
                generated_constraints=t["generated_constraints"],
                explanation=t["explanation"],
                confidence=t.get("confidence", "high"),
            )
            for t in result.translations
        ],
        workflow_edges=result.workflow_edges,
        errors=result.errors,
    )


@app.get("/interpret/schema")
async def interpret_schema():
    """返回可用意图类型及其参数规范，供 LLM agent 发现和使用。"""
    return {
        "description": "Layout optimizer intent schema. LLM agents should translate user requests into these intents.",
        "intents": {
            "reachable_by": {
                "description": "Robot arm must be able to reach all target devices",
                "params": {
                    "arm": {"type": "string", "required": True, "description": "Device ID of robot arm"},
                    "targets": {"type": "list[string]", "required": True, "description": "Device IDs the arm must reach"},
                },
                "generates": "hard reachability constraint per target",
            },
            "close_together": {
                "description": "Group of devices should be placed near each other",
                "params": {
                    "devices": {"type": "list[string]", "required": True, "description": "Device IDs (min 2)"},
                    "priority": {"type": "string", "required": False, "default": "medium", "enum": ["low", "medium", "high"]},
                },
                "generates": "soft minimize_distance for each pair",
            },
            "far_apart": {
                "description": "Devices should be placed far from each other",
                "params": {
                    "devices": {"type": "list[string]", "required": True, "description": "Device IDs (min 2)"},
                    "priority": {"type": "string", "required": False, "default": "medium", "enum": ["low", "medium", "high"]},
                },
                "generates": "soft maximize_distance for each pair",
            },
            "max_distance": {
                "description": "Two devices must be within a maximum distance",
                "params": {
                    "device_a": {"type": "string", "required": True},
                    "device_b": {"type": "string", "required": True},
                    "distance": {"type": "float", "required": True, "description": "Max edge-to-edge distance in meters"},
                },
                "generates": "hard distance_less_than",
            },
            "min_distance": {
                "description": "Two devices must be at least a minimum distance apart",
                "params": {
                    "device_a": {"type": "string", "required": True},
                    "device_b": {"type": "string", "required": True},
                    "distance": {"type": "float", "required": True, "description": "Min edge-to-edge distance in meters"},
                },
                "generates": "hard distance_greater_than",
            },
            "min_spacing": {
                "description": "Minimum gap between all device pairs",
                "params": {
                    "min_gap": {"type": "float", "required": False, "default": 0.3, "description": "Minimum gap in meters"},
                },
                "generates": "hard min_spacing",
            },
            "workflow_hint": {
                "description": "Workflow step order — consecutive devices should be near each other",
                "params": {
                    "workflow": {"type": "string", "required": False, "description": "Workflow name (e.g. 'pcr')"},
                    "devices": {"type": "list[string]", "required": True, "description": "Ordered device IDs following workflow steps"},
                },
                "generates": "soft minimize_distance for consecutive pairs + workflow_edges",
            },
            "face_outward": {
                "description": "Devices should face outward from lab center",
                "params": {},
                "generates": "soft prefer_orientation_mode outward",
            },
            "face_inward": {
                "description": "Devices should face inward toward lab center",
                "params": {},
                "generates": "soft prefer_orientation_mode inward",
            },
            "align_cardinal": {
                "description": "Devices should align to cardinal directions (0/90/180/270 degrees)",
                "params": {},
                "generates": "soft prefer_aligned",
            },
        },
    }


# ---------- 优化 API ----------


class DeviceSpec(BaseModel):
    id: str
    name: str = ""
    size: list[float] | None = None
    device_type: str = "static"
    uuid: str = ""


class ConstraintSpec(BaseModel):
    type: str  # "hard" or "soft"
    rule_name: str
    params: dict = {}
    weight: float = 1.0


class LabSpec(BaseModel):
    width: float
    depth: float
    obstacles: list[dict] = []


class OptimizeRequest(BaseModel):
    devices: list[DeviceSpec]
    lab: LabSpec
    constraints: list[ConstraintSpec] = []
    seeder: str = "compact_outward"
    seeder_overrides: dict = {}
    run_de: bool = True
    workflow_edges: list[list[str]] = []
    maxiter: int = 200
    seed: int | None = None


class PositionXYZ(BaseModel):
    x: float
    y: float
    z: float


class PlacementResult(BaseModel):
    device_id: str
    uuid: str
    position: PositionXYZ
    rotation: PositionXYZ


class OptimizeResponse(BaseModel):
    placements: list[PlacementResult]
    cost: float
    success: bool
    seeder_used: str = ""
    de_ran: bool = True


@app.post("/optimize", response_model=OptimizeResponse)
async def run_optimize(request: OptimizeRequest):
    """接收设备列表+约束，返回最优布局方案。"""
    from fastapi import HTTPException

    from .constraints import evaluate_default_hard_constraints, evaluate_constraints
    from .mock_checkers import MockCollisionChecker
    from .optimizer import optimize, snap_theta, snap_theta_safe
    from .seeders import resolve_seeder_params, seed_layout

    logger.info(
        "Optimize request: %d devices, lab %.1f×%.1f, %d constraints, seeder=%s, run_de=%s",
        len(request.devices),
        request.lab.width,
        request.lab.depth,
        len(request.constraints),
        request.seeder,
        request.run_de,
    )

    # Build mapping: internal uuid-based id → (catalog_id, uuid)
    # create_devices_from_list uses uuid as Device.id when available
    id_to_catalog: dict[str, str] = {}
    id_to_uuid: dict[str, str] = {}
    for d in request.devices:
        internal_id = d.uuid or d.id
        id_to_catalog[internal_id] = d.id
        id_to_uuid[internal_id] = d.uuid or d.id

    # 转换输入
    devices = create_devices_from_list(
        [d.model_dump() for d in request.devices]
    )
    lab = parse_lab(request.lab.model_dump())
    constraints = [
        Constraint(
            type=c.type,
            rule_name=c.rule_name,
            params=c.params,
            weight=c.weight,
        )
        for c in request.constraints
    ]

    # 1. Resolve seeder
    try:
        params = resolve_seeder_params(request.seeder, request.seeder_overrides or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Seed
    seed_placements = seed_layout(
        devices, lab, params,
        request.workflow_edges or None,
    )

    # 3. Auto-inject orientation soft constraints for DE
    if request.run_de and request.seeder != "row_fallback" and seed_placements:
        # Resolve orientation mode from seeder preset
        orientation_mode = params.orientation_mode if params else "none"
        if orientation_mode != "none":
            # prefer_orientation_mode: position-aware outward/inward facing penalty
            constraints.append(Constraint(
                type="soft",
                rule_name="prefer_orientation_mode",
                params={"mode": orientation_mode},
                weight=request.seeder_overrides.get("orientation_weight", DEFAULT_WEIGHT_ANGLE),
            ))
        # prefer_aligned: penalize non-cardinal angles
        align_weight = request.seeder_overrides.get("align_weight", DEFAULT_WEIGHT_ANGLE)
        if align_weight > 0:
            constraints.append(Constraint(
                type="soft",
                rule_name="prefer_aligned",
                weight=align_weight,
            ))

    # 4. Conditional Differential Evolution
    de_ran = False
    checker = MockCollisionChecker()
    if request.run_de:
        result_placements = optimize(
            devices=devices,
            lab=lab,
            constraints=constraints,
            collision_checker=checker,
            seed_placements=seed_placements,
            maxiter=request.maxiter,
            seed=request.seed,
            workflow_edges=request.workflow_edges or None,
        )
        de_ran = True
    else:
        result_placements = seed_placements

    # 5. θ snap post-processing（碰撞安全：snap 后验证，失败则回退）
    result_placements = snap_theta_safe(result_placements, devices, lab, checker)

    # 6. Evaluate final cost (binary mode for pass/fail reporting)
    final_cost = evaluate_default_hard_constraints(
        devices, result_placements, lab, checker, graduated=False,
    )
    # 也检查用户硬约束（binary 模式）
    if constraints and not math.isinf(final_cost):
        user_hard_cost = evaluate_constraints(
            devices, result_placements, lab, constraints, checker,
            graduated=False,
        )
        if math.isinf(user_hard_cost):
            final_cost = math.inf

    return OptimizeResponse(
        placements=[
            PlacementResult(
                device_id=id_to_catalog.get(p.device_id, p.device_id),
                uuid=id_to_uuid.get(p.device_id, p.device_id),
                position=PositionXYZ(x=round(p.x, 4), y=round(p.y, 4), z=0.0),
                rotation=PositionXYZ(x=0.0, y=0.0, z=round(p.theta, 4)),
            )
            for p in result_placements
        ],
        cost=final_cost,
        success=not math.isinf(final_cost),
        seeder_used=request.seeder,
        de_ran=de_ran,
    )


# ---------- 场景状态 API（演示用） ----------


_scene_state: dict = {"version": 0, "placements": []}
_lab_state: dict = {"width": 4.0, "depth": 4.0}


class LabDimensions(BaseModel):
    width: float
    depth: float


@app.get("/scene/lab")
async def get_lab_dimensions():
    """返回当前实验室尺寸（前端推送，agent 读取）。"""
    return _lab_state


@app.post("/scene/lab")
async def set_lab_dimensions(dims: LabDimensions):
    """前端在加载和尺寸变更时推送。"""
    _lab_state["width"] = dims.width
    _lab_state["depth"] = dims.depth
    return _lab_state


class ScenePlacementsRequest(BaseModel):
    placements: list[PlacementResult]


@app.post("/scene/placements")
async def set_scene_placements(request: ScenePlacementsRequest):
    """Agent 写入布局结果，前端轮询读取。"""
    _scene_state["version"] += 1
    _scene_state["placements"] = [p.model_dump() for p in request.placements]
    logger.info(
        "Scene placements updated: version=%d, count=%d",
        _scene_state["version"],
        len(request.placements),
    )
    return {"version": _scene_state["version"], "count": len(request.placements)}


@app.get("/scene/placements")
async def get_scene_placements():
    """前端轮询此端点，检测 version 变化后应用布局。"""
    return _scene_state


@app.delete("/scene/placements")
async def clear_scene_placements():
    """重置场景状态（重录时使用）。"""
    _scene_state["version"] = 0
    _scene_state["placements"] = []
    return {"version": 0, "placements": []}
