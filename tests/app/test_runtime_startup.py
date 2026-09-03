"""Host 启动提示与管理端端口预检。"""

from __future__ import annotations

import socket

import pytest

from unilabos.app.cli.parser import build_parser
from unilabos.app.runtime_startup import build_slave_launch_command
from unilabos.server.api.app import ManagementPortInUseError, ensure_port_available


def test_cli_defaults_to_hostlink_backend() -> None:
    args = build_parser().parse_args(["-g", "graph.json"])
    assert args.backend == "hostlink"


def test_slave_launch_command_uses_hostlink_target() -> None:
    command = build_slave_launch_command(
        backend="hostlink",
        host_ip="192.168.1.10",
        hostlink_port=7302,
        hostlink_enabled=True,
    )
    assert command == (
        "unilab --backend hostlink --is_slave --host_node_ip 192.168.1.10 "
        "--hostlink_port 7302 -g <图文件.json>"
    )


def test_slave_launch_command_without_hostlink_falls_back_to_dds() -> None:
    command = build_slave_launch_command(
        backend="ros2",
        host_ip="192.168.1.10",
        hostlink_port=7302,
        hostlink_enabled=False,
    )
    assert command == "unilab --backend ros2 --is_slave --disable_hostlink -g <图文件.json>"


def test_port_check_reports_actionable_hint_when_in_use() -> None:
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        with pytest.raises(ManagementPortInUseError) as excinfo:
            ensure_port_available("127.0.0.1", port)
    finally:
        holder.close()
    message = excinfo.value.strerror
    assert str(port) in message
    assert f"--port {port + 1}" in message
    # 端口释放后预检必须放行
    ensure_port_available("127.0.0.1", port)
