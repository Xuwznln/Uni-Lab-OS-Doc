"""Host 执行适配器的 transport 中立基类。

:class:`unilabos.backend.host_services.HostServices` 定义 host_node 的服务动作，
ROS2 :class:`unilabos.backend.ros2.presets.host_node.HostNode` 与 HostLink
:class:`unilabos.backend.hostlink.host_node.HostNode` 分别负责各自 transport
的执行编排。本模块提供两种 transport 共用的设备状态、goal 簿记、桥接器
通知、延迟诊断和 test_mode 行为。微后端通过 ``adapter_registry``
只依赖该公共契约。
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

from unilabos.utils import logger

if TYPE_CHECKING:
    from unilabos.server.backend.execution_queue import QueueItem

#: 与 rclpy ``GoalStatus`` 对齐的数值（基类不依赖 ROS 运行时）。
GOAL_STATUS_UNKNOWN = 0
GOAL_STATUS_EXECUTING = 2


def execution_result_bridges(bridges: Iterable[Any]) -> list[Any]:
    """存在微后端生命周期 owner 时，只把原始执行结果交给该 owner。"""

    values = list(bridges)
    owners = [
        bridge
        for bridge in values
        if bool(getattr(bridge, "owns_job_lifecycle", False))
    ]
    return owners or values


class HostAdapterBase:
    """两种 backend 执行适配器的公共骨架。

    子类必须实现 transport 特定的两个能力：``send_goal`` / ``cancel_job``。
    物料/设备管理下行不在本契约内——它们是模块级函数
    （:mod:`unilabos.backend.hostlink.downlink`），微后端直接调用。
    """

    #: 设备命名空间前缀（ROS2 侧按设备实例覆写为 ``/devices/<id>``）。
    namespace: str = "/devices"

    def __init__(self, bridges: Optional[List[Any]] = None) -> None:
        self.bridges: List[Any] = list(bridges or [])
        # 服务端最近一次任务下发时间戳（test_latency 用）
        self.server_latest_timestamp: float = 0.0
        # 设备面簿记（capabilities 快照与微后端调度以此为准）
        self.devices_names: Dict[str, str] = {}
        self.device_machine_names: Dict[str, str] = {}
        self._action_value_mappings: Dict[str, Dict[str, Any]] = {}
        self._online_devices: Set[str] = set()
        # 设备状态簿记
        self.device_status: Dict[str, Dict[str, Any]] = {}
        self.device_status_timestamps: Dict[str, Dict[str, float]] = {}
        self._subscribed_topics: Set[str] = set()
        # ping-pong 簿记（test_latency）
        self._ping_lock = threading.Lock()
        self._ping_responses: Dict[str, Dict[str, Any]] = {}
        # goal / job 状态簿记
        self._goals: Dict[str, Any] = {}
        self._inflight_goal_jobs: Set[str] = set()
        self._canceled_jobs: Set[str] = set()
        self._goal_state_lock = threading.RLock()

    # ------------------------------------------------------------------
    # 日志接口
    # ------------------------------------------------------------------

    def lab_logger(self):
        return logger

    # ------------------------------------------------------------------
    # transport 契约（子类实现）
    # ------------------------------------------------------------------

    def send_goal(
        self,
        item: "QueueItem",
        action_type: str,
        action_kwargs: Dict[str, Any],
        sample_material: Dict[str, Any],
        server_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        raise NotImplementedError

    def cancel_job(self, job_id: str) -> bool:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 共享默认实现
    # ------------------------------------------------------------------

    def cancel_goal(self, goal_uuid: str) -> bool:
        """使用 goal 命名调用 ``cancel_job``。"""

        return self.cancel_job(goal_uuid)

    def get_goal_status(self, job_id: str) -> int:
        """未持有 goal 句柄的默认口径：in-flight 视为执行中。"""

        if job_id in self._inflight_goal_jobs:
            return GOAL_STATUS_EXECUTING
        return GOAL_STATUS_UNKNOWN

    # ------------------------------------------------------------------
    # 桥接器通知（两种 backend 共用）
    # ------------------------------------------------------------------

    def notify_ready(self) -> None:
        """告知所有桥接器 Host 可用。"""
        for bridge in self.bridges:
            method_names = (
                ("publish_host_ready",)
                if callable(getattr(bridge, "publish_host_ready", None))
                else (
                    "report_action_error_decisions",
                    "report_all_action_locks",
                )
            )
            for method_name in method_names:
                method = getattr(bridge, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:  # noqa: BLE001 - bridge may be offline
                        logger.exception("[Host Node] bridge %s 初始化通知失败", method_name)

    def _notify_capabilities_changed(self) -> None:
        """设备/动作能力集变化：通知桥接器刷新 runtime.v1 endpoint 能力快照。"""
        for bridge in self.bridges:
            callback = getattr(bridge, "publish_capabilities_changed", None)
            if callable(callback):
                try:
                    callback()
                except Exception:  # noqa: BLE001 - 能力快照失败不影响设备发现
                    self.lab_logger().debug("capabilities 快照通知失败", exc_info=True)

    def _report_action_locks_free(self, action_pairs: List[Tuple[str, str]]) -> None:
        """向所有桥接器主动上报新发现 action 的锁状态为 free。

        _execute_driver_command[_async] 是通用驱动命令入口，并非具体业务动作，
        不作为锁上报（与 WebSocketClient.report_all_action_locks 的过滤保持一致）。
        """
        if not action_pairs:
            return
        locks = [
            {"device_id": dev, "action_name": act, "free": True}
            for dev, act in action_pairs
            if not act.startswith("_execute_driver_command")
        ]
        if not locks:
            return
        for bridge in self.bridges:
            if hasattr(bridge, "publish_action_locks"):
                try:
                    bridge.publish_action_locks(locks)
                except Exception as e:
                    self.lab_logger().warning(f"[Host Node] publish_action_locks failed: {e}")

    def _publish_terminal_result(
        self,
        item: "QueueItem",
        status: str,
        return_info: Dict[str, Any],
        result_data: Dict[str, Any],
    ) -> None:
        """清理 goal 簿记，并把原始终态交给微后端（两种 backend 共用）。"""
        self._goals.pop(item.job_id, None)
        self._inflight_goal_jobs.discard(item.job_id)
        with self._goal_state_lock:
            self._canceled_jobs.discard(item.job_id)
        for bridge in execution_result_bridges(self.bridges):
            publish_status = getattr(bridge, "publish_job_status", None)
            if callable(publish_status):
                publish_status(result_data, item, status, return_info)

    def handle_pong_response(self, pong_data: Dict[str, Any]) -> None:
        """处理服务端 pong 响应（test_latency 的对端半程）。"""

        ping_id = pong_data.get("ping_id")
        if not ping_id:
            self.lab_logger().warning("⚠️ 收到无效的Pong响应（缺少ping_id）")
            return
        with self._ping_lock:
            self._ping_responses[str(ping_id)] = dict(pong_data)

        client_timestamp = pong_data.get("client_timestamp", 0)
        server_timestamp = pong_data.get("server_timestamp", 0)
        self.lab_logger().debug(
            f"📨 Pong | ID:{str(ping_id)[:8]}.. | C→S→C: "
            f"{client_timestamp:.3f}→{server_timestamp:.3f}→{time.time():.3f}"
        )

    def test_latency(self) -> Dict[str, Any]:
        """网络延迟测试：5 次 ping-pong 校对时间误差并计算实际任务延迟。

        实现 backend 无关（ping 经云端桥接 client 发送、pong 回到
        ``handle_pong_response`` 簿记），两种适配器均可执行。
        """

        import uuid as uuid_module

        log = self.lab_logger()
        log.info("=" * 60)
        log.info("开始网络延迟测试...")

        task_start_time = time.time()
        ping_results = []

        for i in range(5):
            log.info(f"第{i + 1}/5次ping-pong测试...")
            ping_id = str(uuid_module.uuid4())
            send_timestamp = time.time()

            from unilabos.server.backend.legacy_adaptor.session import get_backend_client

            comm_client = get_backend_client()
            comm_client.send_ping(ping_id, send_timestamp)

            timeout = 10.0
            start_wait_time = time.time()
            while time.time() - start_wait_time < timeout:
                with self._ping_lock:
                    if ping_id in self._ping_responses:
                        pong_data = self._ping_responses.pop(ping_id)
                        break
                time.sleep(0.001)
            else:
                log.error(f"❌ 第{i + 1}次测试超时")
                continue

            receive_timestamp = time.time()
            server_timestamp = pong_data["server_timestamp"]
            rtt_ms = (receive_timestamp - send_timestamp) * 1000
            # 客户端与服务端时间差：假设网络延迟对称，取中间点的服务端时间
            mid_point_time = send_timestamp + (receive_timestamp - send_timestamp) / 2
            time_diff_ms = (mid_point_time - server_timestamp) * 1000
            ping_results.append({"rtt_ms": rtt_ms, "time_diff_ms": time_diff_ms})
            log.info(f"✅ 第{i + 1}次: 往返时间={rtt_ms:.2f}ms, 时间差={time_diff_ms:.2f}ms")
            time.sleep(0.1)

        if not ping_results:
            log.error("❌ 所有ping-pong测试都失败了")
            return {
                "avg_rtt_ms": -1.0,
                "avg_time_diff_ms": -1.0,
                "max_time_error_ms": -1.0,
                "task_delay_ms": -1.0,
                "raw_delay_ms": -1.0,
                "test_count": 0,
                "status": "all_timeout",
            }

        rtts = [r["rtt_ms"] for r in ping_results]
        time_diffs = [r["time_diff_ms"] for r in ping_results]
        avg_rtt_ms = sum(rtts) / len(rtts)
        avg_time_diff_ms = sum(time_diffs) / len(time_diffs)
        max_time_diff_error_ms: float = max(abs(min(time_diffs)), abs(max(time_diffs)))

        log.info("-" * 50)
        log.info("[测试统计]")
        log.info(f"有效测试次数: {len(ping_results)}/5")
        log.info(f"平均往返时间: {avg_rtt_ms:.2f}ms")
        log.info(f"平均时间差: {avg_time_diff_ms:.2f}ms")
        log.info(f"时间差范围: {min(time_diffs):.2f}ms ~ {max(time_diffs):.2f}ms")
        log.info(f"最大时间误差: ±{max_time_diff_error_ms:.2f}ms")

        if self.server_latest_timestamp > 0:
            log.info("-" * 50)
            log.info("[任务执行延迟分析]")
            log.info(f"服务端任务下发时间: {self.server_latest_timestamp:.6f}")
            log.info(f"客户端任务开始时间: {task_start_time:.6f}")
            raw_delay_ms = (task_start_time - self.server_latest_timestamp) * 1000
            corrected_delay_ms = raw_delay_ms - avg_time_diff_ms
            log.info(f"📊 原始时间差: {raw_delay_ms:.2f}ms")
            log.info(f"🔧 时间同步校正: {avg_time_diff_ms:.2f}ms")
            log.info(f"⏰ 实际任务延迟: {corrected_delay_ms:.2f}ms")
            log.info(f"📏 误差范围: ±{max_time_diff_error_ms:.2f}ms")
            min_delay = corrected_delay_ms - max_time_diff_error_ms
            max_delay = corrected_delay_ms + max_time_diff_error_ms
            log.info(f"📋 延迟范围: {min_delay:.2f}ms ~ {max_delay:.2f}ms")
        else:
            log.warning("⚠️ 无法获取服务端任务下发时间，跳过任务延迟分析")
            raw_delay_ms = -1
            corrected_delay_ms = -1

        log.info("=" * 60)
        return {
            "avg_rtt_ms": avg_rtt_ms,
            "avg_time_diff_ms": avg_time_diff_ms,
            "max_time_error_ms": max_time_diff_error_ms,
            "task_delay_ms": corrected_delay_ms if corrected_delay_ms > 0 else -1,
            "raw_delay_ms": raw_delay_ms if self.server_latest_timestamp > 0 else -1,
            "test_count": len(ping_results),
            "status": "success",
        }

    def _build_test_mode_return(
        self, device_id: str, action_name: str, action_kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """按注册表 handles 的 output 定义构建 test_mode 模拟返回值。

        data_key 中每层 ``@flatten`` 对应一层嵌套数组，叶子为空字典。
        例如 "vessel" → {}，"plate.@flatten" → [{}]，"a.@flatten.@flatten" → [[{}]]。
        """

        mock_return: Dict[str, Any] = {"test_mode": True, "action_name": action_name}
        action_mapping = self._action_value_mappings.get(device_id, {}).get(action_name, {})
        handles = action_mapping.get("handles", {}) if isinstance(action_mapping, dict) else {}
        if isinstance(handles, dict):
            for output_handle in handles.get("output", []):
                data_key = str(output_handle.get("data_key") or "")
                handler_key = str(output_handle.get("handler_key") or "")
                if not handler_key:
                    continue
                value: Any = {}
                for _ in range(data_key.count("@flatten")):
                    value = [value]
                mock_return[handler_key] = value
        return mock_return


__all__ = [
    "GOAL_STATUS_EXECUTING",
    "GOAL_STATUS_UNKNOWN",
    "HostAdapterBase",
    "execution_result_bridges",
]
