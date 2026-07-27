import json
import os

# from nt import device_encoding
import threading
import time
from typing import Optional, Dict, Any, List
import uuid

import rclpy
from unilabos_msgs.srv._serial_command import SerialCommand_Response

from unilabos.app.register import register_devices_and_resources
from unilabos.ros.nodes.presets.resource_mesh_manager import ResourceMeshManager
from unilabos.resources.resource_tracker import DeviceNodeResourceTracker, ResourceTreeSet
from unilabos.devices.ros_dev.liquid_handler_joint_publisher import LiquidHandlerJointPublisher
from unilabos_msgs.srv import SerialCommand  # type: ignore
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.timer import Timer

from unilabos.registry.registry import lab_registry
from unilabos.ros.initialize_device import initialize_device_from_dict
from unilabos.ros.nodes.presets.host_node import HostNode
from unilabos.utils import logger
from unilabos.config.config import BasicConfig
from unilabos.utils.type_check import TypeEncoder


def exit() -> None:
    """关闭ROS节点和资源"""
    host_instance = HostNode.get_instance()
    if host_instance is not None:
        # 停止发现定时器
        # noinspection PyProtectedMember
        if hasattr(host_instance, "_discovery_timer") and isinstance(host_instance._discovery_timer, Timer):
            # noinspection PyProtectedMember
            host_instance._discovery_timer.cancel()
        for _, device_node in host_instance.devices_instances.items():
            if hasattr(device_node, "destroy_node"):
                device_node.ros_node_instance.destroy_node()
        host_instance.destroy_node()
    rclpy.shutdown()


def main(
    devices_config: ResourceTreeSet,
    resources_config: ResourceTreeSet,
    resources_edge_config: list[dict] = [],
    graph: Optional[Dict[str, Any]] = None,
    controllers_config: Dict[str, Any] = {},
    bridges: List[Any] = [],
    visual: str = "disable",
    resources_mesh_config: dict = {},
    rclpy_init_args: List[str] = ["--log-level", "debug"],
    discovery_interval: float = 15.0,
) -> None:
    """主函数"""

    # Support restart - check if rclpy is already initialized
    if not rclpy.ok():
        rclpy.init(args=rclpy_init_args)
    else:
        logger.info("[ROS] rclpy already initialized, reusing context")
    executor = rclpy.__executor = MultiThreadedExecutor(num_threads=max(os.cpu_count() * 4, 48))
    # 创建主机节点
    host_node = HostNode(
        "host_node",
        devices_config,
        resources_config,
        resources_edge_config,
        graph,
        controllers_config,
        bridges,
        discovery_interval,
    )

    # HostLink：host 侧 TCP 通路（物料本地事实源 + slave 在线监控 + ROS 组网协助下发）
    _start_hostlink_server(host_node)

    if visual != "disable":
        from unilabos.ros.nodes.presets.joint_republisher import JointRepublisher

        # 将 ResourceTreeSet 转换为 list 用于 visual 组件
        resources_list = (
            [node.res_content.model_dump(by_alias=True) for node in resources_config.all_nodes]
            if resources_config
            else []
        )
        resource_mesh_manager = ResourceMeshManager(
            resources_mesh_config,
            resources_list,
            resource_tracker=host_node.resource_tracker,
            device_id="resource_mesh_manager",
            device_uuid=str(uuid.uuid4()),
        )
        joint_republisher = JointRepublisher("joint_republisher", host_node.resource_tracker)
        # lh_joint_pub = LiquidHandlerJointPublisher(
        #     resources_config=resources_list, resource_tracker=host_node.resource_tracker
        # )
        executor.add_node(resource_mesh_manager)
        executor.add_node(joint_republisher)
        # executor.add_node(lh_joint_pub)

    thread = threading.Thread(target=executor.spin, daemon=True, name="host_executor_thread")
    thread.start()

    while True:
        time.sleep(1)


def _start_hostlink_server(host_node) -> None:
    """host 侧启动 HostLink TCP 服务（enable=False 时跳过，纯 ROS 行为不变）。

    物料查询以 host 内存资源树为本地事实源（云端物料接口已下线）；
    hello 响应附带 ROS 组网协助信息，slave 可据此降级组网（静态对端/单播）。
    """
    from unilabos.config.config import HostLinkConfig

    if not HostLinkConfig.enable:
        return
    try:
        from unilabos.hostlink import (
            HostLinkServer,
            LocalResourceResolver,
            build_host_ros_info,
        )
        from unilabos.hostlink.protocol import ActionType
        from unilabos.hostlink.ros_assist import detect_local_ip

        resolver = LocalResourceResolver(lambda: host_node.resources_config)
        host_ip = HostLinkConfig.advertise_ip or detect_local_ip()
        domain_raw = str(HostLinkConfig.ros_domain_id or "").strip()
        static_peers = [p.strip() for p in HostLinkConfig.ros_static_peers.split(";") if p.strip()]
        ros_info = build_host_ros_info(
            host_ip=host_ip,
            domain_id=int(domain_raw) if domain_raw.isdigit() else None,
            discovery_range=HostLinkConfig.ros_discovery_range.strip().upper(),
            static_peers=static_peers or None,
            discovery_server=HostLinkConfig.ros_discovery_server.strip(),
        )

        def _material(data, peer):
            nodes = resolver.resolve(
                uuid=data.get("uuid") or None,
                res_id=data.get("id") or None,
                with_children=bool(data.get("with_children", True)),
            )
            return {"nodes": nodes}

        server = HostLinkServer(
            bind=HostLinkConfig.bind,
            port=HostLinkConfig.port,
            heartbeat_timeout=HostLinkConfig.heartbeat_timeout,
        )
        server.hello_payload = {"host_name": BasicConfig.machine_name, "ros": ros_info.to_dict()}
        server.register_handler(ActionType.MATERIAL, _material)
        server.register_handler(ActionType.ROS_INFO, lambda data, peer: {"ros": ros_info.to_dict()})
        server.start()
        host_node.hostlink_server = server  # 供关停
        from unilabos.hostlink.server import set_hostlink_server

        set_hostlink_server(server)  # REST 面（/api/v1/hostlink/peers）做在线监控
    except Exception as exc:  # noqa: BLE001 - 通路失败不阻塞 ROS 主流程
        logger.error(f"[HostLink] server start failed (ROS-only fallback): {exc}")


def slave(
    devices_config: ResourceTreeSet,
    resources_config: ResourceTreeSet,
    resources_edge_config: list = [],
    graph: Optional[Dict[str, Any]] = None,
    controllers_config: Dict[str, Any] = {},
    bridges: List[Any] = [],
    visual: str = "disable",
    resources_mesh_config: dict = {},
    rclpy_init_args: List[str] = ["--log-level", "debug"],
) -> None:
    """从节点函数"""
    # 0. HostLink 组网：先连 host 拿 ROS 组网协助（域号/静态对端/发现降级），
    #    必须发生在 rclpy.init 之前（DDS 只在初始化时读这些环境变量）。
    #    连不上不阻塞启动：走原纯 ROS 组播发现。
    from unilabos.config.config import HostLinkConfig

    hostlink_domain_id: Optional[int] = None  # host 下发的域号，直传 rclpy.init
    if HostLinkConfig.enable and HostLinkConfig.host:
        try:
            from unilabos.hostlink import (
                HostLinkClient,
                apply_ros_network_env,
                set_hostlink_client,
            )

            link_client = HostLinkClient(
                HostLinkConfig.host,
                HostLinkConfig.port,
                machine_name=BasicConfig.machine_name,
                heartbeat_interval=HostLinkConfig.heartbeat_interval,
                connect_timeout=HostLinkConfig.connect_timeout,
                request_timeout=HostLinkConfig.request_timeout,
            )
            if link_client.connect_blocking(timeout=HostLinkConfig.connect_timeout * 2):
                if HostLinkConfig.ros_assist_apply:
                    ros_info = link_client.hello_ros_info()
                    # TCP 能连通的地址就是 DDS 应该单播的地址：把 HostLink 连接地址并入
                    # static peers（实测 WSL2 同机场景 host 自测 IP 是 NAT 网卡地址，
                    # DDS 发现耗时 ~107s；补上连接地址后走环回单播，秒级发现）
                    if HostLinkConfig.host and HostLinkConfig.host not in ros_info.static_peers:
                        ros_info.static_peers.append(HostLinkConfig.host)
                    applied = apply_ros_network_env(ros_info)
                    # 域号走两条路：env（惠及子进程/命令行工具）+ init 形参（本进程
                    # 显式直传，不受后续环境变化影响）；发现类配置（range/peers/
                    # discovery server）rclpy 没有形参，只能靠 init 前的环境变量。
                    hostlink_domain_id = ros_info.domain_id
                    if applied:
                        logger.info(f"[HostLink] ROS 组网信息已套用: {applied}")
                else:
                    logger.info("[HostLink] ros_assist_apply=False：跳过组网套用，仅保留 TCP 通路")
            else:
                logger.warning(
                    f"[HostLink] 无法连接 host {HostLinkConfig.host}:{HostLinkConfig.port}，"
                    f"后台持续重连；本次启动按纯 ROS 组播发现进行"
                )
            # 注册进程级单例：设备节点物料查询 TCP 优先、ROS service 兜底
            set_hostlink_client(link_client)
        except Exception as exc:  # noqa: BLE001 - 通路失败不阻塞 ROS 启动
            logger.error(f"[HostLink] client init failed (ROS-only fallback): {exc}")

    # 1. 初始化 ROS2（domain_id=None 时 rclpy 回退环境变量/默认域）
    if not rclpy.ok():
        try:
            rclpy.init(args=rclpy_init_args, domain_id=hostlink_domain_id)
        except TypeError:
            # 旧版 rclpy 无 domain_id 形参：环境变量路径已在上面套用
            rclpy.init(args=rclpy_init_args)
    executor = rclpy.__executor
    if not executor:
        executor = rclpy.__executor = MultiThreadedExecutor(num_threads=max(os.cpu_count() * 4, 48))

    # 1.5 启动 executor 线程
    thread = threading.Thread(target=executor.spin, daemon=True, name="slave_executor_thread")
    thread.start()

    # 2. 创建 Slave Machine Node
    n = Node(f"slaveMachine_{BasicConfig.machine_name}", parameter_overrides=[])
    executor.add_node(n)

    # 3. 向 Host 报送节点信息和物料，获取 UUID 映射
    uuid_mapping = {}
    if not BasicConfig.slave_no_host:
        # 3.1 报送节点信息
        sclient = n.create_client(SerialCommand, "/node_info_update")
        sclient.wait_for_service()

        registry_config = {}
        devices_to_register, resources_to_register = register_devices_and_resources(lab_registry, True)
        registry_config.update(devices_to_register)
        registry_config.update(resources_to_register)
        request = SerialCommand.Request()
        request.command = json.dumps(
            {
                "machine_name": BasicConfig.machine_name,
                "type": "slave",
                "devices_config": devices_config.dump(),
                "registry_config": registry_config,
            },
            ensure_ascii=False,
            cls=TypeEncoder,
        )
        sclient.call_async(request).result()
        logger.info(f"Slave node info updated.")

        # 3.2 报送物料树，获取 UUID 映射
        if resources_config:
            rclient = n.create_client(SerialCommand, "/c2s_update_resource_tree")
            rclient.wait_for_service()

            request = SerialCommand.Request()
            request.command = json.dumps(
                {
                    "data": {
                        "data": resources_config.dump(),
                        "mount_uuid": "",
                        "first_add": True,
                    },
                    "action": "add",
                },
                ensure_ascii=False,
            )
            tree_response: SerialCommand_Response = rclient.call(request)
            uuid_mapping = json.loads(tree_response.response)
            logger.info(f"Slave resource tree added. UUID mapping: {len(uuid_mapping)} nodes")

            # 3.3 使用 UUID 映射更新 resources_config 的 UUID（参考 client.py 逻辑）
            old_uuids = {node.res_content.uuid: node for node in resources_config.all_nodes}
            for old_uuid, node in old_uuids.items():
                if old_uuid in uuid_mapping:
                    new_uuid = uuid_mapping[old_uuid]
                    node.res_content.uuid = new_uuid
                    # 更新所有子节点的 parent_uuid
                    for child in node.children:
                        child.res_content.parent_uuid = new_uuid
                else:
                    logger.warning(f"资源UUID未更新: {old_uuid}")
        else:
            logger.info("No resources to add.")

    # 4. 初始化所有设备实例（此时 resources_config 的 UUID 已更新）
    devices_instances = {}
    for device_config in devices_config.root_nodes:
        device_id = device_config.res_content.id
        if device_config.res_content.type == "device":
            d = initialize_device_from_dict(device_id, device_config)
            if d is not None:
                devices_instances[device_id] = d
                logger.info(f"Device {device_id} initialized.")
            else:
                logger.warning(f"Device {device_id} initialization failed.")

    # 5. 如果启用可视化，创建可视化相关节点
    if visual != "disable":
        from unilabos.ros.nodes.presets.joint_republisher import JointRepublisher

        # 将 ResourceTreeSet 转换为 list 用于 visual 组件
        resources_list = (
            [node.res_content.model_dump(by_alias=True) for node in resources_config.all_nodes]
            if resources_config
            else []
        )
        resource_mesh_manager = ResourceMeshManager(
            resources_mesh_config,
            resources_list,
            resource_tracker=DeviceNodeResourceTracker(),
            device_id="resource_mesh_manager",
        )
        joint_republisher = JointRepublisher("joint_republisher", DeviceNodeResourceTracker())
        lh_joint_pub = LiquidHandlerJointPublisher(
            resources_config=resources_list, resource_tracker=DeviceNodeResourceTracker()
        )
        executor.add_node(resource_mesh_manager)
        executor.add_node(joint_republisher)
        executor.add_node(lh_joint_pub)

    # 7. 保持运行
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
