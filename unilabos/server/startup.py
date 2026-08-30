"""微后端服务的启动组合。

应用入口只声明运行环境；四库路径、物料权威和执行服务的装配都在这里完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from unilabos.config.config import BasicConfig, HTTPConfig
from unilabos.server.database import ServerDatabasePaths


@dataclass(frozen=True)
class HostServerStack:
    database_paths: ServerDatabasePaths
    materials_gateway: Any
    execution_backend: Any
    material_authority: str
    template_count: int
    host_network: Any = None


def resolve_database_paths(
    args: Mapping[str, Any], *, working_dir: str | Path
) -> ServerDatabasePaths:
    """解析五库路径并一次性绑定到进程配置。"""

    root = str(
        args.get("server_database_root")
        or (Path(working_dir).expanduser() / ".unilabos")
    )
    overrides = {
        key: value
        for key, value in {
            "runtime": args.get("runtime_db"),
            "materials": args.get("materials_db"),
            "telemetry": args.get("telemetry_db"),
            "history": args.get("history_db"),
            "workflow": args.get("workflow_db"),
        }.items()
        if value is not None and str(value).strip()
    }
    paths = ServerDatabasePaths.resolve(root, overrides)
    BasicConfig.server_database_paths = paths
    return paths


def setup_host_server_stack(
    *,
    args: Mapping[str, Any],
    working_dir: str | Path,
    registry: Any,
    communication_client: Any,
) -> HostServerStack:
    """装配 Host 唯一的微后端权威及其控制链路。"""

    from unilabos.resources.adapters.registry_materials import sync_registry_resources
    from unilabos.client.materials import (
        HTTPMaterialsClient,
        LocalMaterialsClient,
    )
    from unilabos.server.backend.composition import (
        set_materials_gateway,
        shutdown_backend_services,
        setup_execution_backend,
        setup_materials_service,
    )

    paths = resolve_database_paths(args, working_dir=working_dir)
    try:
        address_arg = args.get("material_microbackend_addr")
        if address_arg is not None:
            HTTPConfig.material_microbackend_addr = str(address_arg).strip()
        external_address = str(HTTPConfig.material_microbackend_addr or "").strip()

        if external_address:
            materials_gateway = HTTPMaterialsClient(external_address)
            material_authority = external_address
        else:
            materials_service = setup_materials_service(
                database_paths=paths,
            )
            materials_gateway = LocalMaterialsClient(materials_service)
            material_authority = str(paths.materials_db)

        template_report = sync_registry_resources(registry, materials_gateway)
        set_materials_gateway(materials_gateway)

        execution_backend = setup_execution_backend(
            control_client=communication_client,
            database_paths=paths,
            materials_gateway=materials_gateway,
        )
        # 调度权威归属：未显式配置云端地址时本机是默认权威（本地 Scheduler +
        # Workflow API）；配置后调度在远端 Backend，本机纯执行。
        from unilabos.server.backend.url import build_backend_websocket_url

        if not build_backend_websocket_url():
            from unilabos.server.backend.composition import (
                setup_local_scheduler,
            )

            setup_local_scheduler(
                database_path=paths.workflow_db,
                backend=execution_backend,
            )

        host_network = None
        if args.get("backend") == "ros2":
            from unilabos.backend.hostlink.network import setup_host_network_service

            host_network = setup_host_network_service(
                material_gateway=materials_gateway
            )

        return HostServerStack(
            database_paths=paths,
            materials_gateway=materials_gateway,
            execution_backend=execution_backend,
            material_authority=material_authority,
            template_count=template_report.resource_count,
            host_network=host_network,
        )
    except BaseException:
        shutdown_backend_services()
        raise


__all__ = [
    "HostServerStack",
    "resolve_database_paths",
    "setup_host_server_stack",
]
