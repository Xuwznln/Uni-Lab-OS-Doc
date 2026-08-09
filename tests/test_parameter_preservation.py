import pytest

from unilabos.devices.virtual.fault_injection_device import FaultInjectionDevice
from unilabos.registry.decorators import get_action_error_policy
from unilabos.utils.exception import SensorError, apply_builtin_error_policy_to_alarm


def test_parameter_input_node_emits_all_parameter_types_unchanged():
    device = FaultInjectionDevice()

    result = device.test_parameter_input_node(
        text_value="upstream-text",
        integer_value=42,
        float_value=3.125,
        boolean_value=True,
        list_value=["alpha", "beta"],
        object_value={
            "batch": "YB-007",
            "priority": 3,
            "enabled": False,
            "thresholds": [0.1, 0.2],
        },
    )

    assert result == {
        "success": True,
        "parameters": {
            "text_value": "upstream-text",
            "integer_value": 42,
            "float_value": 3.125,
            "boolean_value": True,
            "list_value": ["alpha", "beta"],
            "object_value": {
                "batch": "YB-007",
                "priority": 3,
                "enabled": False,
                "thresholds": [0.1, 0.2],
            },
        },
        "message": "parameter bundle emitted",
    }


def test_parameter_receive_node_preserves_upstream_and_local_parameters(monkeypatch):
    monkeypatch.setattr(
        "unilabos.devices.virtual.fault_injection_device.np.random.rand",
        lambda: 0.0,
    )
    device = FaultInjectionDevice()
    source_result = device.test_parameter_input_node(
        text_value="upstream-text",
        integer_value=42,
        float_value=3.125,
        boolean_value=True,
        list_value=["alpha", "beta"],
        object_value={"batch": "YB-007", "priority": 3},
    )

    with pytest.raises(SensorError) as raised:
        device.test_parameter_receive_node(
            upstream_parameters=source_result["parameters"],
            local_text="downstream-local",
            local_integer=7,
            local_float=9.75,
            local_boolean=False,
            local_list=["local-a", "local-b"],
            local_object={
                "operator": "yxz321",
                "retry_limit": 2,
                "flags": [True, False],
            },
        )

    alarm = apply_builtin_error_policy_to_alarm(
        raised.value.to_alarm_dict(
            device_id="fault_injection_device",
            device_uuid="device-uuid",
            action_name="test_parameter_receive_node",
            task_id="task-1",
            job_id="job-2",
            traceback_text="traceback",
        ),
        get_action_error_policy(device.test_parameter_receive_node),
    )

    assert alarm["device_snapshot"] == {
        "received_parameters": {
            "text_value": "upstream-text",
            "integer_value": 42,
            "float_value": 3.125,
            "boolean_value": True,
            "list_value": ["alpha", "beta"],
            "object_value": {"batch": "YB-007", "priority": 3},
        },
        "local_parameters": {
            "text_value": "downstream-local",
            "integer_value": 7,
            "float_value": 9.75,
            "boolean_value": False,
            "list_value": ["local-a", "local-b"],
            "object_value": {
                "operator": "yxz321",
                "retry_limit": 2,
                "flags": [True, False],
            },
        },
    }
    assert [item["action"] for item in alarm["suggested_actions"]] == ["retry", "abort"]
