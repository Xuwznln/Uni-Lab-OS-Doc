"""FastAPI 开发服务器。

开发阶段独立运行于 localhost:8000，前端通过 CORS 调用。
集成阶段合并到 Uni-Lab-OS 的 FastAPI 服务中。

运行方式：
    uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000 --reload
    # 或（推荐，支持 --config / --ak --sk --addr）
    python -m unilabos.layout_optimizer.run_server --config layout_optimizer.config.json

调试模式（启用 DEBUG 日志，含优化器逐代 cost 明细）：
    LAYOUT_DEBUG=1 uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000 --reload

日志文件：
    自动写入 layout_optimizer/logs/{YYYYMMDD_HHMMSS}.log（始终 DEBUG 级别）。
    前端 1s 轮询的 GET /scene/placements 200 行不写入日志文件。

前端访问：
    http://localhost:8000/
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
import itertools
import json
import logging
import logging.handlers
import math
import os
import socket
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .constraints import DEFAULT_WEIGHT_ANGLE  # noqa: F401 — kept for external use
from .device_catalog import (
    create_devices_from_list,
    load_devices_from_assets,
    load_devices_from_registry,
    load_footprints,
    merge_device_lists,
)
from .lab_parser import parse_lab
from .intent_interpreter import InterpretResult, interpret_intents
from .feasibility import compute_breakdown, conflicts_to_dicts, precheck_conflicts
from .models import Constraint, Intent
from .optimizer import optimize
from .parallel_optimize import run_multistart
from . import rail_layout

_console_level = logging.DEBUG if os.getenv("LAYOUT_DEBUG") else logging.INFO
# root logger must be DEBUG so the file handler receives all records;
# console output level is controlled separately via its handler.
logging.basicConfig(level=logging.DEBUG)
# basicConfig creates a default StreamHandler — set its level to the console level
for _h in logging.getLogger().handlers:
    if isinstance(_h, logging.StreamHandler):
        _h.setLevel(_console_level)
logger = logging.getLogger(__name__)

# --- 文件日志：实时写入 logs/ 目录，按启动时间命名 ---
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_log_file = _LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.log"


class _PollingFilter(logging.Filter):
    """过滤掉前端 1s 轮询产生的 GET /scene/placements 日志行。"""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "GET /scene/placements" in msg and "200" in msg:
            return False
        return True


_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
)
_file_handler.addFilter(_PollingFilter())
logging.getLogger().addHandler(_file_handler)

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
_DEVICE_PARAM_KEYS = {"device_a", "device_b", "arm_id", "target_device_id", "device"}


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


def _catalog_id_from_internal(device_id: str) -> str:
    """内部实例 ID → catalog ID。"""
    return device_id.split("#", 1)[0]


def _expand_constraints_for_duplicates(
    constraints: list[Constraint], devices: list,
) -> list[Constraint]:
    """将引用 bare catalog ID 的约束扩展到所有重复实例。"""
    catalog_instances: dict[str, list[str]] = defaultdict(list)
    for dev in devices:
        catalog_instances[_catalog_id_from_internal(dev.id)].append(dev.id)

    expanded_constraints: list[Constraint] = []
    for constraint in constraints:
        fan_out_keys: list[str] = []
        fan_out_values: list[list[str]] = []

        for key in _DEVICE_PARAM_KEYS:
            if key not in constraint.params:
                continue
            ref_id = constraint.params[key]
            if "#" in ref_id:
                continue
            instances = catalog_instances.get(ref_id, [])
            if len(instances) > 1:
                fan_out_keys.append(key)
                fan_out_values.append(instances)
                logger.info(
                    "Fan-out: %s %s=%s -> %d instances",
                    constraint.rule_name, key, ref_id, len(instances),
                )

        if not fan_out_keys:
            expanded_constraints.append(constraint)
            continue

        for combo in itertools.product(*fan_out_values):
            new_params = dict(constraint.params)
            for key, internal_id in zip(fan_out_keys, combo):
                new_params[key] = internal_id
            expanded_constraints.append(
                Constraint(
                    type=constraint.type,
                    rule_name=constraint.rule_name,
                    params=new_params,
                    weight=constraint.weight,
                )
            )

    return expanded_constraints


def _maybe_add_prefer_aligned_constraint(
    constraints: list[Constraint], align_weight: float,
) -> list[Constraint]:
    """仅在用户未显式提供 prefer_aligned 时注入对齐约束。"""
    if align_weight <= 0:
        return constraints

    if any(c.rule_name == "prefer_aligned" for c in constraints):
        logger.info("Skipping auto-injected prefer_aligned because one already exists")
        return constraints

    constraints.append(
        Constraint(
            type="soft",
            rule_name="prefer_aligned",
            weight=align_weight,
        )
    )
    return constraints


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
            "keep_adjacent": {
                "description": "Devices should stay adjacent, similar to close_together",
                "params": {
                    "devices": {"type": "list[string]", "required": True, "description": "Device IDs (min 2)"},
                    "priority": {"type": "string", "required": False, "default": "medium", "enum": ["low", "medium", "high"]},
                },
                "generates": "soft minimize_distance for each pair",
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
    # 允许字段名或 JSON 别名（camelCase / type）混用
    model_config = ConfigDict(populate_by_name=True)

    # type(=id，catalog/footprint id) + count 是 /optimize/scene 唯一需要的输入；
    # 其余字段全部可选，缺省时由 OS(footprints bbox + registry model)自动补全、uuid 自动生成。
    id: str = Field(..., alias="type")
    count: int = 1
    name: str = ""
    size: list[float] | None = None
    device_type: str = "static"
    uuid: str = ""
    # ---- edge Material 可选覆盖（不传则自动补全） ----
    model: dict = {}
    config: dict = {}
    data: dict = {}
    parent_uuid: str = Field("", alias="parentUuid")
    parent: str = ""
    parent_link: str = Field("", alias="parentLink")
    mount_point: str = Field("", alias="mountPoint")
    # 允许直接传 pose.extra（优先级高于 parent_link/mount_point）
    extra: dict = {}


class ConstraintSpec(BaseModel):
    type: str  # "hard" or "soft"
    rule_name: str
    params: dict = {}
    weight: float = 1.0


class LabSpec(BaseModel):
    width: float
    depth: float
    obstacles: list[dict] = []
    # 墙体障碍 OBB：[{cx, cy, length, thickness, yaw}, ...]（局部帧，米/弧度）
    wall_obstacles: list[dict] = []


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
    snap_cardinal: bool = False
    angle_granularity: int | None = None
    arm_reach: dict[str, float] = {}
    # DE 超参数
    strategy: str = "currenttobest1bin"
    angle_mode: str = "joint"
    mutation: list[float] = [0.5, 1.0]
    theta_mutation: list[float] | None = None
    recombination: float = 0.7
    crossover_mode: str = "device"


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
    # 失败诊断（铺垫 b）：逐条约束惩罚明细、违反的硬约束、跑 DE 前的确定性冲突
    breakdown: list[dict] = []
    violations: list[dict] = []
    conflicts: list[dict] = []


@app.post("/optimize", response_model=OptimizeResponse)
async def run_optimize(request: OptimizeRequest):
    """接收设备列表+约束，返回最优布局方案。"""
    from fastapi import HTTPException

    from .constraints import evaluate_default_hard_constraints, evaluate_constraints
    from .mock_checkers import MockCollisionChecker, MockReachabilityChecker
    from .optimizer import optimize, snap_theta, snap_theta_safe
    from .seeders import resolve_seeder_params, seed_layout

    logger.info(
        "Optimize request: %d devices, lab %.1f×%.1f, %d constraints, seeder=%s, run_de=%s, angle_granularity=%s",
        len(request.devices),
        request.lab.width,
        request.lab.depth,
        len(request.constraints),
        request.seeder,
        request.run_de,
        request.angle_granularity,
    )

    if request.angle_granularity not in (None, 4, 8, 12, 24):
        raise HTTPException(
            status_code=400,
            detail="angle_granularity must be one of: 4, 8, 12, 24",
        )

    # 转换输入
    devices = create_devices_from_list(
        [d.model_dump() for d in request.devices]
    )
    id_to_catalog = {dev.id: _catalog_id_from_internal(dev.id) for dev in devices}
    id_to_uuid = {dev.id: (dev.uuid or dev.id) for dev in devices}
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
    constraints = _expand_constraints_for_duplicates(constraints, devices)

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

    # 3. Auto-inject alignment soft constraint (opt-in via seeder_overrides)
    if request.run_de and seed_placements:
        # prefer_aligned: penalize non-cardinal angles（默认关闭，用户可通过 align_cardinal intent 或 seeder_overrides 开启）
        constraints = _maybe_add_prefer_aligned_constraint(
            constraints,
            request.seeder_overrides.get("align_weight", 0),
        )

    # 4. Validate DE hyperparameters
    if request.strategy not in {"currenttobest1bin", "best1bin", "rand1bin"}:
        raise HTTPException(
            status_code=400,
            detail=f"strategy must be one of: currenttobest1bin, best1bin, rand1bin (got {request.strategy!r})",
        )
    if request.angle_mode not in {"joint", "hybrid"}:
        raise HTTPException(
            status_code=400,
            detail=f"angle_mode must be one of: joint, hybrid (got {request.angle_mode!r})",
        )
    if request.crossover_mode not in {"device", "dimension"}:
        raise HTTPException(
            status_code=400,
            detail=f"crossover_mode must be one of: device, dimension (got {request.crossover_mode!r})",
        )
    if len(request.mutation) != 2 or request.mutation[0] > request.mutation[1]:
        raise HTTPException(status_code=400, detail="mutation must be [F_min, F_max] with F_min <= F_max")
    if request.mutation[0] < 0 or request.mutation[1] > 2.0:
        raise HTTPException(status_code=400, detail="mutation values must be in [0, 2.0]")
    if request.theta_mutation is not None:
        if len(request.theta_mutation) != 2 or request.theta_mutation[0] > request.theta_mutation[1]:
            raise HTTPException(status_code=400, detail="theta_mutation must be [F_min, F_max] with F_min <= F_max")
        if request.theta_mutation[0] < 0 or request.theta_mutation[1] > 2.0:
            raise HTTPException(status_code=400, detail="theta_mutation values must be in [0, 2.0]")
    if not (0 <= request.recombination <= 1.0):
        raise HTTPException(status_code=400, detail="recombination must be in [0, 1.0]")

    # 5. Conditional Differential Evolution
    de_ran = False
    checker = MockCollisionChecker()
    reachability_checker = MockReachabilityChecker(request.arm_reach or None)
    if request.run_de:
        result_placements = optimize(
            devices=devices,
            lab=lab,
            constraints=constraints,
            collision_checker=checker,
            reachability_checker=reachability_checker,
            seed_placements=seed_placements,
            maxiter=request.maxiter,
            seed=request.seed,
            strategy=request.strategy,
            workflow_edges=request.workflow_edges or None,
            angle_granularity=request.angle_granularity,
            angle_mode=request.angle_mode,
            mutation=tuple(request.mutation),
            theta_mutation=tuple(request.theta_mutation) if request.theta_mutation else None,
            recombination=request.recombination,
            crossover_mode=request.crossover_mode,
        )
        de_ran = True
    else:
        result_placements = seed_placements

    # 5. θ snap post-processing（opt-in，默认关闭）
    if request.snap_cardinal and request.angle_granularity is None:
        result_placements = snap_theta_safe(result_placements, devices, lab, checker)
    elif request.snap_cardinal and request.angle_granularity is not None:
        logger.info(
            "snap_cardinal ignored because angle_granularity=%s already constrains theta",
            request.angle_granularity,
        )

    # 6. Evaluate final cost (binary mode for pass/fail reporting)
    final_cost = evaluate_default_hard_constraints(
        devices, result_placements, lab, checker, graduated=False,
    )
    # 也检查用户硬约束（binary 模式）
    if constraints and not math.isinf(final_cost):
        user_hard_cost = evaluate_constraints(
            devices, result_placements, lab, constraints, checker, reachability_checker,
            graduated=False,
        )
        if math.isinf(user_hard_cost):
            final_cost = math.inf

    # 7. 失败诊断（铺垫 b）：明细 + 违反的硬约束 + 确定性冲突预检
    breakdown, violations = compute_breakdown(
        devices, result_placements, lab, constraints, checker, reachability_checker,
    )
    conflicts = conflicts_to_dicts(
        precheck_conflicts(devices, lab, constraints, request.arm_reach or None)
    )

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
        breakdown=breakdown,
        violations=violations,
        conflicts=conflicts,
    )


# ---------- 优化 -> 读 building 作分布区域 -> edge Material graph -> 上传 /edge/material ----------


class OptimizeSceneRequest(OptimizeRequest):
    """读 building 作分布区域 + 设备 type/count 自动排布 + 上传云端，一次调用走完。

    devices 仅需 type + count，其余字段自动补全（OS：footprints bbox + registry model）；优化器只填 pose。
    building（scene_path 优先，否则 scene）作为分布区域：取墙体/slab 包围盒为矩形区域，
    墙体转 OBB 障碍物，设备避开墙。building 仅作输入，不并入输出。
    输出 edge Material graph 在响应返回；默认先落盘本地再上传（可通过 saveLocal 关闭本地保存）。
    """
    model_config = ConfigDict(populate_by_name=True)

    # building 能解析出区域时用 building；否则回退请求体 lab(width/depth)
    lab: LabSpec | None = None
    scene_path: str = ""
    scene: dict = {}
    mount_uuid: str = ""
    first_add: bool = True
    # 本地保存：默认开启；output_path 为空时自动推导
    save_local: bool = Field(True, alias="saveLocal")
    output_path: str = Field("", alias="outputPath")


class OptimizeSceneResponse(BaseModel):
    # 仅设备的 edge Material graph {nodes:[Material...], edges:[]}（不含 building）
    graph: dict
    cloud_uuid_mapping: dict = {}
    # cost 不可行时为 inf；JSON 不支持 inf，故置 None（以 success 判定可行性）
    cost: float | None = None
    success: bool
    saved_local: bool = False
    local_graph_path: str = ""
    uploaded: bool = False


def _check_remote_connectivity(remote_addr: str, timeout_s: float = 3.0) -> None:
    """上传前快速检查云端网络连通性（TCP 层）。"""
    parsed = urlparse(remote_addr)
    if not parsed.hostname:
        raise ValueError(f"remote_addr 无法解析主机: {remote_addr}")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    with socket.create_connection((parsed.hostname, port), timeout=timeout_s):
        return


def _resolve_graph_output_path(output_path: str, scene_path: str) -> Path:
    """决定 graph 本地保存路径。"""
    if output_path:
        return Path(output_path).expanduser().resolve()
    if scene_path:
        p = Path(scene_path)
        return p.with_name(f"{p.stem}_layout_graph.json")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (Path.cwd() / f"layout_graph_{ts}.json").resolve()


def _save_graph_to_local(graph: dict, output_path: str, scene_path: str) -> str:
    """先落盘本地 graph，再做上传。"""
    target = _resolve_graph_output_path(output_path, scene_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    return str(target)


@app.post("/optimize/scene", response_model=OptimizeSceneResponse)
async def run_optimize_scene(request: OptimizeSceneRequest):
    """一次走完：读 building 作分布区域(避墙) -> 优化 -> 合成 edge Material graph -> 上传 /edge/material。

    设备只需 type+count，其余从 OS 自动补；输出 graph 在响应返回，并可按请求自动本地保存。
    上传复用 edge 的 HTTPClient + 同一个 /edge/material 端点；mount_uuid 为空时按云端默认根挂载。
    """
    import uuid as _uuid

    from fastapi import HTTPException

    from .building_region import load_scene_file, parse_building_region
    from .device_catalog import resolve_registry_model
    from .graph_export import placements_to_graph

    # 0. 上传前置检查：云端配置 / 网络连通（避免最后一步才报错）
    mount_uuid = request.mount_uuid or os.getenv("LAYOUT_MOUNT_UUID", "")
    try:
        from unilabos.app.web.client import HTTPClient
    except Exception as e:  # pragma: no cover - 仅在缺少 unilabos 依赖时触发
        raise HTTPException(status_code=500, detail=f"云端上传依赖不可用: {e}")
    client = HTTPClient()
    if not client.remote_addr or not client.auth:
        raise HTTPException(
            status_code=400,
            detail="缺少云端配置：需要 HTTPConfig.remote_addr 与 ak/sk（BasicConfig.auth_secret）",
        )
    # 测试场景可通过 LAYOUT_SKIP_CLOUD_PREFLIGHT=1 跳过前置连通检查
    if os.getenv("LAYOUT_SKIP_CLOUD_PREFLIGHT", "").lower() not in {"1", "true", "yes"}:
        try:
            _check_remote_connectivity(client.remote_addr, timeout_s=3.0)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"remote_addr 配置无效: {e}")
        except OSError as e:
            raise HTTPException(status_code=502, detail=f"云端网络不通: {e}")

    # 1. building 输入 -> 分布区域 + 墙体障碍
    base_scene = None
    if request.scene_path:
        try:
            base_scene = load_scene_file(request.scene_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif request.scene:
        base_scene = request.scene

    region = parse_building_region(base_scene) if base_scene else None
    origin = (0.0, 0.0)
    wall_obstacle_dicts: list[dict] = []
    if region is not None:
        origin, region_w, region_d, wall_obstacles = region
        lab_width, lab_depth = region_w, region_d
        wall_obstacle_dicts = [
            {"cx": w.cx, "cy": w.cy, "length": w.length, "thickness": w.thickness, "yaw": w.yaw}
            for w in wall_obstacles
        ]
    elif request.lab is not None:
        lab_width, lab_depth = request.lab.width, request.lab.depth
    else:
        raise HTTPException(
            status_code=400,
            detail="无法从 building 解析分布区域，且未提供 lab(width/depth)",
        )

    # 2. 展开 count + 自动补全（同 type 共享 model；uuid 逐实例唯一）
    expanded_specs: list[DeviceSpec] = []
    instance_meta: dict[str, dict] = {}
    for d in request.devices:
        reg_model = resolve_registry_model(d.id) or {}
        model = d.model or reg_model
        display_name = d.name or d.id
        raw_extra = dict(d.extra or {})
        parent_link = (
            raw_extra.get("parent_link")
            or raw_extra.get("parentLink")
            or d.parent_link
            or ""
        )
        mount_point = (
            raw_extra.get("mount_point")
            or raw_extra.get("mountPoint")
            or d.mount_point
            or ""
        )
        n = max(1, d.count)
        for _ in range(n):
            inst_uuid = d.uuid if (n == 1 and d.uuid) else str(_uuid.uuid4())
            instance_meta[inst_uuid] = {
                "id": d.id,
                "display_name": display_name,
                "model": model,
                "config": d.config or {},
                "data": d.data or {},
                "parent_uuid": d.parent_uuid or "",
                "parent": d.parent or "",
                "extra": {
                    "parent_link": str(parent_link),
                    "mount_point": str(mount_point),
                },
            }
            expanded_specs.append(
                DeviceSpec(
                    id=d.id,
                    name=d.name,
                    size=d.size,
                    device_type=d.device_type,
                    uuid=inst_uuid,
                )
            )

    # 3. 用区域(含墙障碍)的 lab + 展开设备跑优化（复用 /optimize 全流程）
    opt_lab = LabSpec(width=lab_width, depth=lab_depth, wall_obstacles=wall_obstacle_dicts)
    opt_req = request.model_copy(update={"devices": expanded_specs, "lab": opt_lab})
    opt_resp = await run_optimize(opt_req)

    # 4. placements(局部帧米) -> 归一化 placed(building 世界坐标米 = origin + 局部)
    placed: list[dict] = []
    for pr in opt_resp.placements:
        meta = instance_meta.get(pr.uuid, {})
        placed.append(
            {
                "id": meta.get("id", pr.device_id),
                "uuid": pr.uuid,
                "display_name": meta.get("display_name") or pr.device_id,
                "model": meta.get("model", {}),
                "config": meta.get("config", {}),
                "data": meta.get("data", {}),
                "parent_uuid": meta.get("parent_uuid", ""),
                "parent": meta.get("parent", ""),
                "extra": meta.get("extra", {}),
                "x": origin[0] + pr.position.x,
                "y": origin[1] + pr.position.y,
                "z": pr.position.z,
                "theta": pr.rotation.z,
            }
        )

    # 5. 合成 edge Material graph（响应返回 + 上传同一份；不含 building）
    graph = placements_to_graph(placed)

    # 6. 先落本地（可选），再上传云端（最后一步）
    local_graph_path = ""
    saved_local = False
    if request.save_local:
        try:
            local_graph_path = _save_graph_to_local(graph, request.output_path, request.scene_path)
            saved_local = True
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"本地保存 graph 失败: {e}")

    # 7. 上传：复用 edge HTTPClient -> POST /edge/material（nodes 即上面的 Material 节点）
    try:
        cloud_uuid_mapping = client.material_add(
            graph["nodes"], mount_uuid=mount_uuid, first_add=request.first_add,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"上传云端失败: {e}")

    logger.info(
        "optimize/scene 完成：%d 设备节点（区域 %.2f×%.2f，墙障碍 %d），已上传至 %s/edge/material",
        len(placed),
        lab_width,
        lab_depth,
        len(wall_obstacle_dicts),
        client.remote_addr,
    )

    return OptimizeSceneResponse(
        graph=graph,
        cloud_uuid_mapping=cloud_uuid_mapping,
        cost=opt_resp.cost if math.isfinite(opt_resp.cost) else None,
        success=opt_resp.success,
        saved_local=saved_local,
        local_graph_path=local_graph_path,
        uploaded=True,
    )


# ---------- 自动布局 API（失败自愈：解析预检 + 并行多起点 DE） ----------


class AutoOptimizeRequest(OptimizeRequest):
    """带失败自愈的自动优化请求。

    在 OptimizeRequest 基础上增加多起点网格参数。网格按"多 seed × 少量 seeder"
    组织，maxiter 固定取较大值并依赖 early-stopping，不再作为独立网格维度。
    """

    seeds: list[int] = [42, 7, 123, 2024]
    seeders: list[str] = ["compact_outward", "spread_inward", "workflow_cluster"]
    maxiter: int = 400
    max_workers: int | None = None


class AutoOptimizeResponse(BaseModel):
    placements: list[PlacementResult]
    cost: float
    success: bool
    seeder_used: str = ""
    de_ran: bool = True
    # 多起点统计
    tried: int = 0
    total: int = 0
    # 失败诊断：跑 DE 前的确定性冲突（情况 A）、跨 run 聚合的持续违反、可选明细
    conflicts: list[dict] = []
    violations: list[dict] = []
    breakdown: list[dict] = []


@app.post("/optimize/auto", response_model=AutoOptimizeResponse)
async def run_optimize_auto(request: AutoOptimizeRequest):
    """带失败自愈的自动布局优化。

    流程：
    1. 解析冲突预检 —— 命中确定性硬冲突（情况 A）立即短路返回，附 conflicts + 放宽建议。
    2. 并行多起点 DE（seeds × seeders）—— 任一起点成功立即终止其余并返回（情况 B 自愈）。
    3. 全部起点失败 —— 返回跨 run 聚合的 persistent 违反约束，供针对性放宽。
    """
    from fastapi import HTTPException

    from .mock_checkers import MockCollisionChecker, MockReachabilityChecker
    from .seeders import resolve_seeder_params, seed_layout

    logger.info(
        "Auto-optimize request: %d devices, lab %.1f×%.1f, %d constraints, seeds=%d, seeders=%s, maxiter=%d",
        len(request.devices), request.lab.width, request.lab.depth,
        len(request.constraints), len(request.seeds), request.seeders, request.maxiter,
    )

    # --- 校验 DE 超参 + 网格参数 ---
    if request.angle_granularity not in (None, 4, 8, 12, 24):
        raise HTTPException(status_code=400, detail="angle_granularity must be one of: 4, 8, 12, 24")
    if request.strategy not in {"currenttobest1bin", "best1bin", "rand1bin"}:
        raise HTTPException(status_code=400, detail=f"strategy must be one of: currenttobest1bin, best1bin, rand1bin (got {request.strategy!r})")
    if request.angle_mode not in {"joint", "hybrid"}:
        raise HTTPException(status_code=400, detail=f"angle_mode must be one of: joint, hybrid (got {request.angle_mode!r})")
    if request.crossover_mode not in {"device", "dimension"}:
        raise HTTPException(status_code=400, detail=f"crossover_mode must be one of: device, dimension (got {request.crossover_mode!r})")
    if len(request.mutation) != 2 or request.mutation[0] > request.mutation[1]:
        raise HTTPException(status_code=400, detail="mutation must be [F_min, F_max] with F_min <= F_max")
    if not (0 <= request.recombination <= 1.0):
        raise HTTPException(status_code=400, detail="recombination must be in [0, 1.0]")
    if not request.seeds:
        raise HTTPException(status_code=400, detail="seeds must not be empty")
    if not request.seeders:
        raise HTTPException(status_code=400, detail="seeders must not be empty")
    for sd in request.seeders:
        try:
            resolve_seeder_params(sd)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # --- 构建设备 / 实验室 / 约束 ---
    devices = create_devices_from_list([d.model_dump() for d in request.devices])
    id_to_catalog = {dev.id: _catalog_id_from_internal(dev.id) for dev in devices}
    id_to_uuid = {dev.id: (dev.uuid or dev.id) for dev in devices}
    lab = parse_lab(request.lab.model_dump())
    constraints = [
        Constraint(type=c.type, rule_name=c.rule_name, params=c.params, weight=c.weight)
        for c in request.constraints
    ]
    constraints = _expand_constraints_for_duplicates(constraints, devices)
    constraints = _maybe_add_prefer_aligned_constraint(
        constraints, request.seeder_overrides.get("align_weight", 0),
    )

    checker = MockCollisionChecker()
    reach = MockReachabilityChecker(request.arm_reach or None)

    def _map_placement(p: dict) -> PlacementResult:
        return PlacementResult(
            device_id=id_to_catalog.get(p["device_id"], p["device_id"]),
            uuid=id_to_uuid.get(p["device_id"], p["device_id"]),
            position=PositionXYZ(x=round(p["x"], 4), y=round(p["y"], 4), z=0.0),
            rotation=PositionXYZ(x=0.0, y=0.0, z=round(p["theta"], 4)),
        )

    # --- 1. 解析冲突预检（情况 A 短路）---
    conflicts = precheck_conflicts(devices, lab, constraints, request.arm_reach or None)
    if conflicts:
        logger.info("Auto-optimize precheck found %d deterministic conflict(s), short-circuiting", len(conflicts))
        params = resolve_seeder_params(request.seeders[0])
        placeholder = seed_layout(devices, lab, params, request.workflow_edges or None)
        breakdown, violations = compute_breakdown(
            devices, placeholder, lab, constraints, checker, reach,
        )
        return AutoOptimizeResponse(
            placements=[
                _map_placement({"device_id": p.device_id, "x": p.x, "y": p.y, "theta": p.theta})
                for p in placeholder
            ],
            cost=math.inf,
            success=False,
            seeder_used="(precheck)",
            de_ran=False,
            tried=0,
            total=0,
            conflicts=conflicts_to_dicts(conflicts),
            violations=violations,
            breakdown=breakdown,
        )

    # --- 2 & 3. 并行多起点 DE（在线程里跑，避免阻塞事件循环）---
    outcome = await asyncio.to_thread(
        run_multistart,
        devices,
        lab,
        constraints,
        seeds=request.seeds,
        seeders=request.seeders,
        maxiter=request.maxiter,
        workflow_edges=request.workflow_edges or None,
        angle_granularity=request.angle_granularity,
        angle_mode=request.angle_mode,
        strategy=request.strategy,
        mutation=request.mutation,
        theta_mutation=request.theta_mutation,
        recombination=request.recombination,
        crossover_mode=request.crossover_mode,
        snap_cardinal=request.snap_cardinal,
        arm_reach=request.arm_reach or None,
        seeder_overrides=request.seeder_overrides or None,
        max_workers=request.max_workers,
    )

    winner = outcome["winner"]
    if winner is None:
        return AutoOptimizeResponse(
            placements=[], cost=math.inf, success=False, seeder_used="",
            de_ran=True, tried=outcome["tried"], total=outcome["total"],
            conflicts=[], violations=outcome["violations"],
        )

    logger.info(
        "Auto-optimize done: success=%s, tried=%d/%d, winner_seeder=%s, cost=%s",
        outcome["success"], outcome["tried"], outcome["total"],
        winner["seeder"], winner["cost"],
    )
    return AutoOptimizeResponse(
        placements=[_map_placement(p) for p in winner["placements"]],
        cost=winner["cost"],
        success=outcome["success"],
        seeder_used=winner["seeder"],
        de_ran=True,
        tried=outcome["tried"],
        total=outcome["total"],
        conflicts=[],
        violations=outcome["violations"],
    )


# ---------- 导轨布局 API（确定性解析布局） ----------
#
# 与 /optimize 系列（差分进化随机寻优）不同，本组端点走确定性解析布局：
# 距离参数定死后坐标唯一，不跑 DE。当前为阶段0（M0）骨架，核心算法在
# rail_layout.py 的 M1~M3 填充，未实现的端点返回 501。


class RailParamsSpec(BaseModel):
    """导轨布局距离参数覆盖（缺省项回落 rail_layout.DEFAULT_PARAMS）。"""

    a: float | None = None
    b: float | None = None
    c: float | None = None
    d: float | None = None
    e: float | None = None
    working_radius: float | None = None


class RailFeasibilityRequest(BaseModel):
    devices: list[DeviceSpec]
    ordered_instruments: list[str]
    lab: LabSpec
    arm_model: dict | None = None
    params: RailParamsSpec | None = None
    stack_model: str | dict | None = rail_layout.DEFAULT_STACK_MODEL


class RailFeasibilityResponse(BaseModel):
    feasible: bool
    n_arm: int
    n_stack: int
    n_max: int
    l_max: float = 0.0
    mode_hint: str
    reasons: list[str] = []
    suggestions: list[str] = []


class RailLayoutRequest(BaseModel):
    devices: list[DeviceSpec]
    ordered_instruments: list[str]
    lab: LabSpec
    arm_model: dict | None = None
    params: RailParamsSpec | None = None
    mode: str = "near_wall"
    stack_model: str | dict | None = rail_layout.DEFAULT_STACK_MODEL


class RailLayoutResponse(BaseModel):
    placements: list[PlacementResult]
    arms: list[dict] = []
    stacks: list[dict] = []
    conflicts: list[dict] = []


class RailValidateRequest(BaseModel):
    placements: list[PlacementResult]
    lab: LabSpec
    arms: list[dict] = []
    # 用于按 device_id 反查 bbox（PlacementResult 不含 bbox）
    devices: list[DeviceSpec] = []


class RailValidateResponse(BaseModel):
    conflicts: list[dict] = []


def _rail_params(spec: RailParamsSpec | None) -> rail_layout.RailParams:
    """将请求中的参数覆盖转为 RailParams（缺省回落默认值）。"""
    overrides = spec.model_dump(exclude_none=True) if spec else None
    return rail_layout.RailParams.from_overrides(overrides)


@app.post("/rail/feasibility", response_model=RailFeasibilityResponse)
async def run_rail_feasibility(request: RailFeasibilityRequest):
    """阶段一：导轨布局可行性检查（M1 填充核心算法）。"""
    from fastapi import HTTPException

    devices = create_devices_from_list([d.model_dump() for d in request.devices])
    lab = parse_lab(request.lab.model_dump())
    params = _rail_params(request.params)
    try:
        report = rail_layout.check_feasibility(
            devices, request.ordered_instruments, lab, request.arm_model, params,
            request.stack_model,
        )
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RailFeasibilityResponse(
        feasible=report.feasible,
        n_arm=report.n_arm,
        n_stack=report.n_stack,
        n_max=report.n_max,
        l_max=report.l_max,
        mode_hint=report.mode_hint,
        reasons=report.reasons,
        suggestions=report.suggestions,
    )


@app.post("/rail/layout", response_model=RailLayoutResponse)
async def run_rail_layout(request: RailLayoutRequest):
    """阶段一~三上层编排：可行性 → 布臂/堆栈 → 布仪器 → 多退少补 → 校验。"""
    from dataclasses import asdict

    from fastapi import HTTPException

    devices = create_devices_from_list([d.model_dump() for d in request.devices])
    lab = parse_lab(request.lab.model_dump())
    params = _rail_params(request.params)
    mode = "centered" if request.mode == "centered" else "near_wall"

    try:
        result = rail_layout.layout_rail(
            devices, request.ordered_instruments, lab, request.arm_model, params,
            mode, request.stack_model,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RailLayoutResponse(
        placements=[
            PlacementResult(
                device_id=p.device_id,
                uuid=p.device_id,
                position=PositionXYZ(x=round(p.center[0], 4), y=round(p.center[1], 4), z=0.0),
                rotation=PositionXYZ(x=0.0, y=0.0, z=round(p.theta, 4)),
            )
            for p in result.placements
        ],
        arms=[asdict(a) for a in result.arms],
        stacks=[asdict(s) for s in result.stacks],
        conflicts=rail_layout.conflicts_to_dicts(result.conflicts),
    )


@app.post("/rail/validate", response_model=RailValidateResponse)
async def run_rail_validate(request: RailValidateRequest):
    """可选：仪器布局的环境碰撞 / 越界校验守卫。"""
    lab = parse_lab(request.lab.model_dump())
    devices = create_devices_from_list([d.model_dump() for d in request.devices])
    bbox_map = {d.id: (float(d.bbox[0]), float(d.bbox[1])) for d in devices}

    placements = [
        rail_layout.InstrumentPlacement(
            device_id=p.device_id,
            center=(p.position.x, p.position.y),
            theta=p.rotation.z,
            bbox=bbox_map.get(p.device_id, (0.0, 0.0)),
        )
        for p in request.placements
    ]
    conflicts = rail_layout.validate_placements(placements, lab, lab.obstacles, None)
    return RailValidateResponse(conflicts=rail_layout.conflicts_to_dicts(conflicts))


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
    """前端在加载和尺寸变更时推送；agent 也可调用以改变实验室尺寸。

    bump 一次 _scene_state["version"]，让前端在 1s 轮询中检测到 version 增长，
    随响应里的 lab 字段一并同步地板尺寸（即便本次没有新的 placements）。
    """
    _lab_state["width"] = dims.width
    _lab_state["depth"] = dims.depth
    _scene_state["version"] += 1
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
    """前端轮询此端点，检测 version 变化后应用布局。

    响应里附带当前 lab 尺寸，使前端在同一次 version 跳变中原子地同步
    地板尺寸与设备位置，避免「地板用旧尺寸、坐标按新尺寸换算」导致出界。
    """
    return {**_scene_state, "lab": _lab_state}


@app.delete("/scene/placements")
async def clear_scene_placements():
    """重置场景状态（重录时使用）。"""
    _scene_state["version"] = 0
    _scene_state["placements"] = []
    return {"version": 0, "placements": []}
