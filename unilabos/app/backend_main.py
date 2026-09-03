"""``unilab --role backend``：常驻调度权威进程。

本进程持有 Workflow Authority、``BackendScheduler``、物料服务和 runtime.v1
控制面；运行设备的 Edge 进程通过 ``--address`` 接入。调度状态与设备进程
解耦，因此 Edge 可以独立重启。

数据库使用独立 root（默认 ``<working_dir>/.unilabos/backend``），与 Edge
进程的四库文件完全隔离，避免 SQLite 跨进程并发写。
"""

from __future__ import annotations

import os
from typing import Any, Dict

from unilabos.config.config import HTTPConfig
from unilabos.utils.banner_print import print_status


def run_backend_process(
    args_dict: Dict[str, Any],
    registry: Any,
    working_dir: str,
) -> None:
    """装配并阻塞运行 backend 角色进程；返回即进程退出。"""

    from unilabos.client.materials import LocalMaterialsClient
    from unilabos.resources.adapters.registry_materials import sync_registry_resources
    from unilabos.server.api.app import (
        ManagementPortInUseError,
        setup_server,
        start_server,
    )
    from unilabos.server.api.runtime import install_edge_control_api
    from unilabos.server.backend.composition import (
        set_materials_gateway,
        setup_local_scheduler,
        setup_materials_service,
        shutdown_backend_services,
    )
    from unilabos.server.backend.edge_control import (
        EdgeControlService,
        set_edge_control_service,
    )
    from unilabos.server.services.runtime.registry import get_registry_service
    from unilabos.server.startup import resolve_database_paths

    # backend 角色使用 <root>/backend，避免与 Edge 进程共享 SQLite 文件。
    base_root = str(
        args_dict.get("server_database_root")
        or os.path.join(working_dir, ".unilabos")
    )
    args_dict["server_database_root"] = os.path.join(
        os.path.expanduser(base_root), "backend"
    )
    paths = resolve_database_paths(args_dict, working_dir=working_dir)

    try:
        materials_service = setup_materials_service(database_paths=paths)
        materials_gateway = LocalMaterialsClient(materials_service)
        template_report = sync_registry_resources(registry, materials_gateway)
        set_materials_gateway(materials_gateway)
        print_status(
            f"物料权威已就绪: {paths.materials_db} "
            f"({template_report.resource_count} 个资源模板)",
            "info",
        )

        # 调度权威 + Registry Authority 同一步装配（与默认 Host 同一函数）；
        # Registry Authority 接收 Edge 的全量快照并按条目维护版本。
        edge_control = EdgeControlService(edge_data_addr=HTTPConfig.edge_data_addr)
        set_edge_control_service(edge_control)

        setup_local_scheduler(backend=edge_control)
        registry_service = get_registry_service()
        if registry_service is None:
            raise RuntimeError("registry authority must be ready after local scheduler setup")
        active_entries = len(registry_service.list_entries(status="active"))
        print_status(
            f"注册表权威已就绪: {paths.runtime_db}"
            + (
                f"（{active_entries} 个生效条目）"
                if active_entries
                else "（等待 Edge 首次上报）"
            ),
            "info",
        )
        print_status(
            f"调度权威已就绪: {paths.runtime_db}（executor=runtime.v1 控制面远程下发）",
            "info",
        )

        app = setup_server()
        install_edge_control_api(app)

        port = int(args_dict.get("port_management") or HTTPConfig.backend_port)
        print_status(
            f"Backend 进程已启动: 管理 API/WS 0.0.0.0:{port}，"
            f"Edge 数据面 {HTTPConfig.edge_data_addr}；"
            f"Edge 侧配置 --address http://127.0.0.1:{port} 接入",
            "info",
        )
        try:
            start_server(host="0.0.0.0", port=port, open_browser=False)
        except ManagementPortInUseError as exc:
            print_status(exc.strerror or str(exc), "error")
            raise SystemExit(1) from exc
    finally:
        set_edge_control_service(None)
        # RegistryService 共享 RuntimeService 的连接，由组合根统一关闭。
        shutdown_backend_services()


__all__ = ["run_backend_process"]
