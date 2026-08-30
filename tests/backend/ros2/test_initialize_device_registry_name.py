from types import SimpleNamespace

import pytest


initialize_device = pytest.importorskip(
    "unilabos.backend.ros2.initialize_device",
    reason="设备初始化测试依赖 Jazzy 版 unilabos_msgs",
    exc_type=ImportError,
)


def _device_config(klass):
    return SimpleNamespace(
        res_content=SimpleNamespace(
            klass=klass,
            uuid="local-device-uuid",
            config={},
        )
    )


def test_device_class_input_must_be_registry_name_string():
    with pytest.raises(initialize_device.DeviceClassInvalid, match="registry name string"):
        initialize_device.initialize_device_from_dict(
            "device-1",
            _device_config({"module": "package.module:Driver"}),
        )


def test_device_registry_name_cannot_be_empty():
    with pytest.raises(initialize_device.DeviceClassInvalid, match="cannot be empty"):
        initialize_device.initialize_device_from_dict("device-1", _device_config("  "))


def test_registry_driver_config_must_be_internal_object(monkeypatch):
    monkeypatch.setitem(
        initialize_device.lab_registry.device_type_registry,
        "invalid-driver-config",
        {"class": "package.module:Driver"},
    )

    with pytest.raises(initialize_device.DeviceClassInvalid, match=r"registry .*\.class must be an object"):
        initialize_device.initialize_device_from_dict(
            "device-1",
            _device_config("invalid-driver-config"),
        )
