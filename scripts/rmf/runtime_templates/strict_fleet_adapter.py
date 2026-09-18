#!/usr/bin/env python3
"""严格遵循 RMF 时空轨迹的 fleet adapter（#25 / rmf-strict-planning 规则）。

问题：官方 rmf_demos_fleet_adapter 的 RobotCommandHandle.follow_new_path 在每个 waypoint
完成后 **立即** 下发下一段 navigate，完全不遵守 Plan::Waypoint.time —— 即 RMF traffic
scheduler 协商出的“等待/让行（hold）”时刻在执行端没有被执行。它默认“真实小车会按规划速度行驶”，
而理想化执行器（mock）“收到点就全速冲”，于是 hold 被抹掉，两车在汇合点/共享 waypoint 撞车。

本文件用 StrictRobotCommandHandle 子类化官方 RobotCommandHandle，覆盖 follow_new_path：
1) 到达每个 waypoint 后按 Plan::Waypoint.time 原地等待（真实 hold/让行）
2) 进入带 mutex 的点/车道前，经官方 MutexGroup 消息申请锁；离开后释放；等锁超时则 replan

运行环境：ROS2 python（run_rmf_layout.sh 已 unset PYTHONPATH 并 source /opt/ros + ros2sp）。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import zlib
from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional, Set

import rmf_demos_fleet_adapter.fleet_adapter as fa
from builtin_interfaces.msg import Time as TimeMsg
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from rmf_demos_fleet_adapter.RobotCommandHandle import RobotCommandHandle, RobotState
from rmf_fleet_msgs.msg import MutexGroupRequest, MutexGroupStates

UNCLAIMED = (1 << 64) - 1
REQUEST_TOPIC = "mutex_group_request"
STATES_TOPIC = "mutex_group_states"


def _load_mutex_maps(nav_graph_path: str) -> tuple[Dict[int, str], Dict[int, str]]:
    """从 nav_graphs/0.yaml 读取 waypoint/lane -> mutex group。"""
    import yaml

    path = Path(nav_graph_path).expanduser().resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    level = ((data.get("levels") or {}).get("L1") or {})
    verts = level.get("vertices") or []
    lanes = level.get("lanes") or []

    wp_mutex: Dict[int, str] = {}
    for idx, v in enumerate(verts):
        if not (isinstance(v, list) and len(v) >= 3 and isinstance(v[2], dict)):
            continue
        mutex = str(v[2].get("mutex") or "").strip()
        if mutex:
            wp_mutex[idx] = mutex

    lane_mutex: Dict[int, str] = {}
    for idx, lane in enumerate(lanes):
        if not (isinstance(lane, list) and len(lane) >= 3 and isinstance(lane[2], dict)):
            continue
        mutex = str(lane[2].get("mutex") or "").strip()
        if mutex:
            lane_mutex[idx] = mutex
    return wp_mutex, lane_mutex


def _claimant_id(fleet_name: str, robot_name: str) -> int:
    raw = zlib.crc32(f"{fleet_name}/{robot_name}".encode("utf-8")) & 0xFFFFFFFF
    # 避开 UNCLAIMED
    return raw if raw != UNCLAIMED else (raw ^ 0x1)


def _now_time_msg(node) -> TimeMsg:
    stamp = node.get_clock().now().to_msg()
    return TimeMsg(sec=int(stamp.sec), nanosec=int(stamp.nanosec))


class StrictRobotCommandHandle(RobotCommandHandle):
    """在官方基础上：hold 到计划时刻 + MutexGroup 锁定热点点/车道。"""

    _STALL_PROGRESS_EPS_M = 0.03
    _STALL_REPLAN_AFTER_WALL_S = 1.0
    _STALL_REPLAN_COOLDOWN_WALL_S = 2.0
    _MUTEX_WAIT_TIMEOUT_WALL_S = 8.0
    _MUTEX_HEARTBEAT_WALL_S = 1.0

    # 由 main() 在 initialize_fleet 前注入
    _nav_graph_path: Optional[str] = None
    _wp_mutex: Dict[int, str] = {}
    _lane_mutex: Dict[int, str] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._claimant = _claimant_id(self.fleet_name, self.name)
        self._held_groups: Dict[str, TimeMsg] = {}
        self._states_by_group: Dict[str, int] = {}
        self._states_lock = threading.Lock()

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=100,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._mutex_req_pub = self.node.create_publisher(
            MutexGroupRequest, REQUEST_TOPIC, qos
        )
        self._mutex_state_sub = self.node.create_subscription(
            MutexGroupStates,
            STATES_TOPIC,
            self._on_mutex_states,
            qos,
        )
        self.node.get_logger().info(
            f"[mutex] {self.name} claimant={self._claimant} "
            f"wp_mutex={len(self._wp_mutex)} lane_mutex={len(self._lane_mutex)}"
        )
        # follow_path 线程结束后，占用点 mutex 仍需心跳维持
        self._mutex_hb_timer = self.node.create_timer(
            self._MUTEX_HEARTBEAT_WALL_S, self._heartbeat_held_mutexes
        )

    def _on_mutex_states(self, msg: MutexGroupStates) -> None:
        with self._states_lock:
            self._states_by_group = {
                str(a.group): int(a.claimant) for a in (msg.assignments or [])
            }

    def _groups_for_waypoint(self, waypoint) -> Set[str]:
        groups: Set[str] = set()
        graph_index = getattr(waypoint, "graph_index", None)
        if graph_index is not None:
            g = self._wp_mutex.get(int(graph_index))
            if g:
                groups.add(g)
        for lane_idx in getattr(waypoint, "approach_lanes", None) or []:
            g = self._lane_mutex.get(int(lane_idx))
            if g:
                groups.add(g)
        return groups

    def _occupancy_groups(self) -> Set[str]:
        """机器人当前占用的 waypoint/lane 对应 mutex（空闲驻停时仍需持有）。"""
        groups: Set[str] = set()
        if self.on_waypoint is not None:
            g = self._wp_mutex.get(int(self.on_waypoint))
            if g:
                groups.add(g)
        if self.on_lane is not None:
            g = self._lane_mutex.get(int(self.on_lane))
            if g:
                groups.add(g)
        return groups

    def _heartbeat_held_mutexes(self) -> None:
        for group, claim_time in list(self._held_groups.items()):
            self._publish_mutex(group, MutexGroupRequest.MODE_LOCK, claim_time)

    def _publish_mutex(self, group: str, mode: int, claim_time: TimeMsg) -> None:
        msg = MutexGroupRequest()
        msg.group = group
        msg.claimant = self._claimant
        msg.claim_time = claim_time
        msg.mode = mode
        try:
            self._mutex_req_pub.publish(msg)
        except Exception:  # noqa: BLE001 —— shutdown 时 context 可能已失效
            pass

    def _is_holder(self, group: str) -> bool:
        with self._states_lock:
            return self._states_by_group.get(group) == self._claimant

    def _acquire_groups(self, groups: Set[str]) -> bool:
        """申请并持有 groups；成功 True；超时/中断 False（调用方应 replan 或 abort）。"""
        if not groups:
            return True
        for group in groups:
            if group in self._held_groups and self._is_holder(group):
                continue
            claim_time = _now_time_msg(self.node)
            self._held_groups[group] = claim_time
            self.node.get_logger().info(
                f"[mutex] {self.name} requesting LOCK {group}"
            )
            deadline = time.monotonic() + self._MUTEX_WAIT_TIMEOUT_WALL_S
            last_hb = 0.0
            while not self._quit_path_event.is_set():
                now = time.monotonic()
                if now - last_hb >= self._MUTEX_HEARTBEAT_WALL_S:
                    self._publish_mutex(
                        group, MutexGroupRequest.MODE_LOCK, claim_time
                    )
                    last_hb = now
                if self._is_holder(group):
                    self.node.get_logger().info(
                        f"[mutex] {self.name} acquired {group}"
                    )
                    break
                if now >= deadline:
                    self.node.get_logger().warn(
                        f"[mutex] {self.name} timeout waiting for {group}"
                    )
                    self._release_groups({group})
                    return False
                self._quit_path_event.wait(0.1)
            else:
                self._release_groups({group})
                return False

        # 维持已持有锁的心跳
        for group, claim_time in list(self._held_groups.items()):
            self._publish_mutex(group, MutexGroupRequest.MODE_LOCK, claim_time)
        return True

    def _release_groups(self, groups: Set[str]) -> None:
        for group in list(groups):
            claim_time = self._held_groups.pop(group, None)
            if claim_time is None:
                continue
            self._publish_mutex(group, MutexGroupRequest.MODE_RELEASE, claim_time)
            self.node.get_logger().info(f"[mutex] {self.name} RELEASE {group}")

    def _release_all_mutexes(self) -> None:
        self._release_groups(set(self._held_groups.keys()))

    def _release_transient_mutexes(self) -> None:
        """路径段结束：IDLE 时保留全部持有锁并心跳；行进中被中断则全释放。"""
        if self.state == RobotState.IDLE:
            if self._held_groups:
                self._heartbeat_held_mutexes()
            return
        self._release_all_mutexes()

    def _sync_mutexes_for_target(self, waypoint) -> bool:
        needed = self._groups_for_waypoint(waypoint)
        obsolete = set(self._held_groups.keys()) - needed
        # 驶离当前 waypoint 时释放其占用锁
        if self.on_waypoint is not None:
            departing = self._wp_mutex.get(int(self.on_waypoint))
            target_idx = getattr(waypoint, "graph_index", None)
            if departing and target_idx != self.on_waypoint:
                obsolete.add(departing)
        # 驶离当前 lane 时释放（新目标 approach_lanes 不再经过该 lane）
        if self.on_lane is not None:
            departing_lane = self._lane_mutex.get(int(self.on_lane))
            approach = {
                int(x) for x in (getattr(waypoint, "approach_lanes", None) or [])
            }
            if departing_lane and int(self.on_lane) not in approach:
                obsolete.add(departing_lane)
        if obsolete:
            self._release_groups(obsolete)
        return self._acquire_groups(needed)

    def _hold_until_scheduled(self, waypoint) -> None:
        """若 adapter 当前时刻早于 waypoint.time，则原地等待到点（可被 interrupt 打断）。"""
        t = getattr(waypoint, "time", None)
        if t is None:
            return
        while not self._quit_path_event.is_set():
            try:
                remaining = (t - self.adapter.now()).total_seconds()
            except Exception:  # noqa: BLE001 —— 时钟/类型异常时不阻塞，直接放行
                return
            if remaining <= 0.0:
                return
            # 等待期间继续为已持有 mutex 心跳
            now = time.monotonic()
            if not hasattr(self, "_last_mutex_hb_mono"):
                self._last_mutex_hb_mono = 0.0
            if now - self._last_mutex_hb_mono >= self._MUTEX_HEARTBEAT_WALL_S:
                for group, claim_time in list(self._held_groups.items()):
                    self._publish_mutex(
                        group, MutexGroupRequest.MODE_LOCK, claim_time
                    )
                self._last_mutex_hb_mono = now
            self._quit_path_event.wait(min(0.2, remaining))

    def follow_new_path(
        self,
        waypoints,
        next_arrival_estimator,
        path_finished_callback,
    ):
        self.interrupt()
        with self._lock:
            self._follow_path_thread = None
            self._quit_path_event.clear()
            self.clear()
            self.node.get_logger().info(f"[strict] Received new path for {self.name}")
            self.remaining_waypoints = self.filter_waypoints(waypoints)
            assert next_arrival_estimator is not None
            assert path_finished_callback is not None

            def _follow_path():
                target_pose = None
                path_index = 0
                last_progress_dist = None
                last_progress_wall = time.monotonic()
                replan_cooldown_until = 0.0
                try:
                    while self.remaining_waypoints or self.state == RobotState.MOVING:
                        cmd_id = self.current_cmd_id
                        if self._quit_path_event.is_set():
                            self.node.get_logger().info(
                                f"[{self.name}] aborting path request"
                            )
                            return

                        if self.state == RobotState.IDLE or target_pose is None:
                            if self.target_waypoint is not None:
                                self._hold_until_scheduled(self.target_waypoint)
                                if self._quit_path_event.is_set():
                                    self.node.get_logger().info(
                                        f"[{self.name}] aborting path request"
                                    )
                                    return

                            self.target_waypoint = self.remaining_waypoints[0]
                            path_index = self.remaining_waypoints[0].index
                            target_pose = self.target_waypoint.position

                            if not self._sync_mutexes_for_target(self.target_waypoint):
                                if self._quit_path_event.is_set():
                                    return
                                self.node.get_logger().warn(
                                    f"[mutex] {self.name} lock failed, requesting replan"
                                )
                                self.replan()
                                if self._quit_path_event.wait(0.2):
                                    return
                                continue

                            x, y = target_pose[:2]
                            theta = target_pose[2]
                            speed_limit = self.get_speed_limit(self.target_waypoint)
                            response = self.api.navigate(
                                self.name,
                                self.next_cmd_id(),
                                [x, y, theta],
                                self.map_name,
                                speed_limit,
                            )
                            if response:
                                self.remaining_waypoints = self.remaining_waypoints[1:]
                                self.state = RobotState.MOVING
                                last_progress_dist = None
                                last_progress_wall = time.monotonic()
                                replan_cooldown_until = 0.0
                            else:
                                self.node.get_logger().info(
                                    f"Robot {self.name} failed to request navigation to "
                                    f"[{x:.0f}, {y:.0f}, {theta:.0f}]. Retrying..."
                                )
                                self._quit_path_event.wait(0.1)

                        elif self.state == RobotState.MOVING:
                            # 移动中维持 mutex 心跳
                            now_hb = time.monotonic()
                            if not hasattr(self, "_last_mutex_hb_mono"):
                                self._last_mutex_hb_mono = 0.0
                            if (
                                now_hb - self._last_mutex_hb_mono
                                >= self._MUTEX_HEARTBEAT_WALL_S
                            ):
                                for group, claim_time in list(self._held_groups.items()):
                                    self._publish_mutex(
                                        group,
                                        MutexGroupRequest.MODE_LOCK,
                                        claim_time,
                                    )
                                self._last_mutex_hb_mono = now_hb

                            if self.api.requires_replan(self.name):
                                self.replan()
                            if self._quit_path_event.wait(0.1):
                                return
                            trigger_replan = False
                            with self._lock:
                                if self.api.navigation_completed(self.name, cmd_id):
                                    self.node.get_logger().info(
                                        f"Robot [{self.name}] has reached the destination "
                                        f"for cmd_id {cmd_id}"
                                    )
                                    self.state = RobotState.IDLE
                                    graph_index = self.target_waypoint.graph_index
                                    if graph_index is not None:
                                        self.on_waypoint = graph_index
                                        self.last_known_waypoint_index = graph_index
                                    else:
                                        self.on_waypoint = None
                                else:
                                    lane = self.get_current_lane()
                                    if lane is not None:
                                        self.on_waypoint = None
                                        self.on_lane = lane
                                    else:
                                        if (
                                            self.target_waypoint.graph_index is not None
                                            and self.dist(self.position, target_pose) < 0.5
                                        ):
                                            self.on_waypoint = (
                                                self.target_waypoint.graph_index
                                            )
                                        elif (
                                            self.last_known_waypoint_index is not None
                                            and self.dist(
                                                self.position,
                                                self.graph.get_waypoint(
                                                    self.last_known_waypoint_index
                                                ).location,
                                            )
                                            < 0.5
                                        ):
                                            self.on_waypoint = (
                                                self.last_known_waypoint_index
                                            )
                                        else:
                                            self.on_lane = None
                                            self.on_waypoint = None
                                if (
                                    target_pose is not None
                                    and self.state == RobotState.MOVING
                                ):
                                    dist_to_target = float(
                                        self.dist(self.position, target_pose)
                                    )
                                    now_wall = time.monotonic()
                                    if last_progress_dist is None or dist_to_target < (
                                        last_progress_dist - self._STALL_PROGRESS_EPS_M
                                    ):
                                        last_progress_dist = dist_to_target
                                        last_progress_wall = now_wall
                                    else:
                                        stalled_wall = now_wall - last_progress_wall
                                        if (
                                            stalled_wall >= self._STALL_REPLAN_AFTER_WALL_S
                                            and now_wall >= replan_cooldown_until
                                        ):
                                            trigger_replan = True
                                            replan_cooldown_until = (
                                                now_wall
                                                + self._STALL_REPLAN_COOLDOWN_WALL_S
                                            )
                                            last_progress_wall = now_wall
                                            self.node.get_logger().warn(
                                                f"[strict] {self.name} stalled for "
                                                f"{stalled_wall:.1f}s "
                                                f"(dist={dist_to_target:.3f}m), "
                                                f"requesting RMF replan"
                                            )
                                duration = self.api.navigation_remaining_duration(
                                    self.name, cmd_id
                                )
                                if path_index is not None and duration is not None:
                                    next_arrival_estimator(
                                        path_index, timedelta(seconds=duration)
                                    )
                            if trigger_replan:
                                self.replan()
                                if self._quit_path_event.wait(0.1):
                                    return

                    if (not self.remaining_waypoints) and self.state == RobotState.IDLE:
                        path_finished_callback()
                        self.node.get_logger().info(
                            f"Robot {self.name} has successfully navigated "
                            f"along requested path."
                        )
                finally:
                    self._release_transient_mutexes()

            self._follow_path_thread = threading.Thread(target=_follow_path)
            self._follow_path_thread.start()


def _extract_nav_graph_path(argv: list[str]) -> Optional[str]:
    for i, a in enumerate(argv):
        if a in ("-n", "--nav_graph") and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--nav_graph="):
            return a.split("=", 1)[1]
    return None


def main(argv=None) -> None:
    argv = list(sys.argv if argv is None else argv)
    nav_path = _extract_nav_graph_path(argv)
    if nav_path:
        StrictRobotCommandHandle._nav_graph_path = nav_path
        try:
            wp_m, lane_m = _load_mutex_maps(nav_path)
            StrictRobotCommandHandle._wp_mutex = wp_m
            StrictRobotCommandHandle._lane_mutex = lane_m
            print(
                f"[mutex] loaded nav_graph mutex wp={len(wp_m)} lane={len(lane_m)} "
                f"from {nav_path}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[mutex] failed to load mutex maps: {exc}", flush=True)

    fa.RobotCommandHandle = StrictRobotCommandHandle
    sim_env = os.environ.get("RMF_USE_SIM_TIME", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if sim_env and "-sim" not in argv and "--use_sim_time" not in argv:
        if "--ros-args" in argv:
            argv.insert(argv.index("--ros-args"), "-sim")
        else:
            argv.append("-sim")
    fa.main(argv)


if __name__ == "__main__":
    main(sys.argv)
