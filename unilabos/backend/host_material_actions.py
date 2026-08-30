"""Host 物料编排四动作的共享实现（backend 无关）。

「host_node」是一个面向前端画布与工作流的固定物料 API：出库
（apply_deduct_resource）、设置物质（set_substance）、丢弃
（discard_resource）、移动（transfer_resource）。人工确认
（manual_confirm）是系统自带的通用动作，不属于物料 API。

两种 backend 都只是本模块的薄壳：ROS2 HostNode 的 ``@action`` 提供
schema / 画布 handle 与 ROS action 入口，HostLink 内置 host 服务设备提供
HostLink 入口；业务全部收敛在这里、且全部走 ``materials.*`` 门面。

调用方注入两样东西：

- ``node``：host 自身的 DeviceNode（update_resource / transfer_resource_to_another）；
- ``dispatch``：async 下行通道 ``(device_id, action_type, payload) -> dict``，
  两端语义一致——本进程设备直调实例协程，跨机（Slave）经 HostLink RPC。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Sequence

from unilabos.backend.hostlink.protocol import ActionType
from unilabos.backend.runtime.async_utils import run_blocking as _run_blocking
from unilabos.resources import materials
from unilabos.resources.resource_tracker import ResourceTreeSet
from unilabos.utils.log import logger

#: 下行通道：(device_id, action_type, payload) -> 结果 dict。
Dispatch = Callable[[str, str, Dict[str, Any]], Awaitable[Dict[str, Any]]]

#: host 物料 API 的固定动作集（画布 / 工作流以此为准）。
HOST_MATERIAL_ACTIONS = (
    "apply_deduct_resource",
    "set_substance",
    "discard_resource",
    "transfer_resource",
)


def _normalize_point(value: Any) -> Dict[str, float]:
    """把挂载坐标归一成 dict：接受 dict / Point 消息 / None。"""

    if value is None:
        return {"x": 0.0, "y": 0.0, "z": 0.0}
    if isinstance(value, dict):
        return {
            "x": float(value.get("x", 0.0)),
            "y": float(value.get("y", 0.0)),
            "z": float(value.get("z", 0.0)),
        }
    return {
        "x": float(getattr(value, "x", 0.0)),
        "y": float(getattr(value, "y", 0.0)),
        "z": float(getattr(value, "z", 0.0)),
    }


def _edge_id(device_id: Any) -> str:
    return str(device_id or "").split("/")[-1]


async def deduct_resource(
    node: Any,
    dispatch: Dispatch,
    resource: Any = None,
    registry_class: str = "",
    material_name: str = "",
    device_id: Any = "",
    mount_resource: Any = None,
    bind_locations: Any = None,
    slot_on_deck: str = "",
) -> Dict[str, Any]:
    """出库物料：物料进入系统的统一入口——创建/落权威，可选下行挂载。

    出库产物两种来源（二选一）：

    - ``resource``：已带 uuid 的扣减产物（云端仓储扣减 / 前端
      ``POST /materials/instantiate`` 出库端点的产物引用）——经
      ``materials.ensure`` 落权威：权威缺失时以原 uuid 显式创建（adopt），
      已存在则直接采用权威记录；
    - ``registry_class``（+ ``material_name``）：按 registry 资源类名现场
      创建全新物料——``materials.create`` 权威发号，与 instantiate 端点
      同款语义（动作路径不需要前端先调端点）。

    之后：

    - 仅登记/透传（缺 mount_resource）：物料树经 ``created_resource_tree``
      输出，供 set_substance / transfer_resource 接续；
    - 出库并挂载（给 mount_resource）：下行 ``RESOURCE_APPEND``——目标设备按
      uuid 从权威拉取实例化 → 物理 assign → 快照回权威。目标设备缺省自动
      推断（挂载目标物料所在根树的归属）；``device_id`` 仅作显式覆盖。
    """

    if registry_class:
        if resource is not None and not (
            isinstance(resource, str) and not resource.strip()
        ):
            raise ValueError("resource 与 registry_class 只能二选一")
        plr = await _run_blocking(
            materials.create, str(registry_class), name=str(material_name)
        )
        # create 已在权威落库（发号），无需再 ensure
        deduct_tree_set = ResourceTreeSet.from_plr_resources([plr])
    else:
        plr = await _run_blocking(materials.resolve, resource)
        if plr is None:
            raise ValueError(
                "申请扣减失败：未接收到已扣减物料（带 uuid 的 resource 或"
                " registry_class 现场创建，二选一）"
            )
        if getattr(plr, "unilabos_uuid", None) is None:
            raise ValueError(
                f"物料 {getattr(plr, 'name', plr)} 缺少 unilabos_uuid，无法处理"
            )
        # 出库扣减走 materials 协议：权威缺失时以原 uuid 显式创建（adopt），
        # 已存在则直接采用权威记录；之后设备侧按 uuid 拉取必然命中。
        deduct_tree_set = ResourceTreeSet.from_plr_resources([plr])
        await _run_blocking(materials.ensure, deduct_tree_set)
    dumped = deduct_tree_set.dump()
    if not dumped:
        raise ValueError(f"物料 {getattr(plr, 'name', plr)} 序列化为空")

    mount = (
        mount_resource
        if isinstance(mount_resource, str)
        else await _run_blocking(materials.resolve, mount_resource)
    )
    if mount is None or (isinstance(mount, str) and not mount):
        logger.info(
            f"[HostMaterials] 仅登记/透传物料 name={getattr(plr, 'name', '')}"
            "（未指定 mount_resource，不挂载）"
        )
        return {
            "created_resource_tree": dumped,
            "substance_resource_tree": [],
            "mount_resource": [],
        }
    target_id = _edge_id(device_id)
    if not target_id:
        # 目标设备自动推断：挂载目标物料属于哪台设备，就下发给哪台
        if isinstance(mount, str):
            raise ValueError(
                "mount_resource 为名字字符串时无法推断目标设备，请显式传 device_id"
            )
        target_id = await _run_blocking(materials.owner_device_of, mount)
    mount_name = getattr(mount, "name", None) or _edge_id(mount)
    logger.info(
        f"[HostMaterials] 挂载物料 name={getattr(plr, 'name', '')} "
        f"-> device={target_id} mount_resource={mount_name}"
    )
    payload: Dict[str, Any] = {
        "resource_uuid": [getattr(plr, "unilabos_uuid")],
        "bind_parent_id": str(mount_name),
        "bind_location": _normalize_point(bind_locations),
    }
    if slot_on_deck:
        payload["other_calling_param"] = {"slot": str(slot_on_deck)}
    result = await dispatch(target_id, ActionType.RESOURCE_APPEND, payload)
    result["mount_resource"] = (
        ResourceTreeSet.from_plr_resources([mount]).dump()
        if not isinstance(mount, str)
        else []
    )
    return result


async def set_substance(
    node: Any,
    resource: Any,
    substance_names: Sequence[str],
    amounts: Sequence[float],
    slots: Sequence[str] = (),
    is_solid: Sequence[bool] = (),
) -> Dict[str, Any]:
    """设置物料物质：写内容物（液体 ul / 固体 ug）并把整棵树同步回权威。"""

    plr = await _run_blocking(materials.resolve, resource)
    if plr is None:
        raise ValueError("设置内容物失败：未接收到物料")
    materials.apply_substances(
        plr,
        list(substance_names),
        list(amounts),
        slots=list(slots),
        is_solid=list(is_solid),
    )
    await node.update_resource(plr)
    dumped = ResourceTreeSet.from_plr_resources([plr]).dump()
    return {"resource": dumped[0] if dumped else []}


async def discard_resource(
    node: Any,
    dispatch: Dispatch,
    resource: Any,
    device_id: Any = "",
) -> Dict[str, Any]:
    """丢弃物料：权威销毁，成功后通知所属设备本地移除。

    所属设备缺省自动推断（materials.owner_device_of，权威根树归属登记）；
    显式传 device_id 可覆盖。推断必须发生在权威销毁之前。
    通知失败不回滚——权威已销毁，边缘侧下次同步对齐。
    """

    plr = await _run_blocking(materials.resolve, resource)
    if plr is None:
        raise ValueError("废弃失败：未接收到物料")
    res_uuid = getattr(plr, "unilabos_uuid", None)
    if res_uuid is None:
        raise ValueError(
            f"物料 {getattr(plr, 'name', plr)} 缺少 unilabos_uuid，无法废弃"
        )
    edge_id = _edge_id(device_id)
    if not edge_id:
        edge_id = await _run_blocking(materials.owner_device_of, plr)
    logger.info(
        f"[HostMaterials] 废弃物料 name={getattr(plr, 'name', '')} "
        f"uuid={res_uuid} device={edge_id}"
    )
    deleted = await _run_blocking(
        materials.remove,
        str(res_uuid),
        source_device_id=edge_id,
        source_device_uuid=str(getattr(node, "resource_uuid", "") or ""),
    )
    if res_uuid not in deleted:
        raise ValueError(f"微后端未确认物料废弃：{res_uuid}")
    try:
        await dispatch(
            edge_id,
            ActionType.RESOURCE_TREE_SYNC,
            {"operations": [{"action": "remove", "data": [res_uuid]}]},
        )
    except Exception as exc:  # noqa: BLE001 - 权威已销毁，通知失败仅告警
        logger.warning(
            f"[HostMaterials] 权威已销毁 uuid={res_uuid}，但通知设备 "
            f"{edge_id} 本地移除失败：{exc}"
        )
    return {"code": 0, "uuids": [res_uuid], "device_id": edge_id}


async def transfer_resource(
    node: Any,
    resource: Any,
    mount_resource: Any,
    site: str = "",
    target_device: Any = "",
) -> Dict[str, Any]:
    """移动物料：把已物理就位的物料在系统中改挂到目标物料的孔位。

    只需给物料与目标物料，两端设备都自动推断（materials.owner_device_of，
    权威根树归属登记）：

    - 来源设备 = 被转移物料当前所在根树的归属——unload 通知发给真实持有者，
      而不是 host 自己；
    - 目标设备 = 挂载目标物料所在根树的归属；``target_device`` 仅作显式覆盖。

    换位与跨设备同一语义（materials.transfer）：微后端先提交权威位置，再按
    来源 unload → 目标 load 通知设备投影，物理搬运由前序节点保证
    （manual_confirm 人工闸门 / 机械臂 pick+place）。

    site：目标父级上的 Site 选择器——前端提交权威 ResourceSite 的 uuid，
    兼容 label / 数字索引；空串表示由父级默认排布。
    """

    plr = await _run_blocking(materials.resolve, resource)
    mount = await _run_blocking(materials.resolve, mount_resource)
    if plr is None:
        raise ValueError("转移失败：未接收到待转移物料")
    if mount is None:
        raise ValueError("转移失败：未指定挂载目标孔位")
    source_id = await _run_blocking(materials.owner_device_of, plr)
    target_id = _edge_id(target_device)
    if not target_id:
        target_id = await _run_blocking(materials.owner_device_of, mount)
    result = await materials.transfer(
        [plr],
        target_id,
        [mount],
        [site if site else None],
        source_device_id=source_id,
    )
    return {
        "resource": ResourceTreeSet.from_plr_resources([plr]).dump(),
        "mount_resource": ResourceTreeSet.from_plr_resources([mount]).dump(),
        "site": site,
        "result": result,
    }


__all__ = [
    "Dispatch",
    "HOST_MATERIAL_ACTIONS",
    "deduct_resource",
    "discard_resource",
    "set_substance",
    "transfer_resource",
]
