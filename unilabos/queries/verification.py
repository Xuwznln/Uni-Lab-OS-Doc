from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Optional

from unilabos.queries.models import VerificationResult


Evaluator = Callable[[Dict[str, Any], Dict[str, Any]], tuple[bool, Dict[str, Any]]]


def _get_path(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _eval_mass_in_range(condition: Dict[str, Any], context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    device = str(condition.get("device", ""))
    value = _get_path(context, f"states.{device}.mass_g", context.get("mass_g"))
    minimum = condition.get("min")
    maximum = condition.get("max")
    ok = value is not None and (minimum is None or value >= minimum) and (maximum is None or value <= maximum)
    return ok, {"device": device, "actual_mass_g": value, "min": minimum, "max": maximum}


def _eval_temp_in_range(condition: Dict[str, Any], context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    device = str(condition.get("device", ""))
    value = _get_path(context, f"states.{device}.temperature_c", context.get("temperature_c"))
    minimum = condition.get("min")
    maximum = condition.get("max")
    ok = value is not None and (minimum is None or value >= minimum) and (maximum is None or value <= maximum)
    return ok, {"device": device, "actual_temperature_c": value, "min": minimum, "max": maximum}


def _eval_object_present(condition: Dict[str, Any], context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    obj = str(condition.get("object") or condition.get("target") or "")
    present = bool(_get_path(context, f"objects.{obj}.present", context.get("object_present", False)))
    return present, {"object": obj, "present": present}


def _eval_device_state(condition: Dict[str, Any], context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    device = str(condition.get("device", ""))
    expected = condition.get("state")
    actual = _get_path(context, f"states.{device}.state", context.get("device_state"))
    return actual == expected, {"device": device, "expected": expected, "actual": actual}


def _eval_pose_near(condition: Dict[str, Any], context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    target = str(condition.get("target", ""))
    actual = _get_path(context, f"poses.{target}.xyz", condition.get("actual"))
    expected = condition.get("expected")
    tolerance = float(condition.get("tolerance", 0.01))
    if not isinstance(actual, list) or not isinstance(expected, list) or len(actual) != 3 or len(expected) != 3:
        return False, {"target": target, "actual": actual, "expected": expected, "tolerance": tolerance}
    distance = math.dist([float(x) for x in actual], [float(x) for x in expected])
    return distance <= tolerance, {"target": target, "actual": actual, "expected": expected, "distance": distance, "tolerance": tolerance}


def _eval_pose_axis_gt(condition: Dict[str, Any], context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    target = str(condition.get("target", ""))
    axis = str(condition.get("axis", "x"))
    idx = {"x": 0, "y": 1, "z": 2}.get(axis, 0)
    pose = _get_path(context, f"poses.{target}.xyz", condition.get("actual"))
    threshold = float(condition.get("threshold", 0.0))
    actual = pose[idx] if isinstance(pose, list) and len(pose) > idx else None
    return actual is not None and float(actual) > threshold, {"target": target, "axis": axis, "actual": actual, "threshold": threshold}


def _eval_gripper_state(condition: Dict[str, Any], context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    expected = condition.get("state")
    actual = _get_path(context, "robot.gripper_state", context.get("gripper_state"))
    return actual == expected, {"expected": expected, "actual": actual}


def _eval_time_elapsed(condition: Dict[str, Any], context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    elapsed = float(context.get("elapsed_s", 0.0))
    minimum = float(condition.get("min_s", 0.0))
    return elapsed >= minimum, {"elapsed_s": elapsed, "min_s": minimum}


DEFAULT_EVALUATORS: Dict[str, Evaluator] = {
    "mass_in_range": _eval_mass_in_range,
    "temp_in_range": _eval_temp_in_range,
    "object_present": _eval_object_present,
    "device_state": _eval_device_state,
    "pose_near": _eval_pose_near,
    "pose_axis_gt": _eval_pose_axis_gt,
    "gripper_state": _eval_gripper_state,
    "time_elapsed": _eval_time_elapsed,
}


class VerificationEngine:
    def __init__(self, evaluators: Optional[Dict[str, Evaluator]] = None):
        self.evaluators = dict(DEFAULT_EVALUATORS)
        if evaluators:
            self.evaluators.update(evaluators)
        self._tasks: Dict[str, List[Dict[str, Any]]] = {}

    def register_task(self, task_id: str, postconditions: Iterable[Dict[str, Any]]) -> None:
        self._tasks[task_id] = [dict(item) for item in postconditions]

    def verify(self, task_id: str, context: Optional[Dict[str, Any]] = None, postconditions: Optional[Iterable[Dict[str, Any]]] = None) -> VerificationResult:
        checks = [dict(item) for item in (postconditions if postconditions is not None else self._tasks.get(task_id, []))]
        context = dict(context or {})
        failures: List[Dict[str, Any]] = []
        evidence: Dict[str, Any] = {}
        for index, condition in enumerate(checks):
            check_type = str(condition.get("type", ""))
            evaluator = self.evaluators.get(check_type)
            if evaluator is None:
                failures.append({"index": index, "condition": condition, "error": f"unknown evaluator: {check_type}"})
                continue
            ok, detail = evaluator(condition, context)
            evidence[f"{index}:{check_type}"] = detail
            if not ok:
                failures.append({"index": index, "condition": condition, "evidence": detail})
        return VerificationResult(ok=not failures and bool(checks), task_id=task_id, evidence=evidence, failures=failures)
