"""物料链路契约：创建只发生在微后端，host 只做挂载编排与变更分发。

下行链路（append_resource / 资源树同步）不建 ROS service：
本进程直调设备节点实例方法，跨机（Slave）经 HostLink 下行 RPC。
"""

import inspect


def test_host_node_no_longer_owns_material_creation() -> None:
    """host 不再提供 create_resource / create_resource_detailed（创建入口在微后端）。"""
    from unilabos.backend.ros2.presets.host_node import HostNode

    assert not hasattr(HostNode, "create_resource")
    assert not hasattr(HostNode, "create_resource_detailed")


def test_host_node_keeps_append_and_notify_orchestration() -> None:
    """host 保留统一下行通道（_material_dispatch）与变更分发（notify_resource_tree_update）。"""
    from unilabos.backend.ros2.presets.host_node import HostNode

    assert inspect.iscoroutinefunction(HostNode._material_dispatch)
    dispatch_source = inspect.getsource(HostNode._material_dispatch)
    assert "RESOURCE_APPEND" in dispatch_source
    assert "RESOURCE_TREE_SYNC" in dispatch_source

    notify_params = inspect.signature(HostNode.notify_resource_tree_update).parameters
    assert list(notify_params)[1:4] == ["device_id", "action", "resource_uuid_list"]


def test_material_downlink_no_longer_uses_ros_services() -> None:
    """物料下行不再走 ROS service：设备侧不注册、host 侧不调用这两个 srv 地址。"""
    from unilabos.backend.ros2 import base_device_node
    from unilabos.backend.ros2.presets import host_node

    device_source = inspect.getsource(base_device_node)
    assert "/append_resource" not in device_source
    assert "/s2c_resource_tree" not in device_source

    host_source = inspect.getsource(host_node)
    assert "/append_resource" not in host_source
    assert "/s2c_resource_tree" not in host_source


def test_device_exposes_downlink_coroutines() -> None:
    """设备侧以实例方法承接下行：挂载与资源树同步均为协程，供直调/HostLink 桥调用。

    两个方法（连同 transfer_to_new_resource 与资源树锁）定义在 backend 无关的
    DeviceNode 上，ROS 层只继承不重复实现；实现不依赖 rclpy / SerialCommand，
    HostLink backend 的设备节点同样可用。
    """
    from unilabos.backend.runtime.node import DeviceNode
    from unilabos.backend.ros2.base_device_node import BaseROS2DeviceNode

    assert inspect.iscoroutinefunction(DeviceNode.append_resource)
    assert inspect.iscoroutinefunction(DeviceNode.apply_resource_tree_update)
    assert callable(DeviceNode.transfer_to_new_resource)
    for name in ("append_resource", "apply_resource_tree_update", "transfer_to_new_resource"):
        assert name not in vars(BaseROS2DeviceNode), f"{name} 不应在 ROS 层重复实现"
    # 旧 srv 回调签名不应存在
    assert not hasattr(BaseROS2DeviceNode, "s2c_resource_tree")

    # DeviceNode 的实现不依赖 ROS：挂载后的快照上报直连权威（update_resource），
    # 不再经 host 的 /c2s_update_resource_tree srv 中转
    for method in (DeviceNode.append_resource, DeviceNode.apply_resource_tree_update):
        source = inspect.getsource(method)
        assert "rclpy" not in source
        assert "SerialCommand" not in source
        assert "c2s_update_resource_tree" not in source
        assert "update_resource" in source

    # 等待挂起用 backend 提供的 Future：DeviceNode 默认 asyncio，ROS 层覆写为 rclpy Future
    assert callable(DeviceNode.create_wait_future)
    assert "create_wait_future" in vars(BaseROS2DeviceNode)


def test_device_append_resource_requires_authority_uuid() -> None:
    """设备侧挂载新协议只接受带 uuid 的引用（微后端权威已创建）。

    锁定请求契约的关键词，防止回退到「本地 initialize + add 上报」的旧协议；
    assign 统一复用 transfer_to_new_resource（含 site/spot 探测）。
    """
    import unilabos.backend.runtime.node as device_node_module
    from unilabos.backend.ros2 import base_device_node

    # BaseROS2DeviceNode 继承自 DeviceNode，取到的是同一份实现
    source = inspect.getsource(base_device_node.BaseROS2DeviceNode.append_resource)
    assert '"resource_uuid"' in source or "'resource_uuid'" in source
    assert "transfer_to_new_resource" in source
    # 旧协议关键字段不应再出现在 append 流程中
    assert "initialize_full" not in inspect.getsource(base_device_node)
    assert "initialize_full" not in inspect.getsource(device_node_module)


def test_resource_tree_mutex_is_backend_neutral() -> None:
    """资源树互斥锁泛化为 DeviceAsyncMutex：唤醒经 node.create_task 调度，
    等待经 node.create_wait_future 挂起，ROS 层不再保留 rclpy 专用锁。"""
    from unilabos.backend.runtime.async_utils import DeviceAsyncMutex
    from unilabos.backend.ros2 import base_device_node

    import unilabos.backend.runtime.async_utils as async_utils_module

    # 不 import rclpy、不调用 rclpy API（docstring 提及不算）
    assert "import rclpy" not in inspect.getsource(async_utils_module)
    mutex_source = inspect.getsource(DeviceAsyncMutex)
    assert "rclpy." not in mutex_source
    assert "create_wait_future" in mutex_source
    assert "create_task" in mutex_source

    assert not hasattr(base_device_node, "RclpyAsyncMutex")


def test_resource_query_goes_through_authority_not_ros() -> None:
    """物料查询（uuid / resource id）统一在 DeviceNode 上直连权威，不再有 /resources/get srv。"""
    import unilabos.backend.runtime.node as device_node_module
    from unilabos.backend.runtime.node import DeviceNode
    from unilabos.backend.ros2 import base_device_node
    from unilabos.backend.ros2.presets import host_node, workstation

    # DeviceNode 暴露查询协程；ROS 层不再 override
    assert inspect.iscoroutinefunction(DeviceNode.get_resource)
    assert inspect.iscoroutinefunction(DeviceNode.get_resource_by_id)
    assert inspect.iscoroutinefunction(DeviceNode.get_resource_with_dir)
    for name in ("get_resource", "get_resource_by_id", "get_resource_with_dir"):
        assert name not in vars(base_device_node.BaseROS2DeviceNode), f"{name} 不应在 ROS 层重复实现"

    # DeviceNode 的实现不依赖 ROS
    assert "rclpy" not in inspect.getsource(device_node_module.DeviceNode.get_resource_by_id)

    # 全链路不再出现 /resources/get srv 地址
    for module in (base_device_node, host_node, workstation):
        assert "/resources/get" not in inspect.getsource(module), f"{module.__name__} 仍引用 /resources/get"


def test_materials_module_exposes_get_and_search() -> None:
    """materials 提供公共查询入口：get（uuid/dir，未命中抛错）与 search（name，未命中返回 []）。"""
    from unilabos.resources import materials

    assert callable(materials.get)
    assert callable(materials.search)
    assert "get" in materials.__all__ and "search" in materials.__all__

    # 网关协议与三条链路（Local / HTTP / HostLink client）都支持按 name 搜索
    from unilabos.client.materials import (
        HTTPMaterialsClient,
        HostLinkMaterialsClient,
        LocalMaterialsClient,
    )

    for client_cls in (LocalMaterialsClient, HTTPMaterialsClient, HostLinkMaterialsClient):
        assert callable(getattr(client_cls, "search_materials")), client_cls.__name__

    from unilabos.backend.hostlink.protocol import ActionType

    assert ActionType.MATERIAL_SEARCH == "material.search"


def test_c2s_update_resource_tree_fully_retired() -> None:
    """/c2s_update_resource_tree 全链路退役。

    其语义内化为 materials.* 工具函数（create/ensure/get/update/remove），
    host / slave 设备语义一致（Slave 经 HostLink 访问同一权威）：
    - host 不再注册该 srv，四个 action 回调全部删除；
    - slave 开机不再上报物料、不再拿 uuid_mapping 换 uuid，改 materials.ensure 对齐；
    - doctor 假设备诊断不再探测该 srv。
    """
    from unilabos.backend.hostlink import doctor
    from unilabos.backend.ros2 import main_slave_run
    from unilabos.backend.ros2.presets import host_node

    for module in (host_node, main_slave_run, doctor):
        assert "c2s_update_resource_tree" not in inspect.getsource(module), module.__name__

    for name in (
        "_resource_tree_update_callback",
        "_resource_tree_action_add_callback",
        "_resource_tree_action_get_callback",
        "_resource_tree_action_update_callback",
        "_resource_tree_action_remove_callback",
    ):
        assert not hasattr(host_node.HostNode, name), f"{name} 应随 srv 一并退役"

    slave_source = inspect.getsource(main_slave_run)
    assert "materials.ensure" in slave_source
    assert "uuid_mapping" not in slave_source


def test_boot_material_alignment_is_shared_between_host_and_slave() -> None:
    """开机物料对齐语义统一：host（main）与两种 slave 入口都走 materials.ensure。"""
    import unilabos.app.main as app_main
    import unilabos.backend.hostlink.main_hostlink_run as hostlink_run
    from unilabos.resources import materials

    for name in ("ensure", "update", "remove", "create"):
        assert callable(getattr(materials, name))
        assert name in materials.__all__

    assert "ensure" in inspect.getsource(app_main)
    assert "materials.ensure" in inspect.getsource(hostlink_run)

    # ensure 的服务端支点：create 请求可携带显式 material_uuid（带条件的创建）
    from unilabos.protocol.materials import MaterialNodeCreate

    assert "material_uuid" in MaterialNodeCreate.model_fields


def test_apply_deduct_resource_lands_material_via_materials_protocol() -> None:
    """出库扣减走 materials 协议：挂载/透传前经 materials.ensure 把扣减产物
    （带 uuid）落权威（相当于 create 带条件），不再假设云端已同步微后端。"""
    from unilabos.backend import host_material_actions

    source = inspect.getsource(host_material_actions.deduct_resource)
    assert "materials.ensure" in source


def test_host_material_actions_shared_by_both_backends() -> None:
    """host 物料 API 固定为四动作，业务实现唯一（host_material_actions，
    全走 materials.*）；两种 backend 的 host_node 都是薄壳：

    - ROS2 HostNode @action 与 HostLink 内置 host 服务设备的四个方法体
      均调用共享实现，不各自维护编排逻辑；
    - transfer_manual 退役——人工闸门由系统自带的 manual_confirm 承担；
    - 共享实现按 materials.resolve 统一解析 ResourceSlot 入参。
    """
    from unilabos.backend import host_material_actions
    from unilabos.backend.hostlink.host_services import (
        HOST_SERVICE_ACTIONS,
        HostLinkHostServices,
    )
    from unilabos.backend.ros2.presets.host_node import HostNode
    from unilabos.resources import materials

    assert host_material_actions.HOST_MATERIAL_ACTIONS == (
        "apply_deduct_resource",
        "set_substance",
        "discard_resource",
        "transfer_resource",
    )
    assert set(HOST_SERVICE_ACTIONS) == {
        *host_material_actions.HOST_MATERIAL_ACTIONS,
        "manual_confirm",
    }

    # transfer_manual 全链路退役
    assert not hasattr(HostNode, "transfer_manual")
    assert not hasattr(HostLinkHostServices, "transfer_manual")

    # 两端薄壳都指向共享实现
    pairs = {
        "apply_deduct_resource": "deduct_resource",
        "set_substance": "set_substance",
        "discard_resource": "discard_resource",
        "transfer_resource": "transfer_resource",
    }
    for method_name, shared_name in pairs.items():
        for owner in (HostNode, HostLinkHostServices):
            source = inspect.getsource(getattr(owner, method_name))
            assert f"host_material_actions.{shared_name}" in source, (
                f"{owner.__name__}.{method_name} 应调用共享实现 {shared_name}"
            )

    # 共享实现全走 materials.* 门面（出库=创建：registry_class 现场创建 /
    # 带 uuid 产物 ensure adopt，两种来源都在 deduct_resource 内闭环；
    # 来源/目标设备经 owner_device_of 自动推断，转移直接走 materials.transfer）
    assert callable(materials.resolve)
    assert callable(materials.owner_device_of)
    shared_source = inspect.getsource(host_material_actions)
    for expected in (
        "materials.resolve",
        "materials.create",
        "materials.ensure",
        "materials.apply_substances",
        "materials.remove",
        "materials.owner_device_of",
        "materials.transfer",
        "update_resource",
    ):
        assert expected in shared_source, expected
    deduct_params = inspect.signature(
        host_material_actions.deduct_resource
    ).parameters
    assert "registry_class" in deduct_params
    assert "material_name" in deduct_params
    # 设备参数全部可缺省（自动推断）；target_device/device_id 仅作显式覆盖
    transfer_params = inspect.signature(
        host_material_actions.transfer_resource
    ).parameters
    assert transfer_params["target_device"].default == ""
    discard_params = inspect.signature(
        host_material_actions.discard_resource
    ).parameters
    assert discard_params["device_id"].default == ""


def test_run_node_coroutine_is_backend_neutral() -> None:
    """协程桥 run_node_coroutine 定义在 runtime（不依赖 rclpy），
    ROS 的 hostlink_bridge 复用同一实现而非各自维护。"""
    import unilabos.backend.runtime.async_utils as async_utils_module
    from unilabos.backend.runtime.async_utils import run_node_coroutine
    from unilabos.backend.ros2 import hostlink_bridge

    assert "import rclpy" not in inspect.getsource(async_utils_module)
    assert hostlink_bridge.run_node_coroutine is run_node_coroutine


def test_pure_hostlink_slave_registers_material_downlink_handlers() -> None:
    """纯 HostLink slave（无 ROS）也承接物料下行：client 注册
    RESOURCE_TREE_SYNC / RESOURCE_APPEND，语义与 ROS slave 的
    register_hostlink_resource_handlers 一致。"""
    from unilabos.backend.hostlink.backend import HostLinkBackend

    slave_source = inspect.getsource(HostLinkBackend._start_slave)
    assert "RESOURCE_TREE_SYNC" in slave_source
    assert "RESOURCE_APPEND" in slave_source

    assert callable(HostLinkBackend._handle_resource_tree_sync)
    assert callable(HostLinkBackend._handle_resource_append)
    for name in ("_handle_resource_tree_sync", "_handle_resource_append"):
        source = inspect.getsource(getattr(HostLinkBackend, name))
        assert "run_node_coroutine" in source
        assert "rclpy" not in source


def test_pure_hostlink_adapter_dispatches_resource_tree_update() -> None:
    """纯 HostLink host 的 notify_resource_tree_update 不再是 stub：
    本进程设备直调 apply_resource_tree_update，跨机经 RESOURCE_TREE_SYNC 下行，
    不可达返回 None（与 ROS HostNode 语义一致）。"""
    from unilabos.backend.hostlink.execution_adapter import HostLinkExecutionAdapter

    source = inspect.getsource(HostLinkExecutionAdapter.notify_resource_tree_update)
    assert "apply_resource_tree_update" in source
    assert "RESOURCE_TREE_SYNC" in source
    assert "has_device" in source
    assert "del device_id" not in source


def test_material_sync_retired_per_device_service() -> None:
    """material_sync 从 per-device service 退役，统一为 MATERIAL_SYNC 下行 RPC：

    - 设备侧只保留实例协程 material_sync(dict) -> dict；
    - ROS / 纯 HostLink slave 均注册 MATERIAL_SYNC handler；
    - 两种 host 的 dispatcher 本进程直调、跨机 HostLink RPC，不再有
      ROS create_client/wait_for_service 或 hostlink service-bus 调用。
    """
    from unilabos.backend.runtime.node import DeviceNode
    from unilabos.backend.hostlink import local_runtime
    from unilabos.backend.hostlink.backend import HostLinkBackend
    from unilabos.backend.hostlink.network import HostNetworkService
    from unilabos.backend.hostlink.protocol import ActionType
    from unilabos.backend.ros2 import hostlink_bridge
    from unilabos.backend.ros2 import base_device_node

    assert inspect.iscoroutinefunction(DeviceNode.material_sync)
    assert not hasattr(DeviceNode, "setup_material_sync_service")
    assert not hasattr(DeviceNode, "_material_sync_callback")

    assert ActionType.MATERIAL_SYNC == "material.sync"

    # 设备侧不再注册 per-device service（srv 地址与 setup 方法均不存在；注释提及不算）
    assert "/material_sync" not in inspect.getsource(base_device_node)
    assert "setup_material_sync_service" not in inspect.getsource(local_runtime)

    # slave 两侧均注册下行 handler
    assert "MATERIAL_SYNC" in inspect.getsource(hostlink_bridge.register_hostlink_resource_handlers)
    assert "MATERIAL_SYNC" in inspect.getsource(HostLinkBackend._start_slave)

    # host 两侧 dispatcher 不再依赖 service 发现
    ros_dispatch = inspect.getsource(HostNetworkService.dispatch_material_sync)
    assert "wait_for_service" not in ros_dispatch
    assert "create_client" not in ros_dispatch
    assert "material_sync_to_device" in ros_dispatch

    hostlink_dispatch = inspect.getsource(HostLinkBackend.dispatch_material_sync)
    assert "call_service" not in hostlink_dispatch
    assert "MATERIAL_SYNC" in hostlink_dispatch


def test_s2c_device_manage_fully_retired() -> None:
    """/s2c_device_manage 全链路退役，设备管理与物料下行同构：

    - 设备侧只保留实例协程 device_manage(dict) -> dict（含 create/destroy_device
      默认实现），定义在 backend 无关的 DeviceNode 上；
    - ROS / 纯 HostLink slave 均注册 DEVICE_MANAGE handler；
    - 两种 host 的 notify_device_manage 本进程直调、跨机 HostLink RPC，
      不再有 ROS create_client/wait_for_service。
    """
    from unilabos.backend.runtime.node import DeviceNode
    from unilabos.backend.hostlink.backend import HostLinkBackend
    from unilabos.backend.hostlink.execution_adapter import HostLinkExecutionAdapter
    from unilabos.backend.hostlink.protocol import ActionType
    from unilabos.backend.ros2 import hostlink_bridge
    from unilabos.backend.ros2 import base_device_node
    from unilabos.backend.ros2.presets.host_node import HostNode

    assert inspect.iscoroutinefunction(DeviceNode.device_manage)
    assert callable(DeviceNode.create_device)
    assert callable(DeviceNode.destroy_device)
    assert ActionType.DEVICE_MANAGE == "device.manage"

    # ROS 侧不再有 srv 回调 / srv 地址 / 重复的默认实现
    device_source = inspect.getsource(base_device_node)
    assert "s2c_device_manage" not in device_source
    for name in ("device_manage", "create_device", "destroy_device"):
        assert name not in vars(base_device_node.BaseROS2DeviceNode), f"{name} 不应在 ROS 层重复实现"

    # slave 两侧均注册下行 handler
    assert "DEVICE_MANAGE" in inspect.getsource(hostlink_bridge.register_hostlink_resource_handlers)
    assert "DEVICE_MANAGE" in inspect.getsource(HostLinkBackend._start_slave)

    # host 两侧分发不依赖 ROS service 发现
    ros_notify = inspect.getsource(HostNode.notify_device_manage)
    assert "wait_for_service" not in ros_notify
    assert "create_client" not in ros_notify
    assert "device_manage_to_device" in ros_notify

    hostlink_notify = inspect.getsource(HostLinkExecutionAdapter.notify_device_manage)
    assert "device_manage" in hostlink_notify
    assert "DEVICE_MANAGE" in hostlink_notify
    assert "del target_node_id" not in hostlink_notify


def test_resource_tree_driver_hooks_are_uniformly_async_aware() -> None:
    """资源树驱动回调（add/update/remove）统一经 _invoke_resource_hook 触发，
    同步/协程驱动实现均可；不再有绕过它的 getattr 直调。"""
    from unilabos.backend.runtime.node import DeviceNode

    apply_source = inspect.getsource(DeviceNode.apply_resource_tree_update)
    append_source = inspect.getsource(DeviceNode.append_resource)
    for hook in ("resource_tree_add", "resource_tree_remove", "resource_tree_update"):
        assert f'_invoke_resource_hook("{hook}"' in apply_source, hook
    assert '_invoke_resource_hook("resource_tree_add"' in append_source
    for source in (apply_source, append_source):
        assert "resource_tree_add\", None" not in source
        assert "resource_tree_remove\", None" not in source
        assert "resource_tree_update\", None" not in source
