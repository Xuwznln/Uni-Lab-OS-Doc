import collections
import json
import threading
import time
import traceback
import uuid

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Dict, Any, List, ClassVar, Set, Tuple, Union

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import Point
from rclpy.action import ActionClient, get_action_server_names_and_types_by_node
from rclpy.service import Service
from typing_extensions import TypedDict
from unilabos_msgs.action import EmptyIn, StrSingleInput
from unilabos_msgs.srv import SerialCommand  # type: ignore
from unique_identifier_msgs.msg import UUID

from unilabos.registry.decorators import device, action, NodeType, ActionInputHandle, ActionOutputHandle, DataSource
from unilabos.registry.placeholder_type import (
    ResourceSlot,
    DeviceSlot,
    SiteSlot,
    PLACEHOLDER_DEVICES,
    PLACEHOLDER_NODES,
    PLACEHOLDER_MANUAL_CONFIRM,
    PLACEHOLDER_DEDUCT_RESOURCE,
    PLACEHOLDER_DEDUCT_REAGENT,
)
from unilabos.registry.registry import lab_registry
from unilabos.resources.presets.container import RegularContainer
from unilabos.backend import host_material_actions
from unilabos.backend.hostlink.protocol import ActionType
from unilabos.resources.objects.resource import ResourceDictType
from unilabos.resources.objects.sample import LabSample, SampleUUIDsType
from unilabos.resources.resource_tracker import (
    ResourceDictInstance,
    ResourceTreeSet,
    ResourceTreeInstance,
    RETURN_UNILABOS_SAMPLES,
    JSON_UNILABOS_PARAM,
    PARAM_SAMPLE_UUIDS,
)
from unilabos.backend.ros2.initialize_device import initialize_device_from_dict
from unilabos.backend.ros2.msgs.message_converter import (
    get_msg_type,
    get_ros_type_by_msgname,
    convert_from_ros_msg,
    convert_to_ros_msg,
    msg_converter_manager,
)
from unilabos.backend.ros2.base_device_node import BaseROS2DeviceNode, ROS2DeviceNode, DeviceNodeResourceTracker
from unilabos.backend.ros2.presets.controller_node import ControllerNode
from unilabos.utils import logger
from unilabos.backend.runtime.exception import DeviceClassInvalid
from unilabos.utils.type_check import serialize_result_info
from unilabos.config.config import BasicConfig
from unilabos.backend.hostlink.adapter_registry import execution_result_bridges
from unilabos.backend.hostlink.server import get_hostlink_server
from unilabos.backend.ros2.hostlink_bridge import (
    DEFAULT_DOWNLINK_TIMEOUT,
    append_resource_via_hostlink,
    device_manage_to_device,
    get_local_device_node,
    sync_resource_tree_to_device,
)
from unilabos.registry.action_policy import SUCCESS_TYPE_CANCELLATION

if TYPE_CHECKING:
    from unilabos.server.backend.execution_queue import QueueItem


@dataclass
class DeviceActionStatus:
    job_ids: Dict[str, float] = field(default_factory=dict)


class TestResourceReturn(TypedDict):
    resources: List[List[ResourceDictType]]
    devices: List[DeviceSlot]
    unilabos_samples: List[LabSample]


class DeductResourceReturn(TypedDict):
    """apply_deduct_resource 返回值：字段本身保持 dump() 完整分组形态（数据传输用完整的）。

    物料创建只发生在微后端；本返回值承载「扣减/挂载」结果。单根树经 handle data_key 的
    @flatten 拍平一层后（见 created_resource_tree.@flatten），用户/下游拿到扁平节点 list。
    消费侧（ResourceSlot / materials.from_str）对两种形态都能自动拆包。

    substance_resource_tree：已加入 substance（内容物）的物料树，统一说法——
    出库物料若已带内容物（set_substance / 权威 data.substances），在此单独承载。
    """

    created_resource_tree: List[List[ResourceDictType]]
    substance_resource_tree: List[List[ResourceDictType]]
    unilabos_samples: List[LabSample]
    mount_resource: List[List[ResourceDictType]]


class TransferResourceReturn(TypedDict):
    """transfer_resource 返回值：透传被转移物料、目标孔位与槽位，便于下游引用。

    resource / mount_resource 均为「单个物料」的扁平节点形态（list[list[ResourceDict]]，单根，
    经 @flatten 后即一棵树的扁平节点 list），与 apply_deduct 输出一致、可直接连到下游单物料输入。
    """

    resource: List[List[ResourceDictType]]
    mount_resource: List[List[ResourceDictType]]
    site: str
    result: Any


class TestLatencyReturn(TypedDict):
    """test_latency方法的返回值类型"""

    avg_rtt_ms: float
    avg_time_diff_ms: float
    max_time_error_ms: float
    task_delay_ms: float
    raw_delay_ms: float
    test_count: int
    status: str


@device(id="host_node", category=[], description="Host Node", icon="icon_device.webp")
class HostNode(BaseROS2DeviceNode):
    """
    主机节点类，负责管理设备、资源和控制器

    作为单例模式实现，确保整个应用中只有一个主机节点实例
    """

    _instance: ClassVar[Optional["HostNode"]] = None
    _ready_event: ClassVar[threading.Event] = threading.Event()
    _shutting_down: ClassVar[bool] = False  # Flag to signal shutdown to background threads
    _background_threads: ClassVar[List[threading.Thread]] = []  # Track all background threads for cleanup
    _device_action_status: ClassVar[collections.defaultdict[str, DeviceActionStatus]] = collections.defaultdict(
        DeviceActionStatus
    )
    _resource_tracker: ClassVar[DeviceNodeResourceTracker] = DeviceNodeResourceTracker()  # 资源管理器实例
    @classmethod
    def get_instance(cls, timeout=None) -> Optional["HostNode"]:
        if cls._ready_event.wait(timeout):
            return cls._instance
        return None

    @classmethod
    def shutdown_background_threads(cls, timeout: float = 5.0) -> None:
        """
        Gracefully shutdown all background threads for clean exit or restart.

        This method:
        1. Sets shutdown flag to stop background operations
        2. Waits for background threads to finish with timeout
        3. Cleans up finished threads from tracking list

        Args:
            timeout: Maximum time to wait for each thread (seconds)
        """
        cls._shutting_down = True

        # Wait for background threads to finish
        active_threads = []
        for t in cls._background_threads:
            if t.is_alive():
                t.join(timeout=timeout)
                if t.is_alive():
                    active_threads.append(t.name)

        if active_threads:
            logger.warning(f"[Host Node] Some background threads still running: {active_threads}")

        # Clear the thread list
        cls._background_threads.clear()
        logger.info("[Host Node] Background threads shutdown complete")

    @classmethod
    def reset_state(cls) -> None:
        """
        Reset the HostNode singleton state for restart or clean exit.
        Call this after destroying the instance.
        """
        cls._instance = None
        cls._ready_event.clear()
        cls._shutting_down = False
        cls._background_threads.clear()
        logger.info("[Host Node] State reset complete")

    def __init__(
        self,
        device_id: str,
        devices_config: ResourceTreeSet,
        resources_config: ResourceTreeSet,
        resources_edge_config: list[dict],
        physical_setup_graph: Optional[Dict[str, Any]] = None,
        controllers_config: Optional[Dict[str, Any]] = None,
        bridges: Optional[List[Any]] = None,
        discovery_interval: float = 180.0,  # 设备发现间隔，单位为秒
    ):
        """
        初始化主机节点

        Args:
            device_id: 节点名称
            devices_config: 设备配置
            resources_config: 资源配置
            physical_setup_graph: 物理设置图
            controllers_config: 控制器配置
            bridges: 桥接器列表
            discovery_interval: 设备发现间隔（秒），默认5秒
        """
        if self._instance is not None:
            self._instance.lab_logger().critical("[Host Node] HostNode instance already exists.")

        # 设置单例实例
        self.__class__._instance = self

        # 初始化配置
        self.server_latest_timestamp = 0.0  #
        self.devices_config = devices_config
        self.resources_config = resources_config  # 直接保存 ResourceTreeSet
        self.resources_edge_config = resources_edge_config
        self.physical_setup_graph = physical_setup_graph
        if controllers_config is None:
            controllers_config = {}
        self.controllers_config = controllers_config
        if bridges is None:
            bridges = []
        self.bridges = bridges

        # 创建 host_node 作为一个单独的 ResourceTree
        host_node_dict = {
            "id": "host_node",
            "uuid": str(uuid.uuid4()),
            "parent_uuid": "",
            "name": "host_node",
            "type": "device",
            "class": "host_node",
            "config": {},
            "data": {},
            "children": [],
            "description": "",
            "schema": {},
            "model": {},
            "icon": "",
        }

        # 创建 host_node 的 ResourceTree
        host_node_instance = ResourceDictInstance.get_resource_instance_from_dict(host_node_dict)
        host_node_tree = ResourceTreeInstance(host_node_instance)
        resources_config.trees.insert(0, host_node_tree)
        # 初始化Node基类，传递空参数覆盖列表
        BaseROS2DeviceNode.__init__(
            self,
            driver_instance=self,
            device_id=device_id,
            registry_name="host_node",
            resource_uuid=host_node_dict["uuid"],
            status_types={},
            action_value_mappings=lab_registry.device_type_registry["host_node"]["class"]["action_value_mappings"],
            hardware_interface={},
            print_publish=False,
            resource_tracker=self._resource_tracker,  # host node并不是通过initialize 包一层传进来的
        )

        # 创建设备、动作客户端和目标存储
        self.devices_names: Dict[str, str] = {device_id: self.namespace}  # 存储设备名称和命名空间的映射
        self.devices_instances: Dict[str, ROS2DeviceNode] = {}  # 存储设备实例
        self.device_machine_names: Dict[str, str] = {
            device_id: "本地",
        }  # 存储设备ID到机器名称的映射
        self._action_clients: Dict[str, ActionClient] = {  # 为了方便了解实际的数据类型，host的默认写好
            "/devices/host_node/test_latency": ActionClient(
                self,
                EmptyIn,
                "/devices/host_node/test_latency",
                callback_group=self.callback_group,
            ),
            "/devices/host_node/test_resource": ActionClient(
                self,
                EmptyIn,
                "/devices/host_node/test_resource",
                callback_group=self.callback_group,
            ),
            "/devices/host_node/_execute_driver_command": ActionClient(
                self,
                StrSingleInput,
                "/devices/host_node/_execute_driver_command",
                callback_group=self.callback_group,
            ),
            "/devices/host_node/_execute_driver_command_async": ActionClient(
                self,
                StrSingleInput,
                "/devices/host_node/_execute_driver_command_async",
                callback_group=self.callback_group,
            ),
        }  # 用来存储多个ActionClient实例
        self._action_value_mappings: Dict[str, Dict] = {
            device_id: self._action_value_mappings
        }  # device_id -> action_value_mappings(本地+远程设备统一存储)
        self._slave_registry_configs: Dict[str, Dict] = {}  # registry_name -> registry_config(含action_value_mappings)
        self._goals: Dict[str, Any] = {}  # 用来存储多个目标的状态
        # HostNode 只负责 ROS2 transport；job 生命周期与错误决策归微后端。
        self._inflight_goal_jobs: Set[str] = set()
        self._goal_state_lock = threading.RLock()
        self._canceled_jobs: Set[str] = set()
        self._online_devices: Set[str] = {f"{self.namespace}/{device_id}"}  # 用于跟踪在线设备
        self._last_discovery_time = 0.0  # 上次设备发现的时间
        self._discovery_lock = threading.Lock()  # 设备发现的互斥锁
        self._subscribed_topics = set()  # 用于跟踪已订阅的话题

        # 创建物料增删改查服务（非客户端）
        self._init_host_service()

        self.device_status = {}  # 用来存储设备状态
        self.device_status_timestamps = {}  # 用来存储设备状态最后更新时间
        time.sleep(1)  # 等待通信连接稳定
        # 首次发现网络中的设备
        self._discover_devices()

        # 初始化所有本机设备节点，多一次过滤，防止重复初始化
        local_machine = BasicConfig.machine_name
        for device_config in devices_config.root_nodes:
            device_id = device_config.res_content.id
            if device_config.res_content.type != "device":
                continue
            dev_machine = device_config.res_content.machine_name
            if dev_machine and local_machine and dev_machine != local_machine:
                self.lab_logger().info(
                    f"[Host Node] Device {device_id} belongs to machine '{dev_machine}', "
                    f"local is '{local_machine}', skipping initialization."
                )
                continue
            if device_id not in self.devices_names:
                self.initialize_device(device_id, device_config)
            else:
                self.lab_logger().warning(f"[Host Node] Device {device_id} already existed, skipping.")
        self.update_device_status_subscriptions()
        # TODO: 需要验证 初始化所有控制器节点
        if controllers_config:
            update_rate = controllers_config["controller_manager"]["ros__parameters"]["update_rate"]
            for controller_id, controller_config in controllers_config["controller_manager"]["ros__parameters"][
                "controllers"
            ].items():
                controller_config["update_rate"] = update_rate
                self.initialize_controller(controller_id, controller_config)

        # 创建定时器，定期发现设备
        self._discovery_timer = self.create_timer(
            discovery_interval, self._discovery_devices_callback, callback_group=self.callback_group
        )

        # 添加ping-pong相关属性
        self._ping_responses = {}  # 存储ping响应
        self._ping_lock = threading.Lock()

        self.lab_logger().info("[Host Node] Host node initialized.")
        HostNode._ready_event.set()

        # 发送host_node ready信号到所有桥接器
        for bridge in self.bridges:
            if hasattr(bridge, "publish_host_ready"):
                bridge.publish_host_ready()
                self.lab_logger().debug(f"Host ready signal sent via {bridge.__class__.__name__}")

    def _notify_capabilities_changed(self) -> None:
        """设备/动作能力集变化：通知桥接器刷新 runtime.v1 endpoint 能力快照。"""

        for bridge in self.bridges:
            callback = getattr(bridge, "publish_capabilities_changed", None)
            if callable(callback):
                try:
                    callback()
                except Exception:  # noqa: BLE001 - 能力快照失败不影响设备发现
                    self.lab_logger().debug("capabilities 快照通知失败", exc_info=True)

    def _send_re_register(self, sclient, device_namespace: str):
        """
        Send re-register command to a device. This is a one-time operation.

        Args:
            sclient: The service client
            device_namespace: The device namespace for logging
        """
        try:
            # Use timeout to prevent indefinite blocking
            if not sclient.wait_for_service(timeout_sec=10.0):
                self.lab_logger().debug(f"[Host Node] Re-register timeout for {device_namespace}")
                return

            # Check shutdown flag after wait
            if self._shutting_down:
                self.lab_logger().debug(f"[Host Node] Re-register aborted for {device_namespace} (shutdown)")
                return

            request = SerialCommand.Request()
            request.command = ""
            future = sclient.call_async(request)
            # Use timeout for result as well
            future.result()
        except Exception as e:
            # Gracefully handle destruction during shutdown
            if "destruction was requested" in str(e) or self._shutting_down:
                self.lab_logger().debug(f"[Host Node] Re-register aborted for {device_namespace} (cleanup)")
            else:
                self.lab_logger().warning(f"[Host Node] Re-register failed for {device_namespace}: {e}")

    def _discover_devices(self) -> None:
        """
        发现网络中的设备

        检测ROS2网络中的所有设备节点，并为它们创建ActionClient
        同时检测设备离线情况
        """
        self.lab_logger().trace("[Host Node] Discovering devices in the network...")

        # 获取当前所有设备
        nodes_and_names = self.get_node_names_and_namespaces()

        # 跟踪本次发现的设备，用于检测离线设备；记录旧集合用于能力快照通知
        current_devices = set()
        previous_device_names = set(self.devices_names)
        previous_online_devices = set(self._online_devices)

        for device_id, namespace in nodes_and_names:
            if not namespace.startswith("/devices/"):
                continue
            edge_device_id = namespace[9:]
            # 将设备添加到当前设备集合
            device_key = f"{namespace}/{edge_device_id}"  # namespace已经包含device_id了，这里复写一遍
            current_devices.add(device_key)

            # 如果是新设备，记录并创建ActionClient
            if edge_device_id not in self.devices_names:
                self.lab_logger().info(f"[Host Node] Discovered new device: {edge_device_id}")
                self.devices_names[edge_device_id] = namespace
                self._create_action_clients_for_device(device_id, namespace)
                self._online_devices.add(device_key)
                sclient = self.create_client(SerialCommand, f"/srv{namespace}/re_register_device")
                t = threading.Thread(
                    target=self._send_re_register,
                    args=(sclient, namespace),
                    daemon=True,
                    name=f"ROSDevice{self.device_id}_re_register_device_{namespace}",
                )
                self._background_threads.append(t)
                t.start()
            elif device_key not in self._online_devices:
                # 设备重新上线
                self.lab_logger().info(f"[Host Node] Device reconnected: {device_key}")
                self._online_devices.add(device_key)
                sclient = self.create_client(SerialCommand, f"/srv{namespace}/re_register_device")
                t = threading.Thread(
                    target=self._send_re_register,
                    args=(sclient, namespace),
                    daemon=True,
                    name=f"ROSDevice{self.device_id}_re_register_device_{namespace}",
                )
                self._background_threads.append(t)
                t.start()

        # 检测离线设备
        offline_devices = self._online_devices - current_devices
        for device_key in offline_devices:
            self.lab_logger().warning(f"[Host Node] Device offline: {device_key}")
            self._online_devices.discard(device_key)

        # 更新在线设备列表
        self._online_devices = current_devices
        if (
            previous_device_names != set(self.devices_names)
            or previous_online_devices != current_devices
        ):
            self._notify_capabilities_changed()
        self.lab_logger().trace(f"[Host Node] Total online devices: {len(self._online_devices)}")

    def _discovery_devices_callback(self) -> None:
        """
        设备发现定时器回调函数
        """
        # 使用互斥锁确保同时只有一个发现过程
        if self._discovery_lock.acquire(blocking=False):
            try:
                self._discover_devices()
                # 发现新设备后，更新设备状态订阅
                self.update_device_status_subscriptions()
            finally:
                self._discovery_lock.release()
        else:
            self.lab_logger().debug("[Host Node] Device discovery already in progress, skipping.")

    def _report_action_locks_free(self, action_pairs: List[Tuple[str, str]]) -> None:
        """向所有桥接器主动上报新发现 action 的锁状态为 free(report_action_lock)。

        服务端直接下发 job 模式下，需要在发现新设备/新 action 时主动告知其可用，
        而不再依赖 query_action_state。
        """
        if not action_pairs:
            return
        # _execute_driver_command[_async] 是通用驱动命令入口，并非具体业务动作，
        # 不作为锁上报（与 WebSocketClient.report_all_action_locks 的过滤保持一致）。
        locks = [
            {"device_id": dev, "action_name": act, "free": True}
            for dev, act in action_pairs
            if not act.startswith("_execute_driver_command")
        ]
        if not locks:
            return
        for bridge in self.bridges:
            if hasattr(bridge, "publish_action_locks"):
                try:
                    bridge.publish_action_locks(locks)
                except Exception as e:
                    self.lab_logger().warning(f"[Host Node] publish_action_locks failed: {e}")

    def _create_action_clients_for_device(self, device_id: str, namespace: str) -> None:
        """
        为设备创建所有必要的ActionClient

        Args:
            device_id: 设备ID
            namespace: 设备命名空间
        """
        new_action_pairs: List[Tuple[str, str]] = []
        edge_device_id = namespace[9:]
        for action_id, action_types in get_action_server_names_and_types_by_node(self, device_id, namespace):
            if action_id not in self._action_clients:
                try:
                    action_type = get_ros_type_by_msgname(action_types[0])
                    self._action_clients[action_id] = ActionClient(
                        self, action_type, action_id, callback_group=self.callback_group
                    )
                    self.lab_logger().trace(f"[Host Node] Created ActionClient (Discovery): {action_id}")
                    action_name = action_id[len(namespace) + 1 :]
                    new_action_pairs.append((edge_device_id, action_name))
                except Exception as e:
                    self.lab_logger().error(f"[Host Node] Failed to create ActionClient for {action_id}: {str(e)}")

        # 补充 _action_value_mappings 中其余动作：UniLabJsonCommand 类型动作不建独立
        # ROS ActionServer，不会出现在 get_action_server_names_and_types_by_node 的结果里；
        # @action(auto_prefix=True) 注册成的 "auto-" 动作(如 workbench 的 prepare_materials 等)
        # 同理。它们仍是可经 _execute_driver_command 调用的能力，发现新设备时必须全量补报其
        # free 锁，否则服务端永远感知不到这些动作。_execute_driver_command[_async] 由
        # _report_action_locks_free 统一过滤，不在此处特判。
        already = {action_name for _, action_name in new_action_pairs}
        for action_name in self._action_value_mappings.get(edge_device_id, {}).keys():
            if action_name in already:
                continue
            new_action_pairs.append((edge_device_id, action_name))

        # 发现新 action 后主动上报其 free 锁状态
        self._report_action_locks_free(new_action_pairs)

    async def _material_dispatch(
        self, device_id: str, action_type: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """host_material_actions 的下行通道（ROS2 backend 形态）。

        链路不走 ROS service：本进程设备直接 await 实例协程，跨机（Slave）
        设备经 HostLink 下行 RPC（同步 RPC，多线程 executor 下阻塞可接受；
        设备不在线/未启用 HostLink 时直接抛错，物料链路不回退 ROS 发现）。
        """
        if action_type == ActionType.RESOURCE_APPEND:
            local_node = get_local_device_node(device_id)
            if local_node is not None:
                return await local_node.append_resource(dict(payload))
            return append_resource_via_hostlink(
                device_id, payload, DEFAULT_DOWNLINK_TIMEOUT
            )
        if action_type == ActionType.RESOURCE_TREE_SYNC:
            return sync_resource_tree_to_device(
                device_id, payload["operations"], DEFAULT_DOWNLINK_TIMEOUT
            )
        raise ValueError(f"未支持的物料下行类型: {action_type}")

    def initialize_device(self, device_id: str, device_config: ResourceDictInstance) -> None:
        """
        根据配置初始化设备，

        此函数根据提供的设备配置动态导入适当的设备类并创建其实例。
        同时为设备的动作值映射设置动作客户端。

        Args:
            device_id: 设备唯一标识符
            device_config: 设备配置字典，包含类型和其他参数
        """
        self.lab_logger().info(f"[Host Node] Initializing device: {device_id}")

        try:
            d = initialize_device_from_dict(device_id, device_config)
        except DeviceClassInvalid as e:
            self.lab_logger().error(f"[Host Node] Device class invalid: {e}")
            d = None
        if d is None:
            return
        # noinspection PyProtectedMember
        self.devices_names[device_id] = d._ros_node.namespace  # 这里不涉及二级device_id
        self.device_machine_names[device_id] = "本地"
        self.devices_instances[device_id] = d
        # noinspection PyProtectedMember
        self._action_value_mappings[device_id] = d._ros_node._action_value_mappings
        new_action_pairs: List[Tuple[str, str]] = []
        # 仅为建独立 ROS ActionServer 的动作创建 ActionClient：
        # auto-/UniLabJsonCommand 动作无 ROS action server，无法也无需建 ActionClient。
        # noinspection PyProtectedMember
        for action_name, action_value_mapping in d._ros_node._action_value_mappings.items():
            if action_name.startswith("auto-") or str(action_value_mapping.get("type", "")).startswith(
                "UniLabJsonCommand"
            ):
                continue
            action_id = f"/devices/{device_id}/{action_name}"
            if action_id not in self._action_clients:
                action_type = action_value_mapping["type"]
                try:
                    self._action_clients[action_id] = ActionClient(self, action_type, action_id)
                except Exception as e:
                    self.lab_logger().error(
                        f"创建ActionClient失败，Device: {device_id}, Action Name: {action_name}, Action Type: {action_type}, Error: {e}")
                    continue
                self.lab_logger().trace(
                    f"[Host Node] Created ActionClient (Local): {action_id}"
                )  # 子设备再创建用的是Discover发现的
                new_action_pairs.append((device_id, action_name))
            else:
                self.lab_logger().warning(f"[Host Node] ActionClient {action_id} already exists.")
        # 锁上报需全量：auto-/UniLabJsonCommand 动作虽不建 ActionClient，但仍是可经
        # _execute_driver_command 调用的能力(如 workbench 的 prepare_materials 等)，必须一并
        # 上报 free 锁，与 report_all_action_locks 的全量快照保持一致。_execute_driver_command
        # [_async] 由 _report_action_locks_free 统一过滤。
        # noinspection PyProtectedMember
        already = {action_name for _, action_name in new_action_pairs}
        for action_name in d._ros_node._action_value_mappings.keys():
            if action_name in already:
                continue
            new_action_pairs.append((device_id, action_name))
        device_key = f"{self.devices_names[device_id]}/{device_id}"  # 这里不涉及二级device_id
        # 添加到在线设备列表
        self._online_devices.add(device_key)
        # 新注册本地设备 action 后主动上报其 free 锁状态
        self._report_action_locks_free(new_action_pairs)

    def update_device_status_subscriptions(self) -> None:
        """
        更新设备状态订阅

        扫描所有设备话题，为新的话题创建订阅，确保不会重复订阅
        """
        topic_names_and_types = self.get_topic_names_and_types()
        for topic, types in topic_names_and_types:
            # 检查是否为设备状态话题且未订阅过
            if (
                topic.startswith("/devices/")
                and not types[0].endswith("FeedbackMessage")
                and "_action" not in topic
                and topic not in self._subscribed_topics
            ):

                # 解析设备名和属性名
                parts = topic.split("/")
                if len(parts) >= 4:  # 可能有WorkstationNode，创建更长的设备
                    device_id = "/".join(parts[2:-1])
                    property_name = parts[-1]

                    # 初始化设备状态字典
                    if device_id not in self.device_status:
                        self.device_status[device_id] = {}
                        self.device_status_timestamps[device_id] = {}

                    # 默认初始化属性值为 None
                    self.device_status[device_id] = collections.defaultdict()
                    self.device_status_timestamps[device_id][property_name] = 0  # 初始化时间戳

                    # 动态创建订阅
                    try:
                        type_class = msg_converter_manager.search_class(types[0].replace("/", "."))
                        if type_class is None:
                            self.lab_logger().error(f"[Host Node] Invalid type {types[0]} for {topic}")
                        else:
                            self.create_subscription(
                                type_class,
                                topic,
                                lambda msg, d=device_id, p=property_name: self.property_callback(msg, d, p),
                                1,
                                callback_group=self.callback_group,
                            )
                            # 标记为已订阅
                            self._subscribed_topics.add(topic)
                            self.lab_logger().trace(f"[Host Node] Subscribed to new topic: {topic}")
                    except (NameError, SyntaxError) as e:
                        self.lab_logger().error(f"[Host Node] Failed to create subscription for topic {topic}: {e}")

    """设备相关"""

    def property_callback(self, msg, device_id: str, property_name: str) -> None:
        """
        更新设备状态字典中的属性值，并发送到桥接器。

        Args:
            msg: 接收到的消息
            device_id: 设备ID
            property_name: 属性名称
        """
        # 更新设备状态字典
        if hasattr(msg, "data"):
            bChange = False
            bCreate = False
            if isinstance(msg.data, (float, int, str)):
                if property_name not in self.device_status[device_id]:
                    bCreate = True
                    bChange = True
                    self.device_status[device_id][property_name] = msg.data
                elif self.device_status[device_id][property_name] != msg.data:
                    bChange = True
                    self.device_status[device_id][property_name] = msg.data
                # 更新时间戳
                self.device_status_timestamps[device_id][property_name] = time.time()
            else:
                self.lab_logger().debug(
                    f"[Host Node] Unsupported data type for {device_id}/{property_name}: {type(msg.data)}"
                )

            # 所有 Bridge 对象都应具有 publish_device_status 方法；都会收到设备状态更新
            if bChange:
                for bridge in self.bridges:
                    if hasattr(bridge, "publish_device_status"):
                        bridge.publish_device_status(self.device_status, device_id, property_name)
                        if bCreate:
                            self.lab_logger().trace(f"Status created: {device_id}.{property_name} = {msg.data}")
                        else:
                            self.lab_logger().trace(f"Status updated: {device_id}.{property_name} = {msg.data}")

    def send_goal(
        self,
        item: "QueueItem",
        action_type: str,
        action_kwargs: Dict[str, Any],
        sample_material: Dict[str, str],
        server_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        向设备发送目标请求

        Args:
            action_type: 动作类型
            action_kwargs: 动作参数
            server_info: 服务器发送信息，包含发送时间戳等
        """
        with self._goal_state_lock:
            if item.job_id in self._canceled_jobs:
                self.lab_logger().info(
                    f"[Host Node] Skip canceled goal {item.job_id[:8]}"
                )
                return
        u = uuid.UUID(item.job_id)
        device_id = item.device_id
        action_name = item.action_name
        if BasicConfig.test_mode:
            action_id = f"/devices/{device_id}/{action_name}"
            self.lab_logger().info(
                f"[TEST MODE] 模拟执行: {action_id} (job={item.job_id[:8]}), 参数: {str(action_kwargs)[:500]}"
            )
            # 根据注册表 handles 构建模拟返回值
            mock_return = self._build_test_mode_return(device_id, action_name, action_kwargs)
            self._handle_test_mode_result(item, action_id, mock_return)
            return

        if action_type.startswith("UniLabJsonCommand"):
            if action_name.startswith("auto-"):
                action_name = action_name[5:]
            action_id = f"/devices/{device_id}/_execute_driver_command"
            json_command: Dict[str, Any] = {
                "function_name": action_name,
                "function_args": action_kwargs,
                JSON_UNILABOS_PARAM: {
                    PARAM_SAMPLE_UUIDS: sample_material,
                },
            }
            action_kwargs = {"string": json.dumps(json_command)}
            if action_type.startswith("UniLabJsonCommandAsync"):
                action_id = f"/devices/{device_id}/_execute_driver_command_async"
        else:
            action_id = f"/devices/{device_id}/{action_name}"
        if action_name == "test_latency" and server_info is not None:
            self.server_latest_timestamp = server_info.get("send_timestamp", 0.0)
        if action_id not in self._action_clients:
            raise ValueError(f"ActionClient {action_id} not found.")

        self._inflight_goal_jobs.add(item.job_id)
        try:
            action_client: ActionClient = self._action_clients[action_id]
            goal_msg = convert_to_ros_msg(action_client._action_type.Goal(), action_kwargs)

            # self.lab_logger().trace(f"[Host Node] Sending goal for {action_id}: {str(goal_msg)[:1000]}")
            self.lab_logger().trace(f"[Host Node] Sending goal for {action_id}: {action_kwargs}")
            self.lab_logger().trace(f"[Host Node] Sending goal for {action_id}: {goal_msg}")
            action_client.wait_for_server()
            goal_uuid_obj = UUID(uuid=list(u.bytes))

            future = action_client.send_goal_async(
                goal_msg,
                feedback_callback=lambda feedback_msg: self.feedback_callback(
                    item,
                    action_id,
                    feedback_msg,
                ),
                goal_uuid=goal_uuid_obj,
            )
        except Exception:
            self._inflight_goal_jobs.discard(item.job_id)
            raise
        future.add_done_callback(
            lambda f: self.goal_response_callback(
                item,
                action_id,
                f,
            )
        )

    def _build_test_mode_return(
        self, device_id: str, action_name: str, action_kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        根据注册表 handles 的 output 定义构建测试模式的模拟返回值

        根据 data_key 中 @flatten 的层数决定嵌套数组层数，叶子值为空字典。
        例如: "vessel" → {}, "plate.@flatten" → [{}], "a.@flatten.@flatten" → [[{}]]
        """
        mock_return: Dict[str, Any] = {"test_mode": True, "action_name": action_name}
        action_mappings = self._action_value_mappings.get(device_id, {})
        action_mapping = action_mappings.get(action_name, {})
        handles = action_mapping.get("handles", {})
        if isinstance(handles, dict):
            for output_handle in handles.get("output", []):
                data_key = output_handle.get("data_key", "")
                handler_key = output_handle.get("handler_key", "")
                # 根据 @flatten 层数构建嵌套数组，叶子为空字典
                flatten_count = data_key.count("@flatten")
                value: Any = {}
                for _ in range(flatten_count):
                    value = [value]
                mock_return[handler_key] = value
        return mock_return

    def _handle_test_mode_result(
        self, item: "QueueItem", action_id: str, mock_return: Dict[str, Any]
    ) -> None:
        """
        测试模式下直接构建结果并走正常的结果回调流程（跳过 ROS）
        """
        job_id = item.job_id
        status = "success"
        return_info = serialize_result_info("", True, mock_return)

        self.lab_logger().info(f"[TEST MODE] Result for {action_id} ({job_id[:8]}): {status}")

        for bridge in execution_result_bridges(self.bridges):
            if hasattr(bridge, "publish_job_status"):
                bridge.publish_job_status(mock_return, item, status, return_info)
        self._inflight_goal_jobs.discard(job_id)

    def _publish_terminal_result(
        self,
        item: "QueueItem",
        status: str,
        return_info: Dict[str, Any],
        result_data: Dict[str, Any],
    ) -> None:
        """清理 ROS Goal 状态，并把原始终态交给微后端。"""

        job_id = item.job_id
        self._goals.pop(job_id, None)
        self._inflight_goal_jobs.discard(job_id)
        with self._goal_state_lock:
            self._canceled_jobs.discard(job_id)

        for bridge in execution_result_bridges(self.bridges):
            if hasattr(bridge, "publish_job_status"):
                bridge.publish_job_status(result_data, item, status, return_info)

    def goal_response_callback(
        self,
        item: "QueueItem",
        action_id: str,
        future,
    ) -> None:
        """目标响应回调"""
        self._inflight_goal_jobs.discard(item.job_id)
        try:
            goal_handle = future.result()
        except Exception as ex:  # noqa: BLE001 - 转成 job 终态失败
            self.lab_logger().error(
                f"[Host Node] Goal {item.action_name} ({item.job_id}) response failed: {ex}"
            )
            self._publish_terminal_result(
                item,
                "failed",
                serialize_result_info(traceback.format_exc(), False, {}),
                {},
            )
            return
        if not goal_handle.accepted:
            self.lab_logger().warning(f"[Host Node] Goal {item.action_name} ({item.job_id}) rejected")
            self._publish_terminal_result(
                item,
                "failed",
                serialize_result_info("Goal was rejected", False, {}),
                {},
            )
            return

        self.lab_logger().info(f"[Host Node] Goal {action_id} ({item.job_id}) accepted")
        self._goals[item.job_id] = goal_handle
        goal_future = goal_handle.get_result_async()
        goal_future.add_done_callback(
            lambda f: self.get_result_callback(
                item,
                action_id,
                f,
            )
        )
        with self._goal_state_lock:
            canceled = item.job_id in self._canceled_jobs
        if canceled:
            self.lab_logger().info(
                f"[Host Node] Goal {item.job_id[:8]} accepted after cancel; cancel immediately"
            )
            self._request_goal_cancel(item.job_id, goal_handle)
            return
        goal_future.result()

    def feedback_callback(self, item: "QueueItem", action_id: str, feedback_msg) -> None:
        """反馈回调"""
        feedback_data = convert_from_ros_msg(feedback_msg)
        feedback_data.pop("goal_id")
        self.lab_logger().trace(f"[Host Node] Feedback for {action_id} ({item.job_id}): {feedback_data}")

        for bridge in execution_result_bridges(self.bridges):
            if hasattr(bridge, "publish_job_status"):
                bridge.publish_job_status(feedback_data, item, "running")

    def get_result_callback(
        self,
        item: "QueueItem",
        action_id: str,
        future,
    ) -> None:
        """获取结果回调"""
        job_id = item.job_id

        try:
            result = future.result()
            result_msg = result.result
            goal_status = result.status
            with self._goal_state_lock:
                cancel_requested = job_id in self._canceled_jobs

            # 检查是否是被取消的任务
            if cancel_requested or goal_status == GoalStatus.STATUS_CANCELED:
                self.lab_logger().info(f"[Host Node] Goal {action_id} ({job_id[:8]}) was cancelled")
                status = "failed"
                return_info = serialize_result_info(
                    "Job was cancelled",
                    False,
                    {},
                    suc_type=SUCCESS_TYPE_CANCELLATION,
                )
            else:
                result_data = convert_from_ros_msg(result_msg)
                status = "success"
                return_info_str = result_data.get("return_info")
                if return_info_str is not None:
                    try:
                        return_info = json.loads(return_info_str)
                        # 适配后端的一些额外处理
                        return_value = return_info.get("return_value")
                        if isinstance(return_value, dict):
                            unilabos_samples = return_value.pop(RETURN_UNILABOS_SAMPLES, None)
                            if isinstance(unilabos_samples, list) and unilabos_samples:
                                self.lab_logger().info(
                                    f"[Host Node] Job {job_id[:8]} returned {len(unilabos_samples)} sample(s): "
                                    f"{[s.get('name', s.get('id', 'unknown')) if isinstance(s, dict) else str(s)[:20] for s in unilabos_samples[:5]]}"
                                    f"{'...' if len(unilabos_samples) > 5 else ''}"
                                )
                                return_info["samples"] = unilabos_samples
                        suc = return_info.get("suc", False)
                        if not suc:
                            status = "failed"
                    except json.JSONDecodeError:
                        status = "failed"
                        return_info = serialize_result_info("", False, result_data)
                        self.lab_logger().critical("错误的return_info类型，请断点修复")
                else:
                    # 无 return_info 字段时，回退到 success 字段（若存在）
                    suc_field = result_data.get("success")
                    if isinstance(suc_field, bool):
                        status = "success" if suc_field else "failed"
                        return_info = serialize_result_info("", suc_field, result_data)
                    else:
                        # 最保守的回退：标记失败并返回空JSON
                        status = "failed"
                        return_info = serialize_result_info("缺少return_info", False, result_data)

            terminal_result_data = (
                {} if cancel_requested or goal_status == GoalStatus.STATUS_CANCELED else result_data
            )
            self.lab_logger().info(f"[Host Node] Result for {action_id} ({job_id[:8]}): {status}")
            if not cancel_requested and goal_status != GoalStatus.STATUS_CANCELED:
                self.lab_logger().trace(f"[Host Node] Result data: {result_data}")
            self._publish_terminal_result(
                item,
                status,
                return_info,
                terminal_result_data,
            )

        except Exception as e:
            self.lab_logger().error(
                f"[Host Node] Error in get_result_callback for {action_id} ({job_id[:8]}): {str(e)}"
            )
            import traceback

            self.lab_logger().error(traceback.format_exc())

            self._publish_terminal_result(
                item,
                "failed",
                serialize_result_info(f"Callback error: {str(e)}", False, {}),
                {},
            )

    def _request_goal_cancel(self, job_id: str, goal_handle: Any) -> None:
        """向已受理的 ROS Goal 发起取消。"""

        cancel_future = goal_handle.cancel_goal_async()
        cancel_future.add_done_callback(
            lambda future: self._cancel_goal_callback(job_id, future)
        )

    def cancel_job(self, job_id: str) -> bool:
        """取消运行中或仍在等待 ROS Goal 响应的执行。"""

        with self._goal_state_lock:
            goal_handle = self._goals.get(job_id)
            is_inflight = job_id in self._inflight_goal_jobs
            if goal_handle is None and not is_inflight:
                self.lab_logger().warning(
                    f"[Host Node] Job {job_id[:8]} not found, cannot cancel"
                )
                return False
            self._canceled_jobs.add(job_id)

        if goal_handle is not None:
            self.lab_logger().info(f"[Host Node] Cancelling goal {job_id[:8]}")
            self._request_goal_cancel(job_id, goal_handle)
        else:
            self.lab_logger().info(
                f"[Host Node] Marked in-flight goal {job_id[:8]} canceled before acceptance"
            )
        return True

    def cancel_goal(self, goal_uuid: str) -> bool:
        """兼容旧接口；统一走 ROS2 transport 的取消逻辑。"""

        return self.cancel_job(goal_uuid)

    def _cancel_goal_callback(self, goal_uuid: str, future) -> None:
        """取消目标的回调"""
        try:
            cancel_response = future.result()
            if cancel_response.goals_canceling:
                self.lab_logger().info(f"[Host Node] Goal {goal_uuid[:8]} cancel request accepted")
            else:
                self.lab_logger().warning(f"[Host Node] Goal {goal_uuid[:8]} cancel request rejected")
        except Exception as e:
            self.lab_logger().error(f"[Host Node] Error cancelling goal {goal_uuid[:8]}: {str(e)}")
            import traceback

            self.lab_logger().error(traceback.format_exc())

    def get_goal_status(self, job_id: str) -> int:
        """获取目标状态"""
        if job_id in self._goals:
            g = self._goals[job_id]
            status = g.status
            self.lab_logger().debug(f"[Host Node] Goal status for {job_id}: {status}")
            return status
        if job_id in self._inflight_goal_jobs:
            return GoalStatus.STATUS_EXECUTING
        self.lab_logger().warning(f"[Host Node] Goal {job_id} not found, status unknown")
        return GoalStatus.STATUS_UNKNOWN

    """Controller Node"""

    def initialize_controller(self, controller_id: str, controller_config: Dict[str, Any]) -> None:
        """
        初始化控制器

        Args:
            controller_id: 控制器ID
            controller_config: 控制器配置
        """
        self.lab_logger().info(f"[Host Node] Initializing controller: {controller_id}")

        class_name = controller_config.pop("type")
        controller_func = globals()[class_name]

        for input_name, input_info in controller_config["inputs"].items():
            controller_config["inputs"][input_name]["type"] = get_msg_type(eval(input_info["type"]))
        for output_name, output_info in controller_config["outputs"].items():
            controller_config["outputs"][output_name]["type"] = get_msg_type(eval(output_info["type"]))

        if controller_config["parameters"] is None:
            controller_config["parameters"] = {}

        ControllerNode(
            controller_id,
            controller_func=controller_func,
            **controller_config,
        )
        self.lab_logger().info(f"[Host Node] Controller {controller_id} created.")
        # rclpy.get_global_executor().add_node(controller)

    """Resource"""

    def _init_host_service(self):
        # 物料链路不再提供 ROS service：查询/创建/更新/删除统一经 materials.*
        # 工具函数直连微后端权威（Slave 经 HostLink 代理），开机对齐走 materials.ensure。
        self._resource_services: Dict[str, Service] = {
            "node_info_update": self.create_service(
                SerialCommand,
                "/node_info_update",
                self._node_info_update_callback,
                callback_group=self.callback_group,
            ),
        }

    def _node_info_update_callback(self, request, response):
        """
        更新节点信息回调

        处理两种消息:
        1. 首次上报(main_slave_run): 带 devices_config + registry_config,存储 action_value_mappings
        2. 设备重注册(SYNC_SLAVE_NODE_INFO): 带 edge_device_id + registry_name,用 registry_name 索引已存储的 mappings
        """
        self.lab_logger().trace(f"[Host Node] Node info update request received: {request}")
        try:
            info = json.loads(request.command)
            if "SYNC_SLAVE_NODE_INFO" in info:
                info = info["SYNC_SLAVE_NODE_INFO"]
                machine_name = info["machine_name"]
                edge_device_id = info["edge_device_id"]
                registry_name = info.get("registry_name", "")
                self.device_machine_names[edge_device_id] = machine_name

                # 用 registry_name 索引已存储的 registry_config,获取 action_value_mappings
                if registry_name and registry_name in self._slave_registry_configs:
                    action_mappings = (
                        self._slave_registry_configs[registry_name].get("class", {}).get("action_value_mappings", {})
                    )
                    if action_mappings:
                        self._action_value_mappings[edge_device_id] = action_mappings
                        self.lab_logger().info(
                            f"[Host Node] Loaded {len(action_mappings)} action mappings "
                            f"for remote device {edge_device_id} (registry: {registry_name})"
                        )
            else:
                devices_config = info.pop("devices_config")
                registry_config = info.pop("registry_config")
                if registry_config:
                    # 存储 slave 的 registry_config,用于后续 SYNC_SLAVE_NODE_INFO 索引
                    for reg_name, reg_data in registry_config.items():
                        if isinstance(reg_data, dict) and "class" in reg_data:
                            self._slave_registry_configs[reg_name] = reg_data

                # 解析 devices_config,建立 device_id -> action_value_mappings 映射
                if devices_config:
                    machine_name = info["machine_name"]
                    # Stamp machine_name on each device dict before parsing
                    for device_tree in devices_config:
                        for device_dict in device_tree:
                            device_dict["machine_name"] = machine_name
                            device_id = device_dict.get("id", "")
                            class_name = device_dict.get("class", "")
                            if device_id and class_name and class_name in self._slave_registry_configs:
                                action_mappings = (
                                    self._slave_registry_configs[class_name]
                                    .get("class", {})
                                    .get("action_value_mappings", {})
                                )
                                if action_mappings:
                                    self._action_value_mappings[device_id] = action_mappings
                                    self.lab_logger().info(
                                        f"[Host Node] Stored {len(action_mappings)} action mappings "
                                        f"for remote device {device_id} (class: {class_name})"
                                    )

                    # Merge slave devices_config into self.devices_config tree
                    try:
                        slave_tree_set = ResourceTreeSet.load(devices_config)  # slave一定是根节点的tree
                        for tree in slave_tree_set.trees:
                            self.devices_config.trees.append(tree)
                        self.lab_logger().info(
                            f"[Host Node] Merged {len(slave_tree_set.trees)} slave device trees "
                            f"(machine: {machine_name}) into devices_config"
                        )
                    except Exception as e:
                        self.lab_logger().error(f"[Host Node] Failed to merge slave devices_config: {e}")

            self.lab_logger().debug(f"[Host Node] Node info update: {info}")
            # slave 的 action_value_mappings 已更新，刷新 runtime.v1 能力快照
            self._notify_capabilities_changed()
            response.response = "OK"
        except Exception as e:
            self.lab_logger().error(f"[Host Node] Error updating node info: {e.args}")
            response.response = "ERROR"
        return response

    def test_latency(self) -> TestLatencyReturn:
        """
        测试网络延迟的action实现
        通过5次ping-pong机制校对时间误差并计算实际延迟

        Returns:
            TestLatencyReturn: 包含延迟测试结果的字典，包括：
                - avg_rtt_ms: 平均往返时间（毫秒）
                - avg_time_diff_ms: 平均时间差（毫秒）
                - max_time_error_ms: 最大时间误差（毫秒）
                - task_delay_ms: 实际任务延迟（毫秒），-1表示无法计算
                - raw_delay_ms: 原始时间差（毫秒），-1表示无法计算
                - test_count: 有效测试次数
                - status: 测试状态，"success"表示成功，"all_timeout"表示全部超时
        """
        import uuid as uuid_module

        self.lab_logger().info("=" * 60)
        self.lab_logger().info("开始网络延迟测试...")

        # 记录任务开始执行的时间
        task_start_time = time.time()

        # 进行5次ping-pong测试
        ping_results = []

        for i in range(5):
            self.lab_logger().info(f"第{i+1}/5次ping-pong测试...")

            # 生成唯一的ping ID
            ping_id = str(uuid_module.uuid4())

            # 记录发送时间
            send_timestamp = time.time()

            # 发送ping
            from unilabos.server.backend.session import get_backend_client

            comm_client = get_backend_client()
            comm_client.send_ping(ping_id, send_timestamp)

            # 等待pong响应
            timeout = 10.0
            start_wait_time = time.time()

            while time.time() - start_wait_time < timeout:
                with self._ping_lock:
                    if ping_id in self._ping_responses:
                        pong_data = self._ping_responses.pop(ping_id)
                        break
                time.sleep(0.001)
            else:
                self.lab_logger().error(f"❌ 第{i+1}次测试超时")
                continue

            # 计算本次测试结果
            receive_timestamp = time.time()
            server_timestamp = pong_data["server_timestamp"]

            # 往返时间
            rtt_ms = (receive_timestamp - send_timestamp) * 1000

            # 客户端与服务端时间差（客户端时间 - 服务端时间）
            # 假设网络延迟对称，取中间点的服务端时间
            mid_point_time = send_timestamp + (receive_timestamp - send_timestamp) / 2
            time_diff_ms = (mid_point_time - server_timestamp) * 1000

            ping_results.append({"rtt_ms": rtt_ms, "time_diff_ms": time_diff_ms})

            self.lab_logger().info(f"✅ 第{i+1}次: 往返时间={rtt_ms:.2f}ms, 时间差={time_diff_ms:.2f}ms")

            time.sleep(0.1)

        if not ping_results:
            self.lab_logger().error("❌ 所有ping-pong测试都失败了")
            return {
                "avg_rtt_ms": -1.0,
                "avg_time_diff_ms": -1.0,
                "max_time_error_ms": -1.0,
                "task_delay_ms": -1.0,
                "raw_delay_ms": -1.0,
                "test_count": 0,
                "status": "all_timeout",
            }

        # 统计分析
        rtts = [r["rtt_ms"] for r in ping_results]
        time_diffs = [r["time_diff_ms"] for r in ping_results]

        avg_rtt_ms = sum(rtts) / len(rtts)
        avg_time_diff_ms = sum(time_diffs) / len(time_diffs)
        max_time_diff_error_ms: float = max(abs(min(time_diffs)), abs(max(time_diffs)))

        self.lab_logger().info("-" * 50)
        self.lab_logger().info("[测试统计]")
        self.lab_logger().info(f"有效测试次数: {len(ping_results)}/5")
        self.lab_logger().info(f"平均往返时间: {avg_rtt_ms:.2f}ms")
        self.lab_logger().info(f"平均时间差: {avg_time_diff_ms:.2f}ms")
        self.lab_logger().info(f"时间差范围: {min(time_diffs):.2f}ms ~ {max(time_diffs):.2f}ms")
        self.lab_logger().info(f"最大时间误差: ±{max_time_diff_error_ms:.2f}ms")

        # 计算任务执行延迟
        if hasattr(self, "server_latest_timestamp") and self.server_latest_timestamp > 0:
            self.lab_logger().info("-" * 50)
            self.lab_logger().info("[任务执行延迟分析]")
            self.lab_logger().info(f"服务端任务下发时间: {self.server_latest_timestamp:.6f}")
            self.lab_logger().info(f"客户端任务开始时间: {task_start_time:.6f}")

            # 原始时间差（不考虑时间同步误差）
            raw_delay_ms = (task_start_time - self.server_latest_timestamp) * 1000

            # 考虑时间同步误差后的延迟（用平均时间差校正）
            corrected_delay_ms = raw_delay_ms - avg_time_diff_ms

            self.lab_logger().info(f"📊 原始时间差: {raw_delay_ms:.2f}ms")
            self.lab_logger().info(f"🔧 时间同步校正: {avg_time_diff_ms:.2f}ms")
            self.lab_logger().info(f"⏰ 实际任务延迟: {corrected_delay_ms:.2f}ms")
            self.lab_logger().info(f"📏 误差范围: ±{max_time_diff_error_ms:.2f}ms")

            # 给出延迟范围
            min_delay = corrected_delay_ms - max_time_diff_error_ms
            max_delay = corrected_delay_ms + max_time_diff_error_ms
            self.lab_logger().info(f"📋 延迟范围: {min_delay:.2f}ms ~ {max_delay:.2f}ms")

        else:
            self.lab_logger().warning("⚠️ 无法获取服务端任务下发时间，跳过任务延迟分析")
            raw_delay_ms = -1
            corrected_delay_ms = -1

        self.lab_logger().info("=" * 60)

        res: TestLatencyReturn = {
            "avg_rtt_ms": avg_rtt_ms,
            "avg_time_diff_ms": avg_time_diff_ms,
            "max_time_error_ms": max_time_diff_error_ms,
            "task_delay_ms": corrected_delay_ms if corrected_delay_ms > 0 else -1,
            "raw_delay_ms": (
                raw_delay_ms if hasattr(self, "server_latest_timestamp") and self.server_latest_timestamp > 0 else -1
            ),
            "test_count": len(ping_results),
            "status": "success",
        }
        return res

    @action(always_free=True, node_type=NodeType.MANUAL_CONFIRM, placeholder_keys={
        "assignee_user_ids": PLACEHOLDER_MANUAL_CONFIRM
    }, goal_default={
        "timeout_seconds": 3600,
        "assignee_user_ids": []
    })
    def manual_confirm(self, timeout_seconds: int, assignee_user_ids: list[str], **kwargs) -> dict:
        """
        timeout_seconds: 超时时间（秒），默认3600秒
        修改的结果无效，是只读的
        """
        return kwargs

    @action(
        description="申请扣减物料并挂载（接收服务端已扣减的单个根物料，挂载到目标设备的目标物料上）",
        always_free=True,
        placeholder_keys={
            "resource": PLACEHOLDER_DEDUCT_RESOURCE,
            "device_id": PLACEHOLDER_DEVICES,
            "mount_resource": PLACEHOLDER_NODES,
        },
        handles=[
            ActionInputHandle(
                key="device_id",
                data_type="device_id",
                label="目标设备",
                data_key="device_id",
                data_source=DataSource.HANDLE,
            ),
            ActionInputHandle(
                key="mount_resource",
                data_type="resource",
                label="挂载目标",
                data_key="mount_resource",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="labware",
                data_type="resource",
                label="物料创建结果",
                data_key="created_resource_tree.@flatten",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="mount_resource",
                data_type="resource",
                label="挂载目标",
                data_key="mount_resource.@flatten",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    async def apply_deduct_resource(
        self,
        resource: ResourceSlot = None,
        registry_class: str = "",
        material_name: str = "",
        device_id: DeviceSlot = "",
        mount_resource: ResourceSlot = None,
        bind_locations: Point = None,
        slot_on_deck: str = "",
    ) -> DeductResourceReturn:
        """
        出库物料：物料进入系统的统一入口——创建/落权威，并可选挂载到目标设备的目标物料上。

        出库产物两种来源（二选一）：
        - resource：已带 uuid 的扣减产物（云端仓储扣减 / 前端 instantiate 出库端点的产物引用），
          经 materials.ensure 落权威（缺失时以原 uuid adopt 创建，已存在则采用权威记录）。
        - registry_class（+ material_name）：按 registry 资源类名现场创建全新物料
          （materials.create 权威发号，与 instantiate 端点同款语义）。

        与 transfer_resource 同构：resource / mount_resource 均为**单个物料**
        （单 ResourceSlot）。框架在 send_goal 把以下两种入参形态解析为单个 PLR 实例：
        - list：一棵树的扁平节点组（上游 handle 的 @flatten）→ 装配成一个物料（这一组必须只有一个根）。
        - dict：资源引用 → 按 uuid with_children 拉取一个物料。

        两种用法：
        - 仅登记/透传（不传 mount_resource）：把出库物料经 labware 输出，
          方便后续 set_substance 设置内容物、再由 transfer_resource 派发。
        - 出库并挂载（给 mount_resource）：下行 RESOURCE_APPEND 请求设备
          append_resource——设备按 uuid 从微后端权威拉取实例化 → 实际 assign → "update" 快照回报
          （相当于从仓库放到仓储设备上；设备侧只做投影挂载）。目标设备缺省自动推断
          （materials.owner_device_of：挂载目标所在根树的归属）；device_id 仅作显式覆盖。

        输出 handle：labware = 出库/挂载得到的物料树；mount_resource = 实际挂载到的目标物料树
        （未挂载时为空），便于下游节点继续引用挂载位置。

        Args:
            resource[扣减物料]: 已扣减的单个根物料（可选；与 registry_class 二选一，dict/list 两形态均解析为一个物料）。
            registry_class[资源类]: registry 资源类 id（可选；与 resource 二选一，按类名现场创建全新物料）。
            material_name[实例名]: 现场创建时的实例名（配合 registry_class）。
            device_id[目标设备]: 挂载到的边缘设备 id（可选；缺省由 mount_resource 自动推断，仅作显式覆盖）。
            mount_resource[挂载目标]: 实际挂载到的单个目标物料/父节点（可选；不传则仅登记/透传，可由图 handle 连入，dict/list 两形态）。
            bind_locations[挂载位置]: 挂载目标坐标系下的挂载坐标（挂载时使用）。
            slot_on_deck[Deck槽位]: 挂载目标槽位，label 或 0-based 数字索引（如 "A1"/"0"，可选）。
        """
        return await host_material_actions.deduct_resource(
            self,
            self._material_dispatch,
            resource,
            registry_class=registry_class,
            material_name=material_name,
            device_id=device_id,
            mount_resource=mount_resource,
            bind_locations=bind_locations,
            slot_on_deck=slot_on_deck,
        )

    @action(
        description="设置物料内容物（液体/固体，默认单位 微升/微克）；接收单个物料，设置后输出",
        always_free=True,
        materials_need_lock=["resource"],
        placeholder_keys={"resource": PLACEHOLDER_DEDUCT_REAGENT},
        handles=[
            ActionInputHandle(
                key="resource",
                data_type="resource",
                label="目标物料",
                data_key="resource",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="resource",
                data_type="resource",
                label="目标物料",
                data_key="resource",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    async def set_substance(
        self,
        resource: ResourceSlot,
        substance_names: List[str],
        amounts: List[float],
        slots: List[str] = [],
        is_solid: List[bool] = [],
    ) -> dict:
        """
        设置单个物料的内容物（液体或固体）。

        接收的物料必须是单个，且为以下之一：
        - container：直接设置在自身的 tracker 上；
        - well（带标号的容器）：同样设置在自身；
        - carrier / plate 带 container：按 slots 设置在对应子容器的 tracker 上（支持 tracker 输入）。

        设置目标只有两种：物料自身，或物料下面 children 的孔位。由 slots 区分（空=自身）。
        单位固定默认：固体=微克(ug)、液体=微升(ul)，由 is_solid 区分（unilab 定制 PLR 的
        set_liquids 仅支持 ul/ug）。底层走 set_liquids 三元组 (名称, 量, 单位)。

        Args:
            resource[目标物料]: 单个物料（container / well / 带子容器的 carrier|plate）。
            substance_names[物质名称]: 每个目标的物质名（液体名或固体名）。
            amounts[用量]: 每个目标的用量（液体=体积/微升，固体=质量/微克）。
            slots[子孔位]: 子孔位 id/索引；为空=设在物料自身，非空=设在对应子容器。
            is_solid[是否固体]: 每个目标是否固体（可选，缺省按液体处理；决定单位 ug/ul）。
        """
        return await host_material_actions.set_substance(
            self, resource, substance_names, amounts, slots=slots, is_solid=is_solid
        )

    @action(
        description="废弃台面物料（指定设备 + uuid：云端销毁并通知该设备本地移除）",
        always_free=True,
        materials_need_lock=["resource"],
        placeholder_keys={
            "resource": PLACEHOLDER_NODES,
            "device_id": PLACEHOLDER_DEVICES,
        },
        handles=[
            ActionInputHandle(
                key="device_id",
                data_type="device_id",
                label="所属设备",
                data_key="device_id",
                data_source=DataSource.HANDLE,
            ),
            ActionInputHandle(
                key="resource",
                data_type="resource",
                label="废弃物料",
                data_key="resource",
                data_source=DataSource.HANDLE,
            ),
        ],
    )
    async def discard_resource(self, resource: ResourceSlot, device_id: DeviceSlot = "") -> dict:
        """
        废弃单个台面物料。

        与 apply_deduct_resource 对称（扣减→挂载到设备 / 废弃→从设备移除并销毁）：接收单个
        已存在物料（前端用节点选择器选择，或图 handle 传入，框架在 send_goal 已解析为 PLR
        实例），先由 MaterialsService 权威执行销毁，成功后再通知所属边缘设备本地移除该
        物料。物料被销毁后无图输出 handle。

        所属设备缺省自动推断（materials.owner_device_of：物料所在权威根树的归属登记）；
        显式传 device_id 可覆盖。

        Args:
            resource[废弃物料]: 要废弃的单个台面物料（须带 unilabos_uuid）。
            device_id[所属设备]: 物料所在的边缘设备 id（可选；缺省自动推断）。
        """
        return await host_material_actions.discard_resource(
            self, self._material_dispatch, resource, device_id
        )

    @action(
        description="转移物料（系统派发）：把已物理就位的物料在系统中改挂到目标设备的目标孔位（人工/机械臂工作流的统一末步）",
        always_free=True,
        materials_need_lock=["resource", "mount_resource"],
        placeholder_keys={
            "target_device": PLACEHOLDER_DEVICES,
            "mount_resource": PLACEHOLDER_NODES,
        },
        handles=[
            ActionInputHandle(
                key="resource",
                data_type="resource",
                label="待转移物料",
                data_key="resource",
                data_source=DataSource.HANDLE,
            ),
            ActionInputHandle(
                key="target_device",
                data_type="device_id",
                label="目标设备",
                data_key="target_device",
                data_source=DataSource.HANDLE,
            ),
            ActionInputHandle(
                key="mount_resource",
                data_type="resource",
                label="目标孔位",
                data_key="mount_resource",
                data_source=DataSource.HANDLE,
            ),
            ActionInputHandle(
                key="site",
                data_type="site",
                label="目标槽位",
                data_key="site",
                data_source=DataSource.HANDLE,
            ),
            ActionOutputHandle(
                key="resource",
                data_type="resource",
                label="已转移物料",
                data_key="resource.@flatten",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="mount_resource",
                data_type="resource",
                label="目标孔位",
                data_key="mount_resource.@flatten",
                data_source=DataSource.EXECUTOR,
            ),
            ActionOutputHandle(
                key="site",
                data_type="site",
                label="目标槽位",
                data_key="site",
                data_source=DataSource.EXECUTOR,
            ),
        ],
    )
    async def transfer_resource(
        self,
        resource: ResourceSlot,
        mount_resource: ResourceSlot = None,
        site: SiteSlot = "",
        target_device: DeviceSlot = "",
    ) -> TransferResourceReturn:
        """
        转移物料到目标物料的孔位（系统记账，不含物理搬运）。物理搬运由前序节点保证：
        - 人工：apply_deduct_resource → manual_confirm（人工搬运到位）→ transfer_resource
        - 机械臂：apply_deduct_resource → 机械臂 pick → 机械臂 place → transfer_resource

        只需给物料与目标物料，两端设备自动推断（materials.owner_device_of：权威根树
        归属登记）——来源设备 = 物料当前所在根树的归属（unload 通知发给真实持有者），
        目标设备 = 目标物料所在根树的归属；target_device 仅作显式覆盖。

        与 apply_deduct_resource 同构：resource / mount_resource 均为**单个物料**（单 ResourceSlot）。
        单物料有两种入参形态，框架在 send_goal 自动解析为一个 PLR 实例：
        - list：一棵树的扁平节点组（上游 handle 的 @flatten）→ 装配成一个物料（这一组必须只有一个根）。
        - dict：资源引用 → 按 uuid with_children 拉取一个物料。

        Args:
            resource[待转移物料]: 待转移的单个物料（须带 unilabos_uuid，可由图 handle 连入，list/dict 两形态）。
            mount_resource[目标孔位]: 目标物料——单个挂载孔位/父物料（list/dict 两形态）。
            site[目标槽位]: 目标父级上的 Site（SiteSlot：前端选择器提交权威 ResourceSite 的 uuid，
                兼容 label/index 便捷形态）；不传则由父级默认排布。
            target_device[目标设备]: 可选覆盖；缺省由 mount_resource 自动推断。
        """
        return await host_material_actions.transfer_resource(
            self, resource, mount_resource, site, target_device
        )

    def test_resource(
        self,
        sample_uuids: SampleUUIDsType,
        resource: ResourceSlot = None,
        resources: List[ResourceSlot] = None,
        device: DeviceSlot = None,
        devices: List[DeviceSlot] = None,
    ) -> TestResourceReturn:
        if resources is None:
            resources = []
        if devices is None:
            devices = []
        if resource is None:
            resource = RegularContainer("test_resource传入None")
        return {
            "resources": ResourceTreeSet.from_plr_resources([resource, *resources]).dump(),
            "devices": [device, *devices],
            "unilabos_samples": [LabSample(sample_uuid=sample_uuid, oss_path="", extra={"material_uuid": content} if isinstance(content, str) else content.serialize()) for sample_uuid, content in sample_uuids.items()]
        }

    def handle_pong_response(self, pong_data: dict):
        """
        处理pong响应
        """
        ping_id = pong_data.get("ping_id")
        if ping_id:
            with self._ping_lock:
                self._ping_responses[ping_id] = pong_data

            # 详细信息合并为一条日志
            client_timestamp = pong_data.get("client_timestamp", 0)
            server_timestamp = pong_data.get("server_timestamp", 0)
            current_time = time.time()

            self.lab_logger().debug(
                f"📨 Pong | ID:{ping_id[:8]}.. | C→S→C: {client_timestamp:.3f}→{server_timestamp:.3f}→{current_time:.3f}"
            )
        else:
            self.lab_logger().warning("⚠️ 收到无效的Pong响应（缺少ping_id）")

    def notify_resource_tree_update(
        self, device_id: str, action: str, resource_uuid_list: List[str]
    ) -> Optional[bool]:
        """
        通知设备节点更新资源树（前端变更经微后端进来后，由 host 分发到各设备）

        物料链路不走 ROS service：本进程设备（含 host 自身）直接调度实例方法执行；
        跨机（Slave）设备经 HostLink 下行 RPC，不依赖 ROS 服务发现，保证高可用。

        Args:
            device_id: 目标设备ID
            action: 操作类型 "add", "update", "remove"
            resource_uuid_list: 资源UUIDs

        Returns:
            True if the update completed, False if it failed, None if it was intentionally skipped.
        """
        operations = [
            {
                "action": action,
                "data": list(resource_uuid_list),
            }
        ]
        try:
            # 可达性判断：设备既不在本进程、也不在 HostLink 在线表 → 有意跳过（None）
            if get_local_device_node(device_id) is None:
                server = get_hostlink_server()
                if server is None or not server.has_device(device_id):
                    self.lab_logger().info(
                        f"[Host Node-Resource] 设备 {device_id} 不在本进程、也不在 HostLink 在线表，"
                        f"跳过资源树 {action} 分发"
                    )
                    return None
            self.lab_logger().trace(
                f"[Host Node-Resource] Host -> {device_id} ResourceTree {action} operation started -------"
            )
            sync_resource_tree_to_device(device_id, operations, DEFAULT_DOWNLINK_TIMEOUT)
            self.lab_logger().trace(
                f"[Host Node-Resource] Host -> {device_id} ResourceTree {action} operation completed -------"
            )
            return True

        except Exception as e:
            self.lab_logger().error(f"[Host Node-Resource] Error notifying resource tree update: {str(e)}")
            self.lab_logger().error(traceback.format_exc())
            return False

    # ------------------------------------------------------------------
    # Device lifecycle (add / remove) — pure forwarder
    # ------------------------------------------------------------------

    def notify_device_manage(self, target_node_id: str, action: str, config: ResourceDictType) -> bool:
        """把 add/remove 设备指令分发到目标节点。

        与物料下行同构、不走 ROS service：本进程节点（含 host 自身）直调实例协程
        device_manage，跨机（Slave）经 HostLink 下行 RPC。
        """
        try:
            target = str(target_node_id).split("/")[-1]
            self.lab_logger().info(
                f"[Host Node-DeviceMgr] Dispatching {action}_device to {target}"
            )
            result = device_manage_to_device(target, action, dict(config))
            success = bool(result.get("success"))
            if success:
                self.lab_logger().info(
                    f"[Host Node-DeviceMgr] {action}_device on {target} completed"
                )
            else:
                self.lab_logger().error(
                    f"[Host Node-DeviceMgr] {action}_device on {target} failed: {result}"
                )
            return success

        except Exception as e:
            self.lab_logger().error(f"[Host Node-DeviceMgr] Error: {e}")
            self.lab_logger().error(traceback.format_exc())
            return False

    def create_device(self, device_id: str, config: ResourceDictType) -> dict:
        """Dynamically create a root-level device on the host."""
        if not device_id:
            return {"success": False, "error": "device_id required"}

        if device_id in self.devices_names:
            return {"success": False, "error": f"Device {device_id} already exists"}

        try:
            config.setdefault("id", device_id)
            config.setdefault("type", "device")
            config.setdefault("machine_name", BasicConfig.machine_name or "本地")
            res_dict = ResourceDictInstance.get_resource_instance_from_dict(config)

            self.initialize_device(device_id, res_dict)

            if device_id not in self.devices_names:
                return {"success": False, "error": f"initialize_device failed for {device_id}"}

            # Add to config tree (devices_config)
            tree = ResourceTreeInstance(res_dict)
            self.devices_config.trees.append(tree)

            # Add to resource tracker so apply_resource_tree_update can find it
            try:
                for plr_resource in ResourceTreeSet([tree]).to_plr_resources():
                    self._resource_tracker.add_resource(plr_resource)
            except Exception as ex:
                self.lab_logger().warning(f"[Host Node-DeviceMgr] PLR resource registration skipped: {ex}")

            self.lab_logger().info(f"[Host Node-DeviceMgr] Device {device_id} created successfully")
            return {"success": True, "device_id": device_id}

        except Exception as e:
            self.lab_logger().error(f"[Host Node-DeviceMgr] Failed to create {device_id}: {e}")
            self.lab_logger().error(traceback.format_exc())
            return {"success": False, "error": str(e)}

    def destroy_device(self, device_id: str) -> dict:
        """Remove a root-level device from the host."""
        if not device_id:
            return {"success": False, "error": "device_id required"}

        if device_id not in self.devices_names:
            return {"success": False, "error": f"Device {device_id} not found"}

        if device_id == self.device_id:
            return {"success": False, "error": "Cannot destroy host_node itself"}

        try:
            namespace = self.devices_names[device_id]
            device_key = f"{namespace}/{device_id}"

            # Remove action clients
            action_prefix = f"/devices/{device_id}/"
            to_remove = [k for k in self._action_clients if k.startswith(action_prefix)]
            for k in to_remove:
                try:
                    self._action_clients[k].destroy()
                except Exception:
                    pass
                del self._action_clients[k]

            # Remove from config tree (devices_config)
            self.devices_config.trees = [
                t for t in self.devices_config.trees
                if t.root_node.res_content.id != device_id
            ]

            # Remove from resource tracker
            try:
                tracked = self._resource_tracker.uuid_to_resources.copy()
                for uid, res in tracked.items():
                    res_id = res.get("id") if isinstance(res, dict) else getattr(res, "name", None)
                    if res_id == device_id:
                        self._resource_tracker.remove_resource(res)
            except Exception as ex:
                self.lab_logger().warning(f"[Host Node-DeviceMgr] Resource tracker cleanup: {ex}")

            # Clean internal state
            self._online_devices.discard(device_key)
            self.devices_names.pop(device_id, None)
            self.device_machine_names.pop(device_id, None)
            self._action_value_mappings.pop(device_id, None)

            # Destroy the ROS2 node of the device
            instance = self.devices_instances.pop(device_id, None)
            if instance is not None:
                try:
                    # noinspection PyProtectedMember
                    ros_node = getattr(instance, "_ros_node", None)
                    if ros_node is not None:
                        ros_node.destroy_node()
                except Exception as e:
                    self.lab_logger().warning(f"[Host Node-DeviceMgr] Error destroying ROS node for {device_id}: {e}")

            self.lab_logger().info(f"[Host Node-DeviceMgr] Device {device_id} destroyed")
            return {"success": True, "device_id": device_id}

        except Exception as e:
            self.lab_logger().error(f"[Host Node-DeviceMgr] Failed to destroy {device_id}: {e}")
            self.lab_logger().error(traceback.format_exc())
            return {"success": False, "error": str(e)}
