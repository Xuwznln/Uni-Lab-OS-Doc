"""读取库存操作的完整资源需求；不取得 Permit、不写库存，也不选择执行顺序。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from unilabos.app.scheduler.device_target import ResolvedDeviceTarget
from unilabos.app.scheduler.resource_wait_policy import is_temporary_resource_condition
from unilabos.app.scheduler.site_target import (
    ResolvedSiteTarget,
    SiteTargetResolutionError,
    resolve_site_target,
    resource_argument_uuid,
)
from unilabos.app.scheduler.transfer_resource_set import resolve_transfer_resource_set
from unilabos.workflow.execution_resource_policy import (
    ResolvedExecutionResourcePolicy,
    resolve_execution_resource_policy,
)
from unilabos.workflow.resource_lock_key import device_lock_key, material_lock_key, site_lock_key


def material_io_resources(service: Any, params: dict[str, Any]) -> tuple[dict[str, str], ...]:
    """人工物料操作在确认前和事实投影时共享相同的库存范围解析。"""
    if service is None:
        raise ValueError("Material I/O 未装配本地库存服务")
    mount, material = params.get("mount"), params.get("material")
    return service.resolve_resources(
        operation=params.get("mode"),
        mount_material_uuid=resource_argument_uuid(mount, argument_name="mount") if mount is not None else None,
        site_uuid=params.get("site_uuid"), site=params.get("site"),
        material_uuid=resource_argument_uuid(material, argument_name="material") if material is not None else None,
        scope=params.get("scope"), disposition=params.get("disposition"), reagent=params.get("reagent"),
    )


@dataclass(frozen=True)
class ResourceBinding:
    parameters: dict[str, Any]
    resources: set[str]
    runtime_resources: set[str]
    facts: dict[str, Any]
    blockers: tuple[dict[str, Any], ...] = ()


def resource_bindings(
    scheduler: Any, node: Any, params: dict[str, Any], target: ResolvedDeviceTarget,
) -> list[ResourceBinding]:
    """复制每个设备/库位组合的完整需求，库存物理结算仍归最终 Permit 事务。"""
    transfer = scheduler._transfer_resource_contract(node)
    policy = scheduler._execution_policy_with_runtime_site_group(
        node, params, transfer_contract=transfer, unavailable_site_uuids=(), enumerate_candidates=True,
    )
    requirements = resolve_execution_resource_policy(policy, params)
    result = []
    site_groups: list[tuple[str, ...]] = [(identity,) for identity in requirements.target_site_uuids] or [()]
    rejected: list[ValueError] = []
    for site_ids in site_groups:
        try:
            result.append(_resource_binding(scheduler, node, params, target, requirements, transfer, site_ids))
        except ValueError as error:
            # 单个等价库位不合法，不能丢弃同组的其他候选；合同损坏仍须失败关闭。
            if not site_ids or not is_temporary_resource_condition(getattr(error, "code", "")):
                raise
            rejected.append(error)
    if not result and rejected:
        raise rejected[0]
    return result


def _resource_binding(
    scheduler: Any, node: Any, params: dict[str, Any], target: ResolvedDeviceTarget,
    requirements: ResolvedExecutionResourcePolicy, transfer: dict[str, str] | None,
    site_ids: tuple[str, ...],
) -> ResourceBinding:
    """解析单个合法绑定，并独立记录当前物理条件。"""
    blockers: list[dict[str, Any]] = []
    canonical, site = scheduler._resolve_transfer_site_target(
        node, deepcopy(params), transfer_contract=transfer, site_uuids=site_ids, require_available=False,
    )
    keys = scheduler._resource_lock_keys(node, canonical, resolved_site=site) | set(requirements.device_lock_keys)
    runtime = {
        material_lock_key(resource_argument_uuid(canonical.get(name), argument_name=name))
        for name in node.runtime_material_parameters
    }
    facts: dict[str, Any] = {}
    if node.is_material_io():
        keys.update(
            item["lock_key"] for item in material_io_resources(
                getattr(scheduler._inventory, "material_io", None), canonical,
            )
        )
        runtime.update(keys)
    elif not node.always_free or node.is_manual_confirm():
        keys.add(device_lock_key(target.material_uuid or target.local_device_id))
    if transfer is not None and site is not None:
        material = resource_argument_uuid(canonical.get(transfer["material_param"]), argument_name=transfer["material_param"])
        blockers.extend(_site_blockers(scheduler, site, material))
        resolved = resolve_transfer_resource_set(
            scheduler._required_station_resources(), resource_material_uuid=material, target=site,
            executor_material_uuid=target.material_uuid,
            allow_held_material=(node.action_resource_contract.get("transfer_step") or {}).get("operation") == "place",
            gripper_site_role=transfer["gripper_site_role"], require_device_owners=False,
        )
        canonical = scheduler._inject_actual_transfer_source(
            canonical, transfer_contract=transfer, source_owner_material_uuid=resolved.source_owner_material_uuid,
            source_site_uuid=resolved.source_site_uuid, source_site_name=resolved.source_site_name,
        )
        keys.update(resolved.lock_keys)
        if scheduler._transfer_uses_runtime_material(node, transfer):
            runtime.update(resolved.lock_keys)
        facts["transfer_dispatch_condition"] = {
            "material_uuid": material, "source_owner_material_uuid": resolved.source_owner_material_uuid,
            "source_site_uuid": resolved.source_site_uuid, "target_owner_material_uuid": site.owner_material_uuid,
            "target_site_uuid": site.uuid, "executor_material_uuid": target.material_uuid,
            "gripper_site_uuid": resolved.gripper_site_uuid,
        }
    operate = scheduler._operate_in_place_contract(node)
    if operate is not None:
        material = resource_argument_uuid(canonical.get(operate["material_param"]), argument_name=operate["material_param"])
        location = scheduler._required_station_resources().resolve_operate_in_place(
            material_uuid=material, device_material_uuid=target.material_uuid,
        )
        keys.update({
            device_lock_key(location.device_material_uuid), material_lock_key(location.material_uuid),
            site_lock_key(location.site_owner_material_uuid, location.site_uuid),
        })
        facts["operate_in_place_condition"] = {
            "material_uuid": location.material_uuid, "site_owner_material_uuid": location.site_owner_material_uuid,
            "site_uuid": location.site_uuid, "device_material_uuid": location.device_material_uuid,
        }
    aliquot = scheduler._aliquot_resource_contract(node)
    if aliquot is not None:
        source = resource_argument_uuid(canonical.get(aliquot["source_material_param"]), argument_name=aliquot["source_material_param"])
        targets = [
            resource_argument_uuid(canonical.get(name), argument_name=name)
            for name in aliquot["target_material_params"]
        ]
        if len(set([source, *targets])) != len(targets) + 1:
            raise ValueError("分装来源与目标容器必须互异")
        keys.update(material_lock_key(identity) for identity in [source, *targets])
        facts["aliquot_dispatch_condition"] = {"source_material_uuid": source, "target_material_uuids": targets}
    if node.action_name == "transfer_resource" and transfer is None:
        material = resource_argument_uuid(canonical.get("resource"), argument_name="resource")
        site = resolve_site_target(
            scheduler._required_station_resources(),
            owner_material_uuid=resource_argument_uuid(canonical.get("mount_resource"), argument_name="mount_resource"),
            site_uuid=str(canonical.get("site_uuid") or ""), site_name=str(canonical.get("site") or ""),
            occupant_material_uuid=material, require_available=False,
        )
        canonical.update(site=site.name, site_uuid=site.uuid)
        blockers.extend(_site_blockers(scheduler, site, material))
        resolved = resolve_transfer_resource_set(
            scheduler._required_station_resources(), resource_material_uuid=material, target=site,
            gripper_site_role="", require_device_owners=False,
        )
        keys.update(resolved.lock_keys)
        if "resource" in node.runtime_material_parameters:
            runtime.update(resolved.lock_keys)
        facts["inventory_relocation_condition"] = {
            "material_uuid": material, "source_owner_material_uuid": resolved.source_owner_material_uuid,
            "source_site_uuid": resolved.source_site_uuid, "target_owner_material_uuid": site.owner_material_uuid,
            "target_site_uuid": site.uuid,
        }
    return ResourceBinding(canonical, keys, runtime, facts, tuple(blockers))


def _site_blockers(scheduler: Any, site: ResolvedSiteTarget, material: str) -> list[dict[str, Any]]:
    """投影物理占用及入口预留；它们改变可执行性，不改变绑定身份。"""
    try:
        resolve_site_target(
            scheduler._required_station_resources(), owner_material_uuid=site.owner_material_uuid,
            site_uuid=site.uuid, occupant_material_uuid=material, require_available=True,
        )
    except SiteTargetResolutionError as error:
        if not is_temporary_resource_condition(error.code):
            raise
        return [{
            "code": error.code.upper(), "message": str(error),
            "resource_ids": [site_lock_key(site.owner_material_uuid, site.uuid)],
        }]
    return []
