"""UniLabOS startup arguments for the host-owned material service."""

from __future__ import annotations

import pytest

from unilabos.app.main import (
    configure_material_startup,
    parse_args,
    should_attach_legacy_http_bridge,
    should_request_remote_startup,
    should_start_edge_scheduler,
    should_start_embedded_material_service,
)
from unilabos.config.config import HTTPConfig


@pytest.fixture(autouse=True)
def _restore_material_config(monkeypatch):
    monkeypatch.setattr(HTTPConfig, "material_source", "microbackend")
    monkeypatch.setattr(HTTPConfig, "material_microbackend_addr", "")


def test_default_starts_embedded_microbackend_with_host_db() -> None:
    args = vars(parse_args().parse_args([]))

    mode = configure_material_startup(args)

    assert HTTPConfig.material_source == "microbackend"
    assert mode == "embedded"
    assert HTTPConfig.material_microbackend_addr == ""
    assert args["edge_inventory_db"] == "~/.unilabos/inventory.db"
    assert args["edge_scheduler"] is True
    assert args["edge_device_state_db"] == "~/.unilabos/device_state.db"
    assert args["edge_workflow_history_db"] == "~/.unilabos/workflow_history.db"
    assert should_start_embedded_material_service(args, is_host_mode=True)
    assert not should_start_embedded_material_service(args, is_host_mode=False)
    assert should_start_edge_scheduler(args, is_host_mode=True)
    assert not should_start_edge_scheduler(args, is_host_mode=False)


def test_scheduler_can_be_explicitly_disabled() -> None:
    args = vars(parse_args().parse_args(["--no_edge_scheduler"]))

    assert args["edge_scheduler"] is False
    assert not should_start_edge_scheduler(args, is_host_mode=True)


def test_production_edge_control_disables_local_scheduler_and_inventory() -> None:
    args = vars(parse_args().parse_args(["--app_bridges", "edge_control", "fastapi"]))

    mode = configure_material_startup(args)

    assert mode == "embedded"
    assert HTTPConfig.material_source == "backend"
    assert not should_start_embedded_material_service(args, is_host_mode=True)
    assert not should_start_edge_scheduler(args, is_host_mode=True)
    assert not should_attach_legacy_http_bridge(args)


def test_local_graph_does_not_request_legacy_remote_startup() -> None:
    assert not should_request_remote_startup(
        startup_json=None,
        graph_file_path="/config/devices.json",
    )
    assert should_request_remote_startup(
        startup_json=None,
        graph_file_path=None,
    )


def test_scheduler_database_paths_are_configurable() -> None:
    args = vars(
        parse_args().parse_args(
            [
                "--device_state_db",
                "/tmp/device-state.db",
                "--workflow_history_db",
                "/tmp/workflow-history.db",
            ]
        )
    )

    assert args["edge_device_state_db"] == "/tmp/device-state.db"
    assert args["edge_workflow_history_db"] == "/tmp/workflow-history.db"


def test_directed_discovery_ports_are_configurable() -> None:
    args = vars(
        parse_args().parse_args(
            [
                "--hostlink_addr",
                "0.0.0.0:7302",
                "--ros_discovery_port",
                "11811",
                "--ros_discovery_server",
                "192.168.1.20:11811",
            ]
        )
    )

    assert args["hostlink_addr"] == "0.0.0.0:7302"
    assert args["ros_discovery_port"] == 11811
    assert args["ros_discovery_server"] == "192.168.1.20:11811"


def test_startup_arguments_switch_to_formal_backend() -> None:
    args = vars(
        parse_args().parse_args(
            ["--material_source", "backend", "--material_db", "/tmp/material.db"]
        )
    )

    mode = configure_material_startup(args)

    assert HTTPConfig.material_source == "backend"
    assert mode == "embedded"
    assert args["edge_inventory_db"] == "/tmp/material.db"


def test_external_mode_defaults_to_standalone_scheduler_port() -> None:
    args = vars(parse_args().parse_args(["--material_service_mode", "external"]))

    mode = configure_material_startup(args)

    assert mode == "external"
    assert HTTPConfig.material_microbackend_addr == "http://127.0.0.1:8092/api/v1"


def test_explicit_microbackend_address_implies_external_mode() -> None:
    args = vars(
        parse_args().parse_args(
            [
                "--material_microbackend_addr",
                "http://10.0.0.2:8092/api/v1",
            ]
        )
    )

    mode = configure_material_startup(args)

    assert mode == "external"
    assert HTTPConfig.material_microbackend_addr == "http://10.0.0.2:8092/api/v1"
