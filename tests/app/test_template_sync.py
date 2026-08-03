"""Edge Registry 到正式后端模板协议的契约测试。"""

from __future__ import annotations

import gzip
import json

import pytest

from unilabos.app.main import parse_args
from unilabos.app.register import register_devices_and_resources
from unilabos.app.template_sync import (
    DEVELOPER_TOKEN_ENV,
    TemplateSyncError,
    TemplateSynchronizer,
    run_template_sync_command,
)


class FakeRegistry:
    def obtain_registry_device_info(self):
        return [
            {
                "id": "pump",
                "displayname": "注射泵",
                "registry_type": "device",
                "file_path": "/private/pump.py",
                "class": {
                    "module": "drivers.pump:Pump",
                    "type": "python",
                    "status_types": {"status": "String"},
                    "action_value_mappings": {
                        "transfer": {
                            "displayname": "输送",
                            "type": "UniLabJsonCommand",
                            "goal": {
                                "unilabos_device_id": "unilabos_device_id",
                                "volume": "volume",
                            },
                            "goal_default": {
                                "unilabos_device_id": "",
                                "volume": 1.0,
                            },
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "goal": {
                                        "type": "object",
                                        "properties": {
                                            "unilabos_device_id": {
                                                "type": "string",
                                                "default": "",
                                            },
                                            "volume": {"type": "number"},
                                        },
                                        "required": ["unilabos_device_id", "volume"],
                                    }
                                },
                            },
                            "handles": {
                                "input": [
                                    {
                                        "handler_key": "volume",
                                        "label": "体积",
                                        "data_type": "number",
                                        "data_source": "param",
                                        "data_key": "volume",
                                        "io_type": "target",
                                    }
                                ],
                                "output": [],
                            },
                        }
                    },
                },
                "handles": [],
                "category": ["pump"],
                "init_param_schema": {
                    "config": {
                        "type": "object",
                        "properties": {"port": {"type": "string"}},
                    }
                },
            }
        ]

    def obtain_registry_resource_info(self):
        return [
            {
                "id": "tube_15ml",
                "displayname": "15 mL 离心管",
                "registry_type": "resource",
                "class": {
                    "module": "resources.tube:Tube15mL",
                    "type": "pylabrobot",
                },
                "handles": [],
                "category": ["container"],
            }
        ]


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {
            "code": 0,
            "data": {
                "templates": [
                    {"uuid": "device-template-uuid", "name": "pump"},
                    {"uuid": "resource-template-uuid", "name": "tube_15ml"},
                ]
            },
        }
        self.text = json.dumps(self._payload, ensure_ascii=False)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_sync_merges_device_and_resource_templates_into_one_transaction():
    session = FakeSession()
    synchronizer = TemplateSynchronizer(
        "http://backend:8080",
        "developer-secret",
        session=session,
    )

    report = synchronizer.sync(FakeRegistry())

    assert report.device_count == 1
    assert report.resource_count == 1
    assert report.template_uuids == {
        "pump": "device-template-uuid",
        "tube_15ml": "resource-template-uuid",
    }
    assert len(session.calls) == 1
    url, request = session.calls[0]
    assert url == "http://backend:8080/api/v1/resource-templates"
    assert request["headers"]["Authorization"] == "Bearer developer-secret"
    assert request["headers"]["Content-Encoding"] == "gzip"
    payload = json.loads(gzip.decompress(request["data"]))
    assert [resource["id"] for resource in payload["resources"]] == [
        "pump",
        "tube_15ml",
    ]
    device, resource = payload["resources"]
    assert device["display_name"] == "注射泵"
    assert device["class"]["action_value_mappings"]["transfer"]["display_name"] == "输送"
    action = device["class"]["action_value_mappings"]["transfer"]
    assert "unilabos_device_id" not in action["goal"]
    assert "unilabos_device_id" not in action["goal_default"]
    assert "unilabos_device_id" not in action["schema"]["properties"]["goal"]["properties"]
    assert action["schema"]["properties"]["goal"]["required"] == ["volume"]
    assert device["init_param_schema"] == {
        "config": {"properties": {"port": {"type": "string"}}}
    }
    assert "file_path" not in device
    assert "status_types" not in device["class"]
    assert resource["display_name"] == "15 mL 离心管"
    assert resource["registry_type"] == "resource"


def test_sync_rejects_backend_business_error():
    session = FakeSession(
        FakeResponse(
            payload={
                "code": 5003,
                "error": {"msg": "template definition invalid"},
            }
        )
    )
    synchronizer = TemplateSynchronizer(
        "http://backend:8080/api/v1",
        "developer-secret",
        session=session,
    )

    with pytest.raises(TemplateSyncError, match="5003"):
        synchronizer.sync(FakeRegistry())


def test_template_sync_command_builds_complete_registry_without_starting_edge():
    parsed = vars(
        parse_args().parse_args(
            [
                "--addr",
                "http://backend:8080/api/v1",
                "--registry_path",
                "/registry-a",
                "--devices",
                "/drivers-a",
                "--skip_env_check",
                "template-sync",
            ]
        )
    )
    builder_calls = []

    def registry_builder(**kwargs):
        builder_calls.append(kwargs)
        return FakeRegistry()

    session = FakeSession()
    report = run_template_sync_command(
        parsed,
        backend_address=parsed["addr"],
        environment={DEVELOPER_TOKEN_ENV: "developer-secret"},
        registry_builder=registry_builder,
        session=session,
    )

    assert report.device_count == 1
    assert builder_calls == [
        {
            "registry_paths": ["/registry-a"],
            "devices_dirs": ["/drivers-a"],
            "upload_registry": False,
            "complete_registry": False,
            "external_only": False,
        }
    ]


def test_legacy_startup_registration_is_read_only():
    with pytest.raises(RuntimeError, match="template-sync"):
        register_devices_and_resources(FakeRegistry())
