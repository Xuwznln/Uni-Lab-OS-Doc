#!/usr/bin/env python3
"""轻量 MutexGroup supervisor（对齐 Open-RMF 官方消息语义）。

Humble apt 的 rmf_fleet_adapter 未提供 mutex_group_supervisor 可执行文件。
本节点用已有 rmf_fleet_msgs 完成仲裁，供 strict_fleet_adapter 申请/释放互斥组。

语义（对齐上游）：
- 同一 group 同时只授予一个 claimant
- claim_time 早者优先
- MODE_LOCK 需周期性心跳，超时释放
- MODE_RELEASE 立即释放（仅当 claim_time 不晚于已登记请求）
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import rclpy
from builtin_interfaces.msg import Time as TimeMsg
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from rmf_fleet_msgs.msg import MutexGroupAssignment, MutexGroupRequest, MutexGroupStates

UNCLAIMED = (1 << 64) - 1
REQUEST_TOPIC = "mutex_group_request"
STATES_TOPIC = "mutex_group_states"
HEARTBEAT_PERIOD_S = 2.0
CLAIM_TIMEOUT_S = 10.0


def _time_msg_key(t: TimeMsg) -> Tuple[int, int]:
    return (int(t.sec), int(t.nanosec))


def _time_leq(a: TimeMsg, b: TimeMsg) -> bool:
    return _time_msg_key(a) <= _time_msg_key(b)


def _time_lt(a: TimeMsg, b: TimeMsg) -> bool:
    return _time_msg_key(a) < _time_msg_key(b)


@dataclass
class _ClaimTimestamps:
    claim_time: TimeMsg
    heartbeat_mono: float


class MutexGroupSupervisor(Node):
    def __init__(self) -> None:
        super().__init__("mutex_group_supervisor")
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=100,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._lock = threading.Lock()
        # group -> claimant -> timestamps
        self._claims: Dict[str, Dict[int, _ClaimTimestamps]] = {}
        self._states = MutexGroupStates()
        self._states.assignments = []

        self._request_sub = self.create_subscription(
            MutexGroupRequest,
            REQUEST_TOPIC,
            self._on_request,
            qos,
        )
        self._state_pub = self.create_publisher(MutexGroupStates, STATES_TOPIC, qos)
        self._timer = self.create_timer(HEARTBEAT_PERIOD_S, self._on_heartbeat)
        self.get_logger().info(
            f"[mutex_supervisor] listening on {REQUEST_TOPIC}, "
            f"publishing {STATES_TOPIC}"
        )

    def _on_request(self, request: MutexGroupRequest) -> None:
        with self._lock:
            group = str(request.group or "")
            if not group:
                return
            claimant = int(request.claimant)
            if request.mode == MutexGroupRequest.MODE_RELEASE:
                claims = self._claims.get(group)
                if not claims:
                    return
                existing = claims.get(claimant)
                if existing is None:
                    return
                if _time_leq(existing.claim_time, request.claim_time):
                    del claims[claimant]
                    self._pick_next(group)
                    self._state_pub.publish(self._states)
                return

            # MODE_LOCK
            now_mono = time.monotonic()
            group_claims = self._claims.setdefault(group, {})
            group_claims[claimant] = _ClaimTimestamps(
                claim_time=request.claim_time,
                heartbeat_mono=now_mono,
            )
            current = self._current_claimant(group)
            if current is not None and current != UNCLAIMED:
                # 已有持有者：仅刷新请求队列，不抢占（除非心跳超时后 pick_next）
                return
            self._pick_next(group)
            self._state_pub.publish(self._states)

    def _current_claimant(self, group: str) -> Optional[int]:
        for a in self._states.assignments:
            if a.group == group:
                return int(a.claimant)
        return None

    def _pick_next(self, group: str) -> None:
        claimants = self._claims.get(group) or {}
        earliest_claimant = UNCLAIMED
        earliest_time = TimeMsg(sec=0, nanosec=0)
        found = False
        for claimant, ts in claimants.items():
            if not found or _time_lt(ts.claim_time, earliest_time):
                found = True
                earliest_claimant = int(claimant)
                earliest_time = ts.claim_time

        for a in self._states.assignments:
            if a.group == group:
                a.claimant = earliest_claimant
                a.claim_time = earliest_time
                return
        assignment = MutexGroupAssignment()
        assignment.group = group
        assignment.claimant = earliest_claimant
        assignment.claim_time = earliest_time
        self._states.assignments.append(assignment)

    def _on_heartbeat(self) -> None:
        with self._lock:
            now_mono = time.monotonic()
            changed = False
            for group, claims in list(self._claims.items()):
                stale = [
                    c
                    for c, ts in claims.items()
                    if (now_mono - ts.heartbeat_mono) > CLAIM_TIMEOUT_S
                ]
                if not stale:
                    continue
                current = self._current_claimant(group)
                need_repick = False
                for c in stale:
                    if current == c:
                        need_repick = True
                    del claims[c]
                    changed = True
                if need_repick:
                    self._pick_next(group)
            self._state_pub.publish(self._states)


def main(argv=None) -> None:
    rclpy.init(args=argv)
    node = MutexGroupSupervisor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
