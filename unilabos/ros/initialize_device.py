from typing import Optional

from unilabos.registry.init_enforce import merge_init_param_enforce
from unilabos.registry.registry import lab_registry
from unilabos.ros.device_node_wrapper import ros2_device_node
from unilabos.ros.nodes.base_device_node import ROS2DeviceNode, DeviceInitError
from unilabos.resources.resource_tracker import ResourceDictInstance
from unilabos.resources.device_site_adapter import apply_device_available_sites
from unilabos.utils.exception import DeviceClassInvalid
from unilabos.utils.import_manager import default_manager


def initialize_device_from_dict(device_id, device_config: ResourceDictInstance) -> Optional[ROS2DeviceNode]:
    """Initializes a device based on its configuration.

    This function dynamically imports the appropriate device class and creates an
    instance of it using the provided device configuration.
    It also sets up action clients for the device based on its action value mappings.

    Args:
        device_id (str): The unique identifier for the device.
        device_config (dict): The configuration dictionary for the device.

    Returns:
        None
    """
    registry_name = device_config.res_content.klass
    if not isinstance(registry_name, str):
        raise DeviceClassInvalid(
            f"Device [{device_id}] class must be a registry name string, "
            f"but {type(registry_name).__name__} got. {device_config}"
        )
    registry_name = registry_name.strip()
    if not registry_name:
        raise DeviceClassInvalid(f"Device [{device_id}] class cannot be empty. {device_config}")
    if registry_name not in lab_registry.device_type_registry:
        raise DeviceClassInvalid(
            f"Device [{device_id}] registry {registry_name!r} not found. {device_config}"
        )

    registry_entry = lab_registry.device_type_registry[registry_name]
    if not isinstance(registry_entry, dict):
        raise DeviceClassInvalid(
            f"Device [{device_id}] registry {registry_name!r} must be an object. {device_config}"
        )
    driver_config = registry_entry.get("class")
    if not isinstance(driver_config, dict):
        raise DeviceClassInvalid(
            f"Device [{device_id}] registry {registry_name!r}.class must be an object. {device_config}"
        )
    module = driver_config.get("module")
    if not isinstance(module, str) or not module.strip():
        raise DeviceClassInvalid(
            f"Device [{device_id}] registry {registry_name!r}.class.module must be a non-empty string. "
            f"{device_config}"
        )
    module = module.strip()

    apply_device_available_sites(device_config, registry_entry, registry_name)
    device_type = default_manager.get_class(module)
    # 不管是 ROS2 驱动还是 Python 驱动，都统一包装为设备节点（HostNode 除外）。
    device_node_type = ros2_device_node(
        device_type,
        status_types=driver_config.get("status_types", {}),
        device_config=device_config,
        action_value_mappings=driver_config.get("action_value_mappings", {}),
        hardware_interface=driver_config.get(
            "hardware_interface",
            {"name": "hardware_interface", "write": "send_command", "read": "read_data", "extra_info": []},
        ),
    )

    runtime_config = device_config.res_content.config
    if not isinstance(runtime_config, dict):
        runtime_config = {}
    driver_params = merge_init_param_enforce(
        runtime_config,
        registry_entry.get("init_param_enforce"),
    )
    try:
        return device_node_type(
            device_id=device_id,
            device_uuid=device_config.res_content.uuid,
            driver_is_ros=driver_config.get("type") == "ros2",
            driver_params=driver_params,
        )
    except DeviceInitError:
        return None
