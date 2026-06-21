from __future__ import annotations

from typing import Optional

from unilabos.registry.pair_registry import PairEntry, lookup as lookup_device_pair
from unilabos.registry.registry import lab_registry
from unilabos.resources.resource_tracker import ResourceDictInstance
from unilabos.ros.device_node_wrapper import ros2_device_node
from unilabos.ros.nodes.base_device_node import DeviceInitError, ROS2DeviceNode
from unilabos.sim.context import RuntimeContext, get_runtime_context
from unilabos.sim.stub_device import NullDeviceStub
from unilabos.sim.twin_bridge import TwinBridge
from unilabos.sim.twin_pair import TwinDriverPair
from unilabos.utils import logger
from unilabos.utils.exception import DeviceClassInvalid
from unilabos.utils.import_manager import default_manager


class MissingVirtualDeviceError(RuntimeError):
    pass


def _lookup_registry_class(device_id: str, class_name: str, device_config: ResourceDictInstance):
    if len(class_name) == 0:
        raise DeviceClassInvalid(f"Device [{device_id}] class cannot be an empty string. {device_config}")
    if class_name not in lab_registry.device_type_registry:
        # Plan 09 Task 7: graph may reference a community variant id (community.<id>);
        # fall back to the normalized local id if the prefixed one isn't registered.
        from unilabos.registry.community_alias import normalize_community_class

        normalized = normalize_community_class(class_name)
        if normalized in lab_registry.device_type_registry:
            class_name = normalized
        else:
            raise DeviceClassInvalid(f"Device [{device_id}] class {class_name} not found. {device_config}")
    return lab_registry.device_type_registry[class_name]["class"]


def _instantiate_device_node(
    device_id: str,
    device_config: ResourceDictInstance,
    class_name: Optional[str] = None,
    driver_params=None,
):
    d = None
    device_class_config = class_name if class_name is not None else device_config.res_content.klass
    uid = device_config.res_content.uuid
    if isinstance(device_class_config, str):
        device_class_config = _lookup_registry_class(device_id, device_class_config, device_config)
    elif isinstance(device_class_config, dict):
        raise DeviceClassInvalid(
            f"Device [{device_id}] class config should be type 'str' but 'dict' got. {device_config}"
        )
    if isinstance(device_class_config, dict):
        DEVICE = default_manager.get_class(device_class_config["module"])
        DEVICE = ros2_device_node(
            DEVICE,
            status_types=device_class_config.get("status_types", {}),
            device_config=device_config,
            action_value_mappings=device_class_config.get("action_value_mappings", {}),
            hardware_interface=device_class_config.get(
                "hardware_interface",
                {"name": "hardware_interface", "write": "send_command", "read": "read_data", "extra_info": []},
            ),
        )
        effective_params = driver_params if driver_params is not None else device_config.res_content.config
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
        except DeviceInitError:
            return d
    else:
        logger.warning(f"initialize device {device_id} failed, provided device_config: {device_config}")
    return d


def _build_stub_node(device_id: str, device_config: ResourceDictInstance, real_class_name: str):
    uid = device_config.res_content.uuid
    DEVICE = ros2_device_node(
        NullDeviceStub,
        status_types={},
        device_config=device_config,
        action_value_mappings={},
        hardware_interface={"name": "hardware_interface", "write": "send_command", "read": "read_data", "extra_info": []},
    )
    return DEVICE(
        device_id=device_id,
        device_uuid=uid,
        driver_is_ros=False,
        driver_params={"real_class_name": real_class_name, "node_id": device_id, "config": device_config.res_content.config},
    )


def _select_sim_class(real_class_name: str, pair: PairEntry) -> Optional[str]:
    return pair.virtual


def _build_twin_pair(device_id: str, device_config: ResourceDictInstance, real_class_name: str, pair: PairEntry):
    real = _instantiate_device_node(device_id, device_config, real_class_name)
    if pair.virtual:
        virtual = _instantiate_device_node(f"{device_id}__virtual", device_config, pair.virtual)
    elif pair.missing_sim_policy == "fail":
        raise MissingVirtualDeviceError(f"No virtual device for {device_id} ({real_class_name})")
    else:
        virtual = _build_stub_node(f"{device_id}__virtual", device_config, real_class_name)
    bridge = TwinBridge(
        real_driver=getattr(real, "driver_instance", real),
        virtual_driver=getattr(virtual, "driver_instance", virtual),
        node_id=device_id,
        observed_fields=pair.twin_observed,
        throttle_hz=pair.twin_throttle_hz,
    )
    return TwinDriverPair(real=real, virtual=virtual, bridge=bridge)


def initialize_device_from_dict(
    device_id,
    device_config: ResourceDictInstance,
    runtime: Optional[RuntimeContext] = None,
) -> Optional[ROS2DeviceNode]:
    """Initialize a device according to real/sim/twin runtime mode."""

    runtime = runtime or get_runtime_context()
    real_class_name = device_config.res_content.klass
    if not isinstance(real_class_name, str):
        return _instantiate_device_node(device_id, device_config)

    if runtime.mode == "real":
        return _instantiate_device_node(device_id, device_config, real_class_name)

    pair = lookup_device_pair(real_class_name)
    if runtime.mode == "sim":
        virtual_class = _select_sim_class(real_class_name, pair)
        if virtual_class:
            logger.info(f"[sim] {device_id}: {real_class_name} -> {virtual_class}")
            return _instantiate_device_node(device_id, device_config, virtual_class)
        if pair.missing_sim_policy == "fail":
            raise MissingVirtualDeviceError(f"No virtual device for {device_id} ({real_class_name})")
        if pair.missing_sim_policy == "skip":
            logger.warning(f"[sim] skipping {device_id} ({real_class_name})")
            return None
        logger.warning(f"[sim] stubbing {device_id} ({real_class_name})")
        return _build_stub_node(device_id, device_config, real_class_name)

    if runtime.mode == "twin":
        return _build_twin_pair(device_id, device_config, real_class_name, pair)

    raise ValueError(f"Unsupported runtime mode: {runtime.mode}")
