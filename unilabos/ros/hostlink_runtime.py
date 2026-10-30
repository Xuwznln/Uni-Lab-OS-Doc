"""ROS 运行时与工站 HostLink 网络所有权之间的轻量组合接缝。"""

from __future__ import annotations

from typing import Any

from unilabos.config.config import BasicConfig


def attach_hostlink_runtime(host_node: Any) -> None:
    """仅本地单进程模式把实时资源树挂接到调度微后端。

    参数：``host_node`` 是已经构造的 ROS HostNode。返回无。异常：正式 Backend
    或双进程角色直接返回；本地微后端装配失败原样传播。
    """

    if BasicConfig.control_plane != "local" or BasicConfig.process_role != "combined":
        return
    from unilabos.app.scheduler.host_network import setup_host_network_service

    setup_host_network_service(lambda: host_node.resources_config)


def setup_host_network_before_ros() -> None:
    """仅本地单进程模式在 ``rclpy.init`` 前启动 HostLink 微后端。

    参数与返回值为空。异常：正式 Backend 或双进程角色直接返回；本地网络装配
    失败原样传播。模块本身不导入 ROS2，便于控制面独立验证。
    """

    if BasicConfig.control_plane != "local" or BasicConfig.process_role != "combined":
        return
    from unilabos.app.scheduler.host_network import setup_host_network_service

    setup_host_network_service()


def start_hostlink_server(host_node: Any) -> None:
    """兼容旧调用名并转发到唯一 HostLink 挂接入口。"""

    attach_hostlink_runtime(host_node)


__all__ = [
    "attach_hostlink_runtime",
    "setup_host_network_before_ros",
    "start_hostlink_server",
]
