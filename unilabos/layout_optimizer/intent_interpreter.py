"""意图解释器：将语义化意图翻译为 Constraint 列表。"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

from .models import Constraint, Intent

# 优先级权重映射
_PRIORITY_WEIGHTS: dict[str, float] = {"low": 1.0, "medium": 3.0, "high": 8.0}
_DEFAULT_WEIGHT = _PRIORITY_WEIGHTS["medium"]


@dataclass
class InterpretResult:
    """意图解释结果。"""

    constraints: list[Constraint] = field(default_factory=list)
    translations: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    workflow_edges: list[list[str]] = field(default_factory=list)


def _handle_reachable_by(intent: Intent, result: InterpretResult) -> None:
    """reachable_by：机械臂必须能到达指定设备列表。"""
    arm = intent.params.get("arm")
    targets = intent.params.get("targets", [])

    if arm is None:
        result.errors.append(f"reachable_by: 缺少必要参数 'arm'")
        return
    if not targets:
        result.errors.append(f"reachable_by: 参数 'targets' 不能为空")
        return

    generated: list[dict] = []
    for target in targets:
        c = Constraint(
            type="hard",
            rule_name="reachability",
            params={"arm_id": arm, "target_device_id": target},
        )
        result.constraints.append(c)
        generated.append({"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight})

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": generated,
        "explanation": f"机械臂 '{arm}' 需要能够到达 {len(targets)} 个目标设备",
    })


def _handle_close_together(intent: Intent, result: InterpretResult) -> None:
    """close_together：设备组内两两最小化距离。"""
    devices: list[str] = intent.params.get("devices", [])
    priority: str = intent.params.get("priority", "medium")

    if len(devices) < 2:
        result.errors.append(f"close_together: 参数 'devices' 至少需要 2 个设备，当前 {len(devices)} 个")
        return

    weight = _PRIORITY_WEIGHTS.get(priority, _DEFAULT_WEIGHT)
    generated: list[dict] = []
    for dev_a, dev_b in itertools.combinations(devices, 2):
        c = Constraint(
            type="soft",
            rule_name="minimize_distance",
            params={"device_a": dev_a, "device_b": dev_b},
            weight=weight,
        )
        result.constraints.append(c)
        generated.append({"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight})

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": generated,
        "explanation": f"设备组 {devices} 应尽量靠近（优先级: {priority}）",
    })


def _handle_far_apart(intent: Intent, result: InterpretResult) -> None:
    """far_apart：设备组内两两最大化距离。"""
    devices: list[str] = intent.params.get("devices", [])
    priority: str = intent.params.get("priority", "medium")

    if len(devices) < 2:
        result.errors.append(f"far_apart: 参数 'devices' 至少需要 2 个设备，当前 {len(devices)} 个")
        return

    weight = _PRIORITY_WEIGHTS.get(priority, _DEFAULT_WEIGHT)
    generated: list[dict] = []
    for dev_a, dev_b in itertools.combinations(devices, 2):
        c = Constraint(
            type="soft",
            rule_name="maximize_distance",
            params={"device_a": dev_a, "device_b": dev_b},
            weight=weight,
        )
        result.constraints.append(c)
        generated.append({"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight})

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": generated,
        "explanation": f"设备组 {devices} 应尽量分散（优先级: {priority}）",
    })


def _handle_max_distance(intent: Intent, result: InterpretResult) -> None:
    """max_distance：两设备间距不超过指定值。"""
    device_a = intent.params.get("device_a")
    device_b = intent.params.get("device_b")
    distance = intent.params.get("distance")

    if device_a is None or device_b is None or distance is None:
        result.errors.append(
            f"max_distance: 缺少必要参数，需要 'device_a'、'device_b' 和 'distance'，"
            f"当前: device_a={device_a}, device_b={device_b}, distance={distance}"
        )
        return

    c = Constraint(
        type="hard",
        rule_name="distance_less_than",
        params={"device_a": device_a, "device_b": device_b, "distance": distance},
    )
    result.constraints.append(c)

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": [{"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight}],
        "explanation": f"设备 '{device_a}' 与 '{device_b}' 之间的距离不得超过 {distance} 米",
    })


def _handle_min_distance(intent: Intent, result: InterpretResult) -> None:
    """min_distance：两设备间距不小于指定值。"""
    device_a = intent.params.get("device_a")
    device_b = intent.params.get("device_b")
    distance = intent.params.get("distance")

    if device_a is None or device_b is None or distance is None:
        result.errors.append(
            f"min_distance: 缺少必要参数，需要 'device_a'、'device_b' 和 'distance'，"
            f"当前: device_a={device_a}, device_b={device_b}, distance={distance}"
        )
        return

    c = Constraint(
        type="hard",
        rule_name="distance_greater_than",
        params={"device_a": device_a, "device_b": device_b, "distance": distance},
    )
    result.constraints.append(c)

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": [{"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight}],
        "explanation": f"设备 '{device_a}' 与 '{device_b}' 之间的距离不得小于 {distance} 米",
    })


def _handle_min_spacing(intent: Intent, result: InterpretResult) -> None:
    """min_spacing：所有设备之间的最小间隙。"""
    min_gap: float = intent.params.get("min_gap", 0.3)

    c = Constraint(
        type="hard",
        rule_name="min_spacing",
        params={"min_gap": min_gap},
    )
    result.constraints.append(c)

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": [{"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight}],
        "explanation": f"所有设备之间至少保持 {min_gap} 米的间隙",
    })


def _handle_face_outward(intent: Intent, result: InterpretResult) -> None:
    """face_outward：设备朝向偏好为向外。"""
    c = Constraint(
        type="soft",
        rule_name="prefer_orientation_mode",
        params={"mode": "outward"},
    )
    result.constraints.append(c)

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": [{"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight}],
        "explanation": "设备开口偏好朝向实验室外侧",
    })


def _handle_face_inward(intent: Intent, result: InterpretResult) -> None:
    """face_inward：设备朝向偏好为向内。"""
    c = Constraint(
        type="soft",
        rule_name="prefer_orientation_mode",
        params={"mode": "inward"},
    )
    result.constraints.append(c)

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": [{"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight}],
        "explanation": "设备开口偏好朝向实验室内侧",
    })


def _handle_align_cardinal(intent: Intent, result: InterpretResult) -> None:
    """align_cardinal：设备偏好对齐到主轴方向。"""
    c = Constraint(
        type="soft",
        rule_name="prefer_aligned",
        params={},
    )
    result.constraints.append(c)

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": [{"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight}],
        "explanation": "设备偏好与实验室主轴对齐（0°/90°/180°/270°）",
    })


def _handle_workflow_hint(intent: Intent, result: InterpretResult) -> None:
    """workflow_hint：工作流顺序暗示，相邻步骤设备靠近。"""
    workflow: str = intent.params.get("workflow", "")
    devices: list[str] = intent.params.get("devices", [])

    if len(devices) < 2:
        result.errors.append(
            f"workflow_hint: 参数 'devices' 至少需要 2 个设备，当前 {len(devices)} 个"
        )
        return

    generated: list[dict] = []
    for dev_a, dev_b in zip(devices[:-1], devices[1:]):
        c = Constraint(
            type="soft",
            rule_name="minimize_distance",
            params={"device_a": dev_a, "device_b": dev_b},
        )
        result.constraints.append(c)
        generated.append({"type": c.type, "rule_name": c.rule_name, "params": c.params, "weight": c.weight})
        result.workflow_edges.append([dev_a, dev_b])

    result.translations.append({
        "source_intent": intent.intent,
        "source_description": intent.description,
        "source_params": intent.params,
        "generated_constraints": generated,
        "explanation": f"工作流 '{workflow}' 中相邻步骤设备应靠近",
        "confidence": "low",
    })


# 意图处理器分发表
_HANDLERS: dict[str, Callable[[Intent, InterpretResult], None]] = {
    "reachable_by": _handle_reachable_by,
    "close_together": _handle_close_together,
    "far_apart": _handle_far_apart,
    "max_distance": _handle_max_distance,
    "min_distance": _handle_min_distance,
    "min_spacing": _handle_min_spacing,
    "face_outward": _handle_face_outward,
    "face_inward": _handle_face_inward,
    "align_cardinal": _handle_align_cardinal,
    "workflow_hint": _handle_workflow_hint,
}


def interpret_intents(intents: list[Intent]) -> InterpretResult:
    """将意图列表翻译为约束列表。

    Args:
        intents: 语义化意图列表（通常由 LLM 生成）

    Returns:
        InterpretResult，包含约束、翻译记录、错误信息和工作流边
    """
    result = InterpretResult()

    for intent in intents:
        handler = _HANDLERS.get(intent.intent)
        if handler is None:
            result.errors.append(f"未知意图类型: '{intent.intent}'，跳过处理")
            continue
        handler(intent, result)

    return result
