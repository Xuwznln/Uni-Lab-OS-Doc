"""设备 backend、管理端 Web 与可视化的统一运行入口。"""

from __future__ import annotations

import threading
from typing import Any

from unilabos.config.config import BasicConfig
from unilabos.utils.banner_print import print_status


def _start_managed_device_processes() -> None:
    """Host 就绪后拉起台账里 auto_start 的受管 Slave 子进程（崩溃由服务看护重启）。"""
    try:
        from unilabos.server.services.device_processes import get_device_process_service

        started = get_device_process_service().start_auto()
        if started:
            print_status(f"已拉起 {len(started)} 个受管设备进程", "info")
    except Exception as exc:  # noqa: BLE001 - 受管进程失败不影响 Host 本体
        print_status(f"受管设备进程启动失败: {exc}", "warning")


def _run_management_or_wait(backend_thread: threading.Thread) -> None:
    if not BasicConfig.is_host_mode:
        backend_thread.join()
        return

    from unilabos.server.api.app import start_server

    _start_managed_device_processes()
    start_server(
        open_browser=not BasicConfig.disable_browser,
        port=BasicConfig.port,
    )


def run_runtime(args: dict[str, Any]) -> None:
    """启动设备 runtime 和 Host 微后端管理 API。"""

    from unilabos.backend import start_backend

    if args["visual"] == "disable":
        _run_management_or_wait(start_backend(**args))
        return

    from unilabos.resources.graphio import dict_from_graph

    devices_and_resources = dict_from_graph(args["graph"])
    if devices_and_resources is None:
        _run_management_or_wait(start_backend(**args))
        return

    from unilabos.device_mesh.resource_visalization import ResourceVisualization

    visualization = ResourceVisualization(
        devices_and_resources,
        [node.res_content for node in args["resources_config"].all_nodes],
        enable_rviz=args["visual"] == "rviz",
    )
    args["resources_mesh_config"] = visualization.resource_model
    backend_thread = start_backend(**args)

    if BasicConfig.is_host_mode:
        from unilabos.server.api.app import start_server

        _start_managed_device_processes()
        threading.Thread(
            target=start_server,
            kwargs={
                "open_browser": not BasicConfig.disable_browser,
                "port": BasicConfig.port,
            },
            daemon=True,
            name="UniLabManagementAPI",
        ).start()

    try:
        visualization.start()
    except OSError as exc:
        if "AMENT_PREFIX_PATH" not in str(exc):
            raise
        print_status(
            f"ROS 2环境未正确设置，跳过3D可视化启动。错误详情: {exc}",
            "warning",
        )
        print_status(
            "建议激活 ROS 2 环境，或使用 --backend hostlink / --visual disable",
            "info",
        )

    backend_thread.join()


__all__ = ["run_runtime"]
