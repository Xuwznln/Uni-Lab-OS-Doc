"""RMF task 信封组装与下发（#18 §2.5 / §4.3 / #17 §7.1）。

信封构造是纯函数（可单测，对齐 `rmf_demos_tasks/dispatch_*.py`）；实际下发二选一：
- ROS 路径（首选，OS 为中心）：publish `rmf_task_msgs/ApiRequest` 到 `task_api_requests`。
- REST 路径（可选）：POST 展示面 api-server `/tasks/dispatch_task`。

为可测试，`RmfTaskDispatcher` 接受一个 `publish_fn(json_msg, request_id)` 回调，
ROS publisher 的接线在 `attach_ros` 里惰性导入 rclpy / rmf_task_msgs。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from unilabos.sim.fleet.rmf.coordinate_transform import deg_to_rad

REQUESTER_DEFAULT = "unilab"
LABELS_DEFAULT = ["unilab"]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _wrap(request: Dict[str, Any], fleet: Optional[str], robot: Optional[str]) -> Dict[str, Any]:
    """套上 dispatch_task_request / robot_task_request 外层。"""
    if fleet and robot:
        return {"type": "robot_task_request", "robot": robot, "fleet": fleet, "request": request}
    if fleet:
        request.setdefault("fleet_name", fleet)
    return {"type": "dispatch_task_request", "request": request}


def build_go_to_request(
    place: str,
    orientation_deg: Optional[float] = None,
    *,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
    requester: str = REQUESTER_DEFAULT,
) -> Dict[str, Any]:
    """compose + go_to_place（对齐 dispatch_go_to_place.py）。"""
    place_json: Dict[str, Any] = {"waypoint": place}
    if orientation_deg is not None:
        place_json["orientation"] = deg_to_rad(orientation_deg)
    activity = {"category": "go_to_place", "description": {"one_of": [place_json]}}
    request = {
        "category": "compose",
        "description": {"category": "go_to_place", "phases": [{"activity": activity}]},
        "unix_millis_request_time": _now_ms(),
        "unix_millis_earliest_start_time": _now_ms(),
        "requester": requester,
        "labels": list(LABELS_DEFAULT),
    }
    return _wrap(request, fleet, robot)


def build_delivery_request(
    pickup_place: str,
    pickup_handler: str,
    dropoff_place: str,
    dropoff_handler: str,
    payload: Optional[List[Dict[str, Any]]] = None,
    *,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
    requester: str = REQUESTER_DEFAULT,
) -> Dict[str, Any]:
    """delivery（单 pickup/dropoff，对齐 dispatch_delivery.py）。"""
    request = {
        "category": "delivery",
        "description": {
            "pickup": {"place": pickup_place, "handler": pickup_handler, "payload": list(payload or [])},
            "dropoff": {"place": dropoff_place, "handler": dropoff_handler, "payload": []},
        },
        "unix_millis_request_time": _now_ms(),
        "unix_millis_earliest_start_time": _now_ms(),
        "requester": requester,
        "labels": list(LABELS_DEFAULT),
    }
    return _wrap(request, fleet, robot)


def build_patrol_request(
    places: List[str],
    rounds: int = 1,
    *,
    fleet: Optional[str] = None,
    robot: Optional[str] = None,
    requester: str = REQUESTER_DEFAULT,
) -> Dict[str, Any]:
    """patrol（对齐 dispatch_patrol.py）。"""
    request = {
        "category": "patrol",
        "description": {"places": list(places), "rounds": int(rounds)},
        "unix_millis_request_time": _now_ms(),
        "unix_millis_earliest_start_time": _now_ms(),
        "requester": requester,
        "labels": list(LABELS_DEFAULT),
    }
    return _wrap(request, fleet, robot)


def build_cancel_request(task_id: str) -> Dict[str, Any]:
    return {"type": "cancel_task_request", "task_id": task_id}


class RmfTaskDispatcher:
    """把 task 信封下发到 RMF。默认走 ROS `task_api_requests`。

    `publish_fn` 注入用于单测；生产环境调用 `attach_ros(node)` 接线真实 publisher。
    """

    def __init__(self, publish_fn: Optional[Callable[[str, str], None]] = None):
        self._publish_fn = publish_fn
        self._pub = None
        self._node = None

    def attach_ros(self, node: Any, topic: str = "task_api_requests") -> None:
        """惰性接线 ROS publisher（TRANSIENT_LOCAL QoS，对齐 dispatch_delivery.py）。"""
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
        from rmf_task_msgs.msg import ApiRequest

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._node = node
        self._ApiRequest = ApiRequest
        self._pub = node.create_publisher(ApiRequest, topic, qos)

    def dispatch(self, envelope: Dict[str, Any], request_id: Optional[str] = None) -> str:
        """下发一个信封，返回 request_id。"""
        rid = request_id or f"unilab_{uuid.uuid4()}"
        json_msg = json.dumps(envelope)
        if self._publish_fn is not None:
            self._publish_fn(json_msg, rid)
            return rid
        if self._pub is not None:
            msg = self._ApiRequest()
            msg.request_id = rid
            msg.json_msg = json_msg
            self._pub.publish(msg)
            return rid
        raise RuntimeError("RmfTaskDispatcher 未接线：请提供 publish_fn 或先调用 attach_ros(node)")
