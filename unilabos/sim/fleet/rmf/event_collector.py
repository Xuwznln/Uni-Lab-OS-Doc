"""RMF 运行态采集与归一化（#18 §4.4 / §2.4 / #17 §5.3）。

归一化函数是纯函数（输入 dict-like，可单测，无需 ROS）：把 RMF
FleetState/RobotState/DoorState/LiftState 转为 Go 友好的 DTO；同时完成
battery 0-100→0-1、RobotMode 整数→字符串状态、DoorMode/LiftState 枚举换算。

`EventCollector` 在 `attach_ros` 里惰性订阅 RMF ROS topics，把归一化事件交给回调
（通常是 backend_reporter 的批量上报）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# RobotMode.mode（uint32）→ DTO status
_ROBOT_MODE_TO_STATUS = {
    0: "idle",       # MODE_IDLE
    1: "charging",   # MODE_CHARGING
    2: "moving",     # MODE_MOVING
    3: "idle",       # MODE_PAUSED
    4: "idle",       # MODE_WAITING
    5: "error",      # MODE_EMERGENCY
    6: "moving",     # MODE_GOING_HOME
    7: "moving",     # MODE_DOCKING
    8: "error",      # MODE_ADAPTER_ERROR
    9: "moving",     # MODE_CLEANING
    10: "moving",    # MODE_PERFORMING_ACTION
    11: "idle",      # MODE_ACTION_COMPLETED
}

_DOOR_MODE = {0: "closed", 1: "moving", 2: "open", 3: "offline", 4: "offline"}
_LIFT_DOOR = {0: "closed", 1: "moving", 2: "open"}
_LIFT_MOTION = {0: "stopped", 1: "up", 2: "down", 3: "unknown"}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """同时支持 dict 与对象属性访问（ROS msg / pydantic / dict 通吃）。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def normalize_battery(battery_percent: Optional[float]) -> Optional[float]:
    """RMF ROS msg battery_percent(0-100) → DTO battery(0-1)。已是 0-1 则原样返回。"""
    if battery_percent is None:
        return None
    val = float(battery_percent)
    if val > 1.0:
        return round(val / 100.0, 4)
    return round(val, 4)


def robot_mode_to_status(mode: Optional[int]) -> str:
    if mode is None:
        return "idle"
    return _ROBOT_MODE_TO_STATUS.get(int(mode), "idle")


def normalize_robot_state(robot: Any, fleet_name: str, mode: str = "sim", stale: bool = False) -> Dict[str, Any]:
    """单个 RobotState → RmfRobotStateDTO（#17 §5.3）。"""
    loc = _get(robot, "location") or {}
    robot_mode = _get(robot, "mode")
    mode_int = _get(robot_mode, "mode") if robot_mode is not None else None
    return {
        "robotId": _get(robot, "name", ""),
        "fleetName": fleet_name,
        "mode": mode,  # sim/real/twin（运行模式，非 RobotMode）
        "mapName": _get(loc, "level_name", ""),
        "pose": {
            "x": float(_get(loc, "x", 0.0) or 0.0),
            "y": float(_get(loc, "y", 0.0) or 0.0),
            "yaw": float(_get(loc, "yaw", 0.0) or 0.0),
        },
        "battery": normalize_battery(_get(robot, "battery_percent")),
        "taskId": _get(robot, "task_id", "") or None,
        "status": robot_mode_to_status(mode_int),
        "stale": stale,
    }


def normalize_fleet_state(fleet: Any, runtime_mode: str = "sim", stale: bool = False) -> List[Dict[str, Any]]:
    """FleetState → RmfRobotStateDTO 列表。robots 兼容 list（ROS）/ dict（rmf_api）。"""
    fleet_name = _get(fleet, "name", "")
    robots = _get(fleet, "robots") or []
    items = robots.values() if isinstance(robots, dict) else robots
    return [normalize_robot_state(r, fleet_name, mode=runtime_mode, stale=stale) for r in items]


def normalize_door_state(door: Any, stale: bool = False) -> Dict[str, Any]:
    current = _get(door, "current_mode") or {}
    value = _get(current, "value")
    return {
        "doorName": _get(door, "door_name", ""),
        "mode": _DOOR_MODE.get(int(value) if value is not None else 4, "offline"),
        "stale": stale,
    }


def normalize_lift_state(lift: Any, stale: bool = False) -> Dict[str, Any]:
    door_state = _get(lift, "door_state")
    motion_state = _get(lift, "motion_state")
    return {
        "liftName": _get(lift, "lift_name", ""),
        "currentFloor": _get(lift, "current_floor", ""),
        "destinationFloor": _get(lift, "destination_floor", "") or None,
        "doorState": _LIFT_DOOR.get(int(door_state) if door_state is not None else 0, "closed"),
        "motionState": _LIFT_MOTION.get(int(motion_state) if motion_state is not None else 3, "unknown"),
        "sessionId": _get(lift, "session_id", "") or None,
        "stale": stale,
    }


class EventCollector:
    """订阅 RMF ROS topics → 归一化事件 → on_event 回调。

    `on_event(event_type, payload)`：event_type ∈
    robot_state / door_state / lift_state；payload 为上面归一化后的 DTO。
    """

    def __init__(self, on_event: Callable[[str, Dict[str, Any]], None], runtime_mode: str = "sim"):
        self._on_event = on_event
        self._runtime_mode = runtime_mode
        self._node = None
        self._subs: List[Any] = []
        self._stale_fn: Callable[[], bool] = lambda: False

    def set_stale_provider(self, fn: Callable[[], bool]) -> None:
        """注入 stale 判定（scene_hash 与当前发布版不一致时为 True）。"""
        self._stale_fn = fn

    def attach_ros(
        self,
        node: Any,
        fleet_topic: str = "/fleet_states",
        door_topic: str = "/door_states",
        lift_topic: str = "/lift_states",
    ) -> None:
        """惰性订阅 RMF 状态 topic（仅在有 ROS 环境时调用）。"""
        from rmf_door_msgs.msg import DoorState
        from rmf_fleet_msgs.msg import FleetState
        from rmf_lift_msgs.msg import LiftState

        self._node = node
        self._subs.append(node.create_subscription(FleetState, fleet_topic, self._on_fleet, 10))
        self._subs.append(node.create_subscription(DoorState, door_topic, self._on_door, 10))
        self._subs.append(node.create_subscription(LiftState, lift_topic, self._on_lift, 10))

    def _on_fleet(self, msg: Any) -> None:
        stale = self._stale_fn()
        for dto in normalize_fleet_state(msg, runtime_mode=self._runtime_mode, stale=stale):
            self._on_event("robot_state", dto)

    def _on_door(self, msg: Any) -> None:
        self._on_event("door_state", normalize_door_state(msg, stale=self._stale_fn()))

    def _on_lift(self, msg: Any) -> None:
        self._on_event("lift_state", normalize_lift_state(msg, stale=self._stale_fn()))
