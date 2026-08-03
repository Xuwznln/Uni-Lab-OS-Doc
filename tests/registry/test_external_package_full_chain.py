"""目录式社区包从发现、加载到 JSON 配置构建设备的完整合同。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from unilabos.app.package_cli import discover_registry_paths_from_project
from unilabos.registry.init_enforce import merge_init_param_enforce
from unilabos.registry.registry import Registry
from unilabos.resources.resource_tracker import DeviceNodeResourceTracker
from unilabos.ros.utils.driver_creator import DeviceClassCreator
from tests.registry.fixtures.initializer_drivers import SharedDevice

PKG = Path(__file__).parent / "fixtures" / "external_variant_pkg"


def test_external_package_discover_load_and_construct_from_json_config():
    paths = discover_registry_paths_from_project(PKG)
    assert paths == [(PKG / "unilabos_registry").resolve()]

    registry = Registry()
    if registry._startup_executor is None:
        registry._startup_executor = ThreadPoolExecutor(max_workers=2)
    registry.load_device_types(paths[0], complete_registry=False)

    model_a = registry.device_type_registry["vendor.lh.model_a"]
    model_b = registry.device_type_registry["vendor.lh.model_b"]
    assert model_a["class"]["module"] == model_b["class"]["module"]
    assert model_a["init_param_enforce"] == {"deck_name": "model-a-deck", "channels": 8}
    assert model_b["init_param_enforce"] == {"deck_name": "model-b-deck", "channels": 96}
    assert "setup" in model_a["class"]["action_value_mappings"]
    assert "initialized" in model_b["class"]["status_types"]

    driver_params = merge_init_param_enforce(
        {"host": "10.0.0.9", "port": 7001, "channels": 1},
        model_b["init_param_enforce"],
    )
    driver_params["device_id"] = "lh_b"
    creator = DeviceClassCreator(
        SharedDevice,
        children=[],
        resource_tracker=DeviceNodeResourceTracker(),
    )
    device = creator.create_instance(driver_params)

    assert device.name == "lh_b"
    assert device.channels == 96
    assert device.backend.host == "10.0.0.9"
    assert device.backend.port == 7001
    assert device.deck.name == "model-b-deck"
