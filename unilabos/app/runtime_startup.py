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


def build_slave_launch_command(
    *,
    backend: str,
    host_ip: str,
    hostlink_port: int,
    hostlink_enabled: bool,
) -> str:
    """生成在另一台机器上把设备接入本 Host 的 Slave 启动命令。"""

    parts = ["unilab", "--backend", backend, "--is_slave"]
    if hostlink_enabled:
        parts += ["--host_node_ip", host_ip, "--hostlink_port", str(hostlink_port)]
    else:
        parts.append("--disable_hostlink")
    parts += ["-g", "<图文件.json>"]
    return " ".join(parts)


def print_slave_launch_hint() -> None:
    """Host 就绪后告诉用户如何拉起 Slave；Slave 进程和 --role backend 不打印。"""

    from unilabos.config.config import HostLinkConfig

    backend = str(BasicConfig.backend or "hostlink")
    hostlink_enabled = bool(HostLinkConfig.enable)
    host_ip = str(HostLinkConfig.advertise_ip or "").strip()
    hostlink_port = int(HostLinkConfig.port)
    if hostlink_enabled:
        from unilabos.backend.hostlink.ros_assist import detect_local_ip
        from unilabos.backend.hostlink.server import get_hostlink_server

        server = get_hostlink_server()
        if server is not None:
            hostlink_port = int(server.port)
        if not host_ip:
            host_ip = detect_local_ip() or "127.0.0.1"

    command = build_slave_launch_command(
        backend=backend,
        host_ip=host_ip,
        hostlink_port=hostlink_port,
        hostlink_enabled=hostlink_enabled,
    )
    transport = "HostLink TCP" if hostlink_enabled else "ROS 2 DDS 发现"
    print_status(f"Host 已就绪（backend={backend}，Slave 经 {transport} 接入）", "success")
    print_status(f"在其他机器上启动 Slave 接入本 Host：{command}", "info")
    if hostlink_enabled:
        print_status(
            "同一台机器上可把 --host_node_ip 改为 127.0.0.1；"
            "需要加载驱动包时追加 --devices <目录>。",
            "info",
        )
    else:
        print_status(
            "已关闭 HostLink，Slave 需与 Host 处于同一 ROS_DOMAIN_ID 且网络可互相发现。",
            "info",
        )


def _fail_on_port_in_use(exc: OSError) -> None:
    """管理端端口被占用属于用户可修复的启动错误：只打印修改建议，不打印堆栈。"""

    from unilabos.app.supervisor import PORT_IN_USE_EXIT_CODE

    print_status(exc.strerror or str(exc), "error")
    raise SystemExit(PORT_IN_USE_EXIT_CODE) from exc


def _serves_over_control_plane() -> bool:
    """调度权威拉起的 Host 子进程不监听端口：管理 API 由权威经控制 WS 下发、在进程内执行。"""

    from unilabos.app.supervisor import is_host_child

    return is_host_child()


def _ensure_management_port_available() -> None:
    """Host 模式下在拉起设备 runtime 之前先确认管理端端口可用，避免设备起来后再失败。"""

    if not BasicConfig.is_host_mode or _serves_over_control_plane():
        return
    from unilabos.server.api.app import ManagementPortInUseError, ensure_port_available

    try:
        ensure_port_available("0.0.0.0", BasicConfig.port)
    except ManagementPortInUseError as exc:
        _fail_on_port_in_use(exc)


def _start_management_server() -> None:
    from unilabos.server.api.app import ManagementPortInUseError, serve_over_control_plane, start_server

    if _serves_over_control_plane():
        print_status("Host 子进程不监听端口：管理 API 由调度权威经控制面 WS 下发、在本进程内执行", "info")
        serve_over_control_plane()
        return
    try:
        start_server(
            open_browser=not BasicConfig.disable_browser,
            port=BasicConfig.port,
        )
    except ManagementPortInUseError as exc:
        _fail_on_port_in_use(exc)


def _run_management_or_wait(backend_thread: threading.Thread) -> None:
    if not BasicConfig.is_host_mode:
        backend_thread.join()
        return

    print_slave_launch_hint()
    _start_managed_device_processes()
    _start_management_server()


def run_runtime(args: dict[str, Any]) -> None:
    """启动设备 runtime 和 Host 微后端管理 API。"""

    from unilabos.backend import start_backend

    _ensure_management_port_available()

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
        print_slave_launch_hint()
        _start_managed_device_processes()
        threading.Thread(
            target=_start_management_server,
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


__all__ = ["build_slave_launch_command", "print_slave_launch_hint", "run_runtime"]
