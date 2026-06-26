from typing import Optional

from unilabos.registry.registry import lab_registry
from unilabos.ros.device_node_wrapper import ros2_device_node
from unilabos.ros.nodes.base_device_node import ROS2DeviceNode, DeviceInitError
from unilabos.resources.resource_tracker import ResourceDictInstance
from unilabos.utils import logger
from unilabos.utils.exception import DeviceClassInvalid
from unilabos.utils.import_manager import default_manager



def initialize_device_from_dict(device_id, device_config: ResourceDictInstance) -> Optional[ROS2DeviceNode]:
    """Initializes a device based on its configuration.

    This function dynamically imports the appropriate device class and creates an instance of it using the provided device configuration.
    It also sets up action clients for the device based on its action value mappings.

    Args:
        device_id (str): The unique identifier for the device.
        device_config (dict): The configuration dictionary for the device, which includes the class type and other parameters.

    Returns:
        None
    """
    d = None
    device_class_config = device_config.res_content.klass
    uid = device_config.res_content.uuid
    if isinstance(device_class_config, str):  # 如果是字符串，则直接去lab_registry中查找，获取class
        if len(device_class_config) == 0:
            raise DeviceClassInvalid(f"Device [{device_id}] class cannot be an empty string. {device_config}")
        if device_class_config not in lab_registry.device_type_registry:
            # Plan 09 Task 7: graph 可能引用 community 变体 id(community.<id>);
            # 若带前缀的 id 未注册,回退到归一化后的本地 id。
            from unilabos.registry.community_alias import normalize_community_class

            normalized = normalize_community_class(device_class_config)
            if normalized in lab_registry.device_type_registry:
                device_class_config = normalized
            else:
                raise DeviceClassInvalid(f"Device [{device_id}] class {device_class_config} not found. {device_config}")
        device_class_config = lab_registry.device_type_registry[device_class_config]["class"]
    elif isinstance(device_class_config, dict):
        raise DeviceClassInvalid(f"Device [{device_id}] class config should be type 'str' but 'dict' got. {device_config}")
    if isinstance(device_class_config, dict):
        DEVICE = default_manager.get_class(device_class_config["module"])
        # 不管是ros2的实例，还是python的，都必须包一次，除了HostNode
        DEVICE = ros2_device_node(
            DEVICE,
            status_types=device_class_config.get("status_types", {}),
            device_config=device_config,
            action_value_mappings=device_class_config.get("action_value_mappings", {}),
            hardware_interface=device_class_config.get(
                "hardware_interface",
                {"name": "hardware_interface", "write": "send_command", "read": "read_data", "extra_info": []},
            )
        )
        effective_params = device_config.res_content.config
        # Plan 09 Task 6: external variant registry entries declare class.init; resolve it
        # (build factory objects + inject ${config.*}/${node.*}) and merge into driver_params,
        # keeping the existing ROS2DeviceNode wrapper/creator construction path.
        if device_class_config.get("init"):
            from unilabos.registry.initializer import resolve_init_kwargs

            node_meta = {"id": device_id, "name": getattr(device_config.res_content, "name", device_id)}
            # class.init fully defines the constructor kwargs; the raw config is only the
            # source for ${config.*} placeholders, so it replaces (not merges into) params.
            resolved = resolve_init_kwargs({"class": device_class_config}, node=node_meta, config=effective_params or {})
            effective_params = resolved["kwargs"]
        try:
            d = DEVICE(
                device_id=device_id,
                device_uuid=uid,
                driver_is_ros=device_class_config["type"] == "ros2",
                driver_params=effective_params,
            )
        except DeviceInitError as ex:
            return d
    else:
        logger.warning(f"initialize device {device_id} failed, provided device_config: {device_config}")
    return d
