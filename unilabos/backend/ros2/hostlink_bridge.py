"""Host→Device 物料下行链路桥（append_resource / 资源树同步，不建 ROS service）。

物料链路要求走 HostLink 保证高可用，不依赖 ROS 服务发现：

- 本进程设备（含 HostNode 自身）：直接查 ``registered_devices``，把协程调度到
  rclpy executor 执行；
- 跨机（Slave）设备：Host 经 ``HostLinkServer.request_device`` 下行 RPC；Slave
  进程用 :func:`register_hostlink_resource_handlers` 把 handler 挂到
  ``HostLinkClient``，收到请求后同样经协程桥调用本地设备节点实例方法。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from unilabos.backend.runtime.async_utils import run_node_coroutine
from unilabos.backend.hostlink.protocol import ActionType
from unilabos.backend.hostlink.server import get_hostlink_server

DEFAULT_DOWNLINK_TIMEOUT = 30.0


def get_local_device_node(device_id: str) -> Optional[Any]:
    """按 device_id 取本进程的设备节点实例（DeviceNode 子类，含 HostNode 自身）。"""
    from unilabos.backend.ros2.base_device_node import registered_devices

    info = registered_devices.get(str(device_id))
    if info is None:
        return None
    return info.get("base_node_instance")


def _require_local_device_node(device_id: str) -> Any:
    node = get_local_device_node(device_id)
    if node is None:
        raise ValueError(f"本进程没有设备节点: {device_id!r}")
    return node


def local_resource_tree_sync(data: Dict[str, Any]) -> Dict[str, Any]:
    """``RESOURCE_TREE_SYNC`` 的本进程执行：分发资源树变更到设备节点。

    data: {"device_id": str, "operations": [{"action", "data", ...}]}
    """
    device_id = str(data.get("device_id") or "")
    operations = list(data.get("operations") or [])
    node = _require_local_device_node(device_id)
    return run_node_coroutine(node, node.apply_resource_tree_update(operations))


def local_resource_append(data: Dict[str, Any]) -> Dict[str, Any]:
    """``RESOURCE_APPEND`` 的本进程执行：把微后端权威物料挂载到设备节点。

    data: {"device_id": str, "resource_uuid": [...], "bind_parent_id": str,
           "bind_location": {x,y,z}, "other_calling_param": {...}}
    """
    device_id = str(data.get("device_id") or "")
    node = _require_local_device_node(device_id)
    payload = {key: value for key, value in data.items() if key != "device_id"}
    return run_node_coroutine(node, node.append_resource(payload))


def local_material_sync(data: Dict[str, Any]) -> Dict[str, Any]:
    """``MATERIAL_SYNC`` 的本进程执行：微后端 transfer 的设备侧投影（unload/load）。

    data: {"device_id": str, "action": "unload"|"load", "material_uuids": [...],
           "transfer_uuid": str, "destination_site_uuids": [...]}
    """
    device_id = str(data.get("device_id") or "")
    node = _require_local_device_node(device_id)
    return run_node_coroutine(node, node.material_sync(dict(data)))


def local_device_manage(data: Dict[str, Any]) -> Dict[str, Any]:
    """``DEVICE_MANAGE`` 的本进程执行：动态 add/remove 设备。

    data: {"device_id": str（目标节点）, "action": "add"|"remove",
           "data": {...device config, 含 id...}}
    """
    device_id = str(data.get("device_id") or "")
    node = _require_local_device_node(device_id)
    payload = {key: value for key, value in data.items() if key != "device_id"}
    return run_node_coroutine(node, node.device_manage(payload))


def register_hostlink_resource_handlers(client: Any) -> None:
    """把物料/管理下行 handler 挂到 Slave 的 HostLinkClient（幂等）。"""
    client.register_handler(ActionType.RESOURCE_TREE_SYNC, local_resource_tree_sync)
    client.register_handler(ActionType.RESOURCE_APPEND, local_resource_append)
    client.register_handler(ActionType.MATERIAL_SYNC, local_material_sync)
    client.register_handler(ActionType.DEVICE_MANAGE, local_device_manage)


def sync_resource_tree_to_device(
    device_id: str,
    operations: List[Dict[str, Any]],
    timeout: float = DEFAULT_DOWNLINK_TIMEOUT,
) -> Dict[str, Any]:
    """Host 侧资源树变更分发：本进程直调，否则经 HostLink 下行到 Slave。

    设备既不在本进程、又不在 HostLink 在线表时抛异常（物料链路不回退 ROS 发现）。
    """
    payload = {"device_id": str(device_id), "operations": list(operations)}
    if get_local_device_node(device_id) is not None:
        return local_resource_tree_sync(payload)
    server = get_hostlink_server()
    if server is None:
        raise RuntimeError(f"HostLink server 未启动，无法向跨机设备 {device_id!r} 分发资源树变更")
    return server.request_device(str(device_id), ActionType.RESOURCE_TREE_SYNC, payload, timeout)


def append_resource_via_hostlink(
    device_id: str,
    payload: Dict[str, Any],
    timeout: float = DEFAULT_DOWNLINK_TIMEOUT,
) -> Dict[str, Any]:
    """Host 侧跨机挂载请求：经 HostLink 下行到广播该设备的 Slave。"""
    server = get_hostlink_server()
    if server is None:
        raise RuntimeError(f"HostLink server 未启动，无法向跨机设备 {device_id!r} 分发物料挂载")
    data = {"device_id": str(device_id), **payload}
    return server.request_device(str(device_id), ActionType.RESOURCE_APPEND, data, timeout)


def material_sync_to_device(
    device_id: str,
    command: Dict[str, Any],
    timeout: float = DEFAULT_DOWNLINK_TIMEOUT,
) -> Dict[str, Any]:
    """Host 侧 transfer 投影分发：本进程直调，否则经 HostLink 下行到 Slave。"""
    payload = {**command, "device_id": str(device_id)}
    if get_local_device_node(device_id) is not None:
        return local_material_sync(payload)
    server = get_hostlink_server()
    if server is None:
        raise RuntimeError(f"HostLink server 未启动，无法向跨机设备 {device_id!r} 分发物料同步")
    return server.request_device(str(device_id), ActionType.MATERIAL_SYNC, payload, timeout)


def device_manage_to_device(
    device_id: str,
    action: str,
    config: Dict[str, Any],
    timeout: float = DEFAULT_DOWNLINK_TIMEOUT,
) -> Dict[str, Any]:
    """Host 侧设备管理分发：本进程直调，否则经 HostLink 下行到 Slave。"""
    payload = {
        "device_id": str(device_id),
        "action": str(action),
        "data": dict(config),
    }
    if get_local_device_node(device_id) is not None:
        return local_device_manage(payload)
    server = get_hostlink_server()
    if server is None:
        raise RuntimeError(f"HostLink server 未启动，无法向跨机设备 {device_id!r} 分发设备管理")
    return server.request_device(str(device_id), ActionType.DEVICE_MANAGE, payload, timeout)


__all__ = [
    "DEFAULT_DOWNLINK_TIMEOUT",
    "append_resource_via_hostlink",
    "device_manage_to_device",
    "get_local_device_node",
    "local_device_manage",
    "local_material_sync",
    "local_resource_append",
    "local_resource_tree_sync",
    "material_sync_to_device",
    "register_hostlink_resource_handlers",
    "run_node_coroutine",
    "sync_resource_tree_to_device",
]
