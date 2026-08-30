"""后端受控微后端与 HostLink/ROS2 执行 bridge 的装配层。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from unilabos.config.config import BasicConfig
from unilabos.server.composition import (
    configure_server_services,
    shutdown_server_services,
)
from unilabos.server.database import ServerDatabasePaths
from unilabos.server.backend.execution import (
    JobExecutionBackend,
    make_device_materials_need_lock_resolver,
    make_device_status_policy_resolver,
)
from unilabos.server.backend.coordinator import WorkflowBusinessCoordinator
from unilabos.server.backend.scheduler.service import BackendScheduler
from unilabos.server.backend.telemetry import TelemetryDeviceStateProjection
from unilabos.protocol.common import canonical_json
from unilabos.protocol.history import HistoryEventAppend, InlinePayloadWrite
from unilabos.server.services.history import HistoryService
from unilabos.server.services.materials import MaterialsService

logger = logging.getLogger(__name__)

_backend: Optional[JobExecutionBackend] = None
_coordinator: Optional[WorkflowBusinessCoordinator] = None
_scheduler: Optional[BackendScheduler] = None
_materials: Optional[MaterialsService] = None
_materials_gateway: Any = None
_device_state_projection: Optional[TelemetryDeviceStateProjection] = None
_workflow_service: Any = None


def _status_incident_history_listener(
    history: HistoryService,
    *,
    endpoint_uuid: str,
):
    """把实时状态联锁事实追加到既有 history_event。"""

    def append(event: dict[str, Any]) -> None:
        incident = event.get("incident") or {}
        data = event.get("data") or {}
        event_type = str(event.get("type") or "status_incident")
        updated_at = float(
            incident.get("updated_at")
            or incident.get("created_at")
            or 0
        )
        payload = history.store_payload(
            InlinePayloadWrite(
                media_type="application/json",
                encoding="utf-8",
                inline_payload=canonical_json(event).encode("utf-8"),
            )
        )
        history.append_event(
            HistoryEventAppend(
                event_uuid=str(uuid4()),
                event_type="action_availability",
                endpoint_uuid=endpoint_uuid,
                device_uuid=str(incident.get("device_id") or "") or None,
                event_key=str(incident.get("policy_id") or event_type),
                payload_uuid=payload.payload_uuid,
                summary={
                    "event": event_type,
                    "incident_id": str(incident.get("incident_id") or ""),
                    "property_name": str(
                        incident.get("property_name") or ""
                    ),
                    "state": str(data.get("state") or incident.get("state") or ""),
                    "hold": bool(
                        (incident.get("hold") or {}).get("new_dispatch")
                    ),
                    "selected_action": str(
                        data.get("selected_action")
                        or incident.get("selected_action")
                        or ""
                    ),
                },
                severity=str(incident.get("severity") or "info"),
                actor_type=(
                    "operator"
                    if event_type == "status_incident_resolved"
                    else "device"
                ),
                actor_uuid=(
                    None
                    if event_type == "status_incident_resolved"
                    else str(incident.get("device_id") or "") or None
                ),
                occurred_at_ms=(
                    int(updated_at * 1000) if updated_at > 0 else None
                ),
            )
        )

    return append


def get_scheduler() -> Optional[BackendScheduler]:
    """返回本进程的 Backend Scheduler；接入云端（Backend-controlled）时为 ``None``。"""

    return _scheduler


def get_execution_backend() -> Optional[JobExecutionBackend]:
    return _backend


def get_business_coordinator() -> Optional[WorkflowBusinessCoordinator]:
    return _coordinator


def get_materials_service() -> Optional[MaterialsService]:
    """返回当前进程持有的 MaterialsService。"""

    return _materials


def get_workflow_service() -> Any:
    """返回本机 Workflow Authority；接入云端（Backend-controlled）时为 ``None``。"""

    return _workflow_service


def setup_local_scheduler(
    *,
    database_path: str | Path,
    backend: Any = None,
) -> Any:
    """装配本机默认的 Workflow Authority（``local_scheduler`` profile）。

    未配置云端地址的 Host 默认由本进程 BackendScheduler 持有 WorkflowTask
    权威；接入云端后调度权威在远端 Backend，本函数不再被调用。复用同一
    JobExecutionBackend 下发到 HostLink。
    """

    global _workflow_service, _scheduler
    if _workflow_service is not None:
        return _workflow_service
    execution_backend = backend or _backend
    if execution_backend is None:
        raise RuntimeError("job execution backend must be ready first")

    from unilabos.server.backend.scheduler.authority import SchedulerAuthorityProfile
    from unilabos.server.services.workflow.service import WorkflowService
    from unilabos.server.database.repositories.workflow import WorkflowStore

    service = WorkflowService(
        WorkflowStore(database_path),
        authority_profile=SchedulerAuthorityProfile.LOCAL_SCHEDULER,
    )
    scheduler = BackendScheduler(
        service,
        execution_backend,
        materials_gateway=_materials_gateway,
        materials_need_lock_resolver=(
            execution_backend.resolve_material_lock_parameters
        ),
    )
    service.set_task_submitter(scheduler.submit)
    scheduler.start(recover=True)
    _workflow_service = service
    _scheduler = scheduler
    logger.info(
        "[WorkflowIntegration] local Workflow Authority ready (%s)",
        database_path,
    )
    return service


def setup_materials_service(
    *,
    database_paths: Optional[ServerDatabasePaths] = None,
) -> MaterialsService:
    """从统一 ServerServices 装配 materials writer。"""

    global _materials
    if _materials is not None:
        return _materials

    paths = database_paths or BasicConfig.server_database_paths
    if not isinstance(paths, ServerDatabasePaths):
        raise RuntimeError("ServerDatabasePaths must be configured before startup")
    _materials = configure_server_services(paths).materials

    logger.info("[MaterialsIntegration] materials.v1 writer ready")
    return _materials


def set_materials_gateway(gateway: Any) -> None:
    """Publish the Host-selected embedded/external materials authority."""

    global _materials_gateway
    _materials_gateway = gateway


def get_materials_gateway() -> Any:
    return _materials_gateway


def setup_execution_backend(
    control_client: Any = None,
    *,
    host_node_getter: Any = None,
    database_paths: Optional[ServerDatabasePaths] = None,
    materials_gateway: Any = None,
) -> JobExecutionBackend:
    """启动只消费后端命令的微后端，不创建本地 DAG 或旧 Store。"""

    global _backend, _coordinator, _device_state_projection
    if _backend is not None:
        return _backend

    paths = database_paths or BasicConfig.server_database_paths
    if not isinstance(paths, ServerDatabasePaths):
        raise RuntimeError("ServerDatabasePaths must be configured before startup")
    services = configure_server_services(paths)
    endpoint_uuid = ":".join(
        (
            BasicConfig.backend,
            BasicConfig.machine_name or BasicConfig.host_node_name or "host",
        )
    )
    _device_state_projection = TelemetryDeviceStateProjection(
        services.telemetry,
        endpoint_uuid=endpoint_uuid,
    )

    from unilabos.server.backend.incidents import StatusIncidentManager

    status_incidents = StatusIncidentManager()
    status_incidents.add_listener(
        _status_incident_history_listener(
            services.history,
            endpoint_uuid=endpoint_uuid,
        )
    )
    backend = JobExecutionBackend(
        host_node_getter=host_node_getter,
        device_state_store=_device_state_projection,
        status_policy_resolver=make_device_status_policy_resolver(host_node_getter),
        status_incidents=status_incidents,
        result_bridges=[],
        materials_need_lock_resolver=make_device_materials_need_lock_resolver(
            host_node_getter
        ),
        materials_gateway=(
            materials_gateway
            if materials_gateway is not None
            else _materials_gateway
        ),
    )
    coordinator = WorkflowBusinessCoordinator(
        services.runtime,
        services.history,
        backend,
        endpoint_uuid=endpoint_uuid,
        transport=BasicConfig.backend,
        host_uuid=BasicConfig.machine_name or BasicConfig.host_node_name or "host",
        instance_name=BasicConfig.host_node_name or "host",
        notice_callback=(
            getattr(control_client, "publish_runtime_events", None)
            if control_client is not None
            else None
        ),
    )
    backend.result_bridges.append(coordinator)
    _coordinator = coordinator
    backend.start()
    backend.rebuild_status_incidents()
    coordinator.restore()
    _backend = backend
    logger.info(
        "[JobExecutionIntegration] backend-controlled microbackend ready (%s)",
        endpoint_uuid,
    )
    return backend


def shutdown_backend_services() -> None:
    """关闭执行 bridge 和四库组合根。"""

    global _backend, _coordinator, _materials, _materials_gateway
    global _device_state_projection, _workflow_service, _scheduler

    if BasicConfig.backend == "ros2":
        from unilabos.backend.hostlink.network import shutdown_network_services

        shutdown_network_services()
    if _scheduler is not None:
        _scheduler.stop()
    if _workflow_service is not None:
        _workflow_service.set_task_submitter(None)
        _workflow_service.close()
    if _backend is not None:
        _backend.stop()
    shutdown_server_services()

    _backend = None
    _coordinator = None
    _materials = None
    _materials_gateway = None
    _device_state_projection = None
    _workflow_service = None
    _scheduler = None


def reset_for_test() -> None:
    shutdown_backend_services()


__all__ = [
    "get_execution_backend",
    "get_business_coordinator",
    "get_scheduler",
    "get_materials_service",
    "get_materials_gateway",
    "get_workflow_service",
    "reset_for_test",
    "setup_execution_backend",
    "setup_local_scheduler",
    "setup_materials_service",
    "set_materials_gateway",
    "shutdown_backend_services",
]
