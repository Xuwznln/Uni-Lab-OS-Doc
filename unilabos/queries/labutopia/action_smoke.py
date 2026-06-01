from __future__ import annotations

from typing import Any, Dict, List, Optional

from unilabos.queries.engine import QueryEngine, QueryNotFound


def _axis_index(axis: str) -> int:
    return {"x": 0, "y": 1, "z": 2}.get(axis, 0)


def _copy_xyz(value: Optional[List[float]]) -> Optional[List[float]]:
    if not isinstance(value, list) or len(value) != 3:
        return None
    return [float(value[0]), float(value[1]), float(value[2])]


def _query_pose_dict(engine: QueryEngine, target: Optional[str]) -> Optional[Dict[str, Any]]:
    if not target:
        return None
    try:
        return engine.query_pose(target).to_dict()
    except QueryNotFound:
        return None


def _pose_xyz(engine: QueryEngine, target: Optional[str]) -> Optional[List[float]]:
    pose = _query_pose_dict(engine, target)
    if pose is None:
        return None
    return _copy_xyz(pose.get("xyz"))


def _offset_pose(xyz: Optional[List[float]], offset: List[float]) -> Optional[List[float]]:
    if xyz is None:
        return None
    return [xyz[i] + float(offset[i]) for i in range(3)]


def _first_postcondition(schema: Dict[str, Any], check_type: str) -> Optional[Dict[str, Any]]:
    for condition in schema.get("postconditions") or []:
        if isinstance(condition, dict) and condition.get("type") == check_type:
            return dict(condition)
    return None


def _press_button_smoke(engine: QueryEngine, schema: Dict[str, Any], target: Optional[str]) -> Dict[str, Any]:
    targets = dict((schema.get("args") or {}).get("targets") or {})
    button_target = target or targets.get("target_button_path") or targets.get("sub_obj_path")
    postcondition = _first_postcondition(schema, "pose_axis_gt") or {}
    verification_target = postcondition.get("target") or targets.get("sub_obj_path") or button_target
    axis = str(postcondition.get("axis", "x"))
    idx = _axis_index(axis)
    threshold = float(postcondition.get("threshold", 0.0))

    button_xyz = _pose_xyz(engine, button_target)
    verification_xyz = _pose_xyz(engine, verification_target) or button_xyz
    planned_xyz = _copy_xyz(verification_xyz) or [0.0, 0.0, 0.0]
    planned_xyz[idx] = max(float(planned_xyz[idx]), threshold + 0.01)

    pre_offset = [0.0, 0.0, 0.0]
    pre_offset[idx] = -0.06
    press_offset = [0.0, 0.0, 0.0]
    press_offset[idx] = 0.02
    plan = {
        "controller": "classical_press_button_contract",
        "target": button_target,
        "verification_target": verification_target,
        "axis": axis,
        "threshold": threshold,
        "waypoints": [
            {
                "name": "pre_press",
                "xyz": _offset_pose(button_xyz, pre_offset),
                "description": "approach pose offset from the target affordance",
            },
            {
                "name": "contact",
                "xyz": button_xyz,
                "description": "nominal contact pose at the target affordance",
            },
            {
                "name": "pressed",
                "xyz": _offset_pose(button_xyz, press_offset),
                "description": "small displacement along the configured press axis",
            },
            {
                "name": "retreat",
                "xyz": _offset_pose(button_xyz, pre_offset),
                "description": "retreat to the approach pose",
            },
        ],
        "status": "contract_only",
        "limits": {
            "executes_physics": False,
            "executes_robot_motion": False,
        },
    }

    current_context = {"poses": {verification_target: {"xyz": verification_xyz}}} if verification_xyz is not None else {"poses": {}}
    planned_context = {"poses": {verification_target: {"xyz": planned_xyz}}}
    current = engine.query_verification(
        task_id="press_button.current_pose",
        action=schema["action"],
        context=current_context,
    ).to_dict()
    planned = engine.query_verification(
        task_id="press_button.planned_pose",
        action=schema["action"],
        context=planned_context,
    ).to_dict()
    return {
        "action": schema["action"],
        "schema": schema,
        "plan": plan,
        "verification": {
            "current_pose": current,
            "planned_contract": planned,
        },
    }


def build_action_smoke(engine: QueryEngine, action: str, target: Optional[str] = None) -> Dict[str, Any]:
    schema = engine.query_action_schema(action).to_dict()
    if schema["action"] == "press_button":
        return _press_button_smoke(engine, schema, target=target)
    return {
        "action": schema["action"],
        "schema": schema,
        "plan": {
            "status": "unsupported_action",
            "reason": "Only press_button has a classical contract smoke at this stage.",
        },
        "verification": {},
    }
