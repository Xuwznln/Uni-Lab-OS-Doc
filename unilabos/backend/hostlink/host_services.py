"""HostLink Host 进程内置「host_node 服务设备」。

物料编排四动作（出库 apply_deduct_resource / 设置物质 set_substance /
丢弃 discard_resource / 移动 transfer_resource）的业务实现在
:mod:`unilabos.backend.host_material_actions`（backend 无关、全走
``materials.*``）。ROS2 模式由 HostNode 的 ``@action`` 承载同一实现；
HostLink 全栈没有 ROS HostNode，本模块把同语义动作承载到一个内置
HostLink 设备上（device_id 即 ``BasicConfig.host_node_name``），使微前端
单点动作（ad_hoc_device_action）与工作流画布节点在两种 backend 下都能对
同一 device_id 提交这些动作。

manual_confirm 是系统自带的通用人工确认动作（不属于物料 API），一并由
本服务设备承载。

action_value_mappings 直接复用 registry 中 AST 扫描出的 host_node 条目
（HostLink schema-only 模式保留 JSON 描述），保证 schema / placeholder /
handles 与 ROS2 模式完全一致。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from unilabos.config.config import resolve_host_node_name
from unilabos.backend import host_material_actions
from unilabos.backend.hostlink.protocol import ActionType
from unilabos.utils.log import logger

#: 由内置 host 服务设备承载的动作（物料四动作 + 自带 manual_confirm）。
HOST_SERVICE_ACTIONS = (
    *host_material_actions.HOST_MATERIAL_ACTIONS,
    "manual_confirm",
)


class HostLinkHostServices:
    """HostLink host 进程的物料编排驱动（host_material_actions 的薄壳）。"""

    def __init__(self, backend: Any = None, **_kwargs: Any) -> None:
        self._backend = backend
        self._node: Any = None

    def post_init(self, node: Any) -> None:
        self._node = node

    # ------------------------------------------------------------------
    # 下行分发：本进程设备直调实例协程；跨机（Slave）设备经 HostLink RPC。
    # 与 ros2/hostlink_bridge 的 *_to_device helpers 同语义（HostLink 侧的
    # 本地注册表是 runtime.devices，而非 ROS 的 registered_devices）。
    # ------------------------------------------------------------------

    def _dispatch_blocking(
        self, device_id: str, action_type: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        from unilabos.backend.runtime.async_utils import run_node_coroutine

        backend = self._backend
        if backend is None:
            raise RuntimeError("HostLinkHostServices 未绑定 backend")
        node = backend.local.get_device(device_id)
        if node is not None:
            data = {k: v for k, v in payload.items() if k != "device_id"}
            if action_type == ActionType.RESOURCE_APPEND:
                return run_node_coroutine(node, node.append_resource(data))
            if action_type == ActionType.RESOURCE_TREE_SYNC:
                return run_node_coroutine(
                    node, node.apply_resource_tree_update(data["operations"])
                )
            raise ValueError(f"未支持的本地下行类型: {action_type}")
        server = backend.server
        if server is None or not server.has_device(device_id):
            raise ValueError(
                f"设备 {device_id!r} 不在本进程、也不在 HostLink 在线表，无法下行 {action_type}"
            )
        return server.request_device(
            str(device_id),
            action_type,
            {"device_id": str(device_id), **payload},
            timeout=30.0,
        )

    async def _dispatch(
        self, device_id: str, action_type: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        # 阻塞 RPC / 跨节点协程桥放到线程池，避免阻塞本设备事件循环
        return await asyncio.to_thread(
            self._dispatch_blocking, device_id, action_type, payload
        )

    # ------------------------------------------------------------------
    # 动作薄壳（签名与 HostNode @action 对齐；ResourceSlot 参数收 raw 形态，
    # 共享实现内经 materials.resolve 统一解析——引用回权威拉取）
    # ------------------------------------------------------------------

    async def apply_deduct_resource(
        self,
        resource: Any = None,
        registry_class: str = "",
        material_name: str = "",
        device_id: str = "",
        mount_resource: Any = None,
        bind_locations: Any = None,
        slot_on_deck: str = "",
    ) -> Dict[str, Any]:
        """出库物料（语义同 HostNode.apply_deduct_resource）。"""
        return await host_material_actions.deduct_resource(
            self._node,
            self._dispatch,
            resource,
            registry_class=registry_class,
            material_name=material_name,
            device_id=device_id,
            mount_resource=mount_resource,
            bind_locations=bind_locations,
            slot_on_deck=slot_on_deck,
        )

    async def set_substance(
        self,
        resource: Any,
        substance_names: List[str],
        amounts: List[float],
        slots: List[str] = [],
        is_solid: List[bool] = [],
    ) -> Dict[str, Any]:
        """设置物料物质（语义同 HostNode.set_substance）。"""
        return await host_material_actions.set_substance(
            self._node, resource, substance_names, amounts, slots=slots, is_solid=is_solid
        )

    async def discard_resource(self, resource: Any, device_id: str = "") -> Dict[str, Any]:
        """丢弃物料（语义同 HostNode.discard_resource，设备缺省自动推断）。"""
        return await host_material_actions.discard_resource(
            self._node, self._dispatch, resource, device_id
        )

    async def transfer_resource(
        self,
        resource: Any,
        mount_resource: Any = None,
        site: str = "",
        target_device: str = "",
    ) -> Dict[str, Any]:
        """移动物料（语义同 HostNode.transfer_resource，两端设备自动推断）。"""
        return await host_material_actions.transfer_resource(
            self._node, resource, mount_resource, site, target_device
        )

    def manual_confirm(
        self, timeout_seconds: int, assignee_user_ids: List[str], **kwargs: Any
    ) -> Dict[str, Any]:
        """人工确认（只读透传，语义同 HostNode.manual_confirm）。"""
        del timeout_seconds, assignee_user_ids
        return kwargs


def host_service_action_mappings() -> Dict[str, Dict[str, Any]]:
    """从 registry 的 host_node 条目取内置服务动作的 action_value_mappings。"""
    from unilabos.registry.registry import lab_registry

    entry = lab_registry.device_type_registry.get("host_node", {})
    mappings = entry.get("class", {}).get("action_value_mappings", {})
    return {
        name: mappings[name] for name in HOST_SERVICE_ACTIONS if name in mappings
    }


def register_host_services(backend: Any) -> Optional[Any]:
    """在 HostLink Host 进程注册内置 host 服务设备（幂等）。

    registry 未加载（如精简单测环境）时跳过并返回 None——此时没有
    schema/placeholder 描述，注册空动作设备没有意义。
    """
    from unilabos.backend.hostlink.local_runtime import HostLinkDriverSpec

    device_id = resolve_host_node_name()
    existing = backend.local.devices.get(device_id)
    if existing is not None:
        return existing
    mappings = host_service_action_mappings()
    if not mappings:
        logger.info(
            "[HostServices] registry 中没有 host_node 动作描述，跳过内置服务设备注册"
        )
        return None
    return backend.local.add_driver(
        HostLinkDriverSpec(
            device_id=device_id,
            driver_class=HostLinkHostServices,
            config={"backend": backend},
            registry_name="host_node",
            display_name="Host 服务",
            action_names=tuple(mappings),
            action_value_mappings=mappings,
        )
    )


__all__ = [
    "HOST_SERVICE_ACTIONS",
    "HostLinkHostServices",
    "host_service_action_mappings",
    "register_host_services",
]
