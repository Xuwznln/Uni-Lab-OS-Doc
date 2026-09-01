"""验证四库组合根及每个物理数据库的共享 writer。"""

from __future__ import annotations

import pytest

from unilabos.server.composition import (
    configure_server_services,
    get_server_services,
    shutdown_server_services,
)
from unilabos.server.database import ServerDatabasePaths
from unilabos.protocol.history import HistoryEventQuery
from unilabos.config.config import BasicConfig
from unilabos.server.backend.composition import (
    get_execution_backend,
    get_materials_service,
    reset_for_test,
    setup_execution_backend,
    setup_materials_service,
)
from unilabos.server.services import (
    HistoryService,
    MaterialsService,
    RuntimeService,
    TelemetryService,
)


def test_server_services_open_exactly_four_databases(tmp_path) -> None:
    paths = ServerDatabasePaths.resolve(tmp_path)
    try:
        services = configure_server_services(paths)

        assert get_server_services() is services
        assert services.runtime.connection is not services.materials.connection
        assert services.materials.connection is not services.telemetry.connection
        assert services.telemetry.connection is not services.history.connection
        # GraphService 与 MaterialsService 共享 materials.db 的单写连接。
        assert services.graph.connection is services.materials.connection
        assert isinstance(services.runtime, RuntimeService)
        assert isinstance(services.materials, MaterialsService)
        assert isinstance(services.telemetry, TelemetryService)
        assert isinstance(services.history, HistoryService)
        # workflow 与 registry 表使用 runtime.db，不产生额外数据库文件。
        assert {path.name for path in tmp_path.glob("*.db")} == {
            "runtime.db",
            "materials.db",
            "telemetry.db",
            "history.db",
        }
    finally:
        shutdown_server_services()


def test_server_services_reject_runtime_layout_switch(tmp_path) -> None:
    first = ServerDatabasePaths.resolve(tmp_path / "first")
    second = ServerDatabasePaths.resolve(tmp_path / "second")
    try:
        configure_server_services(first)
        with pytest.raises(RuntimeError, match="another database layout"):
            configure_server_services(second)
    finally:
        shutdown_server_services()


def test_local_scheduler_and_registry_share_runtime_database(
    tmp_path, monkeypatch
) -> None:
    """Workflow 与 Registry 服务共享 runtime.db 的连接和写锁。"""

    from unilabos.server.backend.composition import (
        get_workflow_service,
        setup_local_scheduler,
    )
    from unilabos.server.services.runtime.registry import RegistryService

    paths = ServerDatabasePaths.resolve(tmp_path)
    monkeypatch.setattr(BasicConfig, "backend", "hostlink")
    monkeypatch.setattr(BasicConfig, "machine_name", "test-host")
    monkeypatch.setattr(BasicConfig, "server_database_paths", paths)
    try:
        backend = setup_execution_backend(database_paths=paths)
        workflow_service = setup_local_scheduler(backend=backend)
        assert get_workflow_service() is workflow_service

        services = get_server_services()
        assert services is not None
        # WorkflowService 使用 RuntimeService 的连接。
        assert workflow_service.connection is services.runtime.connection

        workflow = workflow_service.create_workflow(
            name="共库验证", tags=[], description=None, meta_data={}
        )
        rows = services.runtime.connection.execute(
            "SELECT uuid FROM workflow"
        ).fetchall()
        assert [row[0] for row in rows] == [workflow["uuid"]]

        # RegistryService 使用相同的 runtime 连接与写锁。
        registry = RegistryService(services.runtime)
        report = registry.report(
            [{"id": "pump", "registry_type": "device", "class": {"module": "m"}}]
        )
        assert report["report_id"] == 1
        assert report["summary"]["added"] == ["pump"]
        assert services.runtime.connection.execute(
            "SELECT COUNT(*) FROM registry_entry"
        ).fetchone()[0] == 1

        assert {path.name for path in tmp_path.glob("*.db")} == {
            "runtime.db",
            "materials.db",
            "telemetry.db",
            "history.db",
        }
    finally:
        reset_for_test()


def test_host_startup_uses_four_writers_without_local_scheduler(
    tmp_path, monkeypatch
) -> None:
    paths = ServerDatabasePaths.resolve(tmp_path)
    monkeypatch.setattr(BasicConfig, "backend", "hostlink")
    monkeypatch.setattr(BasicConfig, "machine_name", "test-host")
    monkeypatch.setattr(BasicConfig, "server_database_paths", paths)
    try:
        materials = setup_materials_service(database_paths=paths)
        backend = setup_execution_backend(database_paths=paths)

        assert get_materials_service() is materials
        assert get_execution_backend() is backend
        assert backend.device_state.endpoint_uuid == "hostlink:test-host"
        incident = backend.status_incidents.observe(
            "pump-1",
            "mode",
            "Error",
            {
                "incidents": {
                    "Error": {
                        "code": "pump.mode.error",
                        "hold": True,
                    }
                }
            },
        )
        assert incident is not None
        services = get_server_services()
        assert services is not None
        events = services.history.query_events(
            HistoryEventQuery(
                event_types=["action_availability"],
                device_uuid="pump-1",
            )
        )
        assert len(events) == 1
        assert events[0].event_key == "pump.mode.error"
        assert events[0].payload_uuid is not None
        assert events[0].summary["event"] == "status_incident_required"
        assert {path.name for path in tmp_path.glob("*.db")} == {
            "runtime.db",
            "materials.db",
            "telemetry.db",
            "history.db",
        }
    finally:
        reset_for_test()
