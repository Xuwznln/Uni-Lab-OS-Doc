"""@workflow 装饰器：把 Python 函数声明为可上报的默认子工作流。

设备包用 ``@workflow`` 声明工作流模板，函数体内通过 ctx 描述步骤：

    from unilabos.registry.workflows import workflow

    @workflow(display_name="演示闭环", description="终止并重启一轮计数")
    def demo_flow(ctx):
        ctx.run("sub_reporter/stop_counting", {})
        ctx.run_template("status_reporter_demo/start_counting", {})

- ``ctx.run("device_id/action_name", params)``：显式指定目标设备实例。
- ``ctx.run_template("class_name/action_name", params)``：按 registry 设备类
  解析目标设备；该类在设备图中只有一个实例时自动填充，无需确认。
- 两者都接受 ``inventory=[...]``：该步骤的库存需求（``InventoryRequirement``
  形态，如 ``{"key": "water", "kind": "lot", "lot_uuid": ..., "quantity": 40,
  "unit": "ml"}``），写入节点 ``meta_data.inventory_requirements``；调度器在任务
  启动时 all-or-nothing 预留，数量不足则任务在派发前失败（``plan_not_executable``），
  动作开始时由执行面扣减。

工作流 uuid 由函数相对路径（``module:qualname``）经 uuid5 派生，重复启动
或重复上传保持稳定，权威侧按 uuid 幂等 upsert（存在即覆盖节点图）。

步骤节点不画 handle 连线（handle 边属于节点模板体系，声明式步骤没有数据
流），执行序依赖写在节点 ``execution_policy.depends_on``（上一步节点
uuid），调度器把它翻译成 DAG 依赖边——步骤严格按声明序串行执行。节点
uuid 按步骤序构造，拓扑序 == 声明序。
"""

from __future__ import annotations

import importlib
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from unilabos.protocol.materials import InventoryRequirement
from unilabos.utils.log import logger

#: uuid5 命名空间：Uni-Lab 工作流（固定值，保证跨进程/跨机器一致）。
WORKFLOW_NAMESPACE = uuid_module.uuid5(uuid_module.NAMESPACE_URL, "unilabos://workflow")

#: 模块级注册表：workflow_uuid -> WorkflowDefinition（import 装饰器即注册）。
_registered_workflows: Dict[str, "WorkflowDefinition"] = {}


def workflow_uuid_for(source_path: str) -> str:
    """按函数相对路径（``module:qualname``）派生稳定 workflow uuid。"""

    return str(uuid_module.uuid5(WORKFLOW_NAMESPACE, source_path))


@dataclass(frozen=True)
class WorkflowStep:
    """一条 ctx.run/ctx.run_template 记录（build 时解析为 workflow 节点）。"""

    kind: str  # "run"（device_id 显式）或 "run_template"（class 解析）
    target: str  # device_id 或 class_name
    action: str
    params: Dict[str, Any]
    name: str  # 节点显示名，缺省 f"{target}.{action}"
    #: 已校验的 InventoryRequirement（json 形态），落到节点 meta_data.inventory_requirements
    inventory: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowDefinition:
    """一个 @workflow 函数的注册条目。"""

    uuid: str
    source_path: str  # module:qualname，uuid 的派生源
    display_name: str
    description: str
    tags: List[str]
    fn: Callable[["WorkflowBuildContext"], Any]

    def collect_steps(self) -> List[WorkflowStep]:
        """执行函数体收集步骤（声明式：函数只描述步骤，不执行设备动作）。"""

        ctx = WorkflowBuildContext()
        self.fn(ctx)
        if not ctx.steps:
            raise ValueError(f"工作流 {self.source_path} 未声明任何步骤")
        return list(ctx.steps)


class WorkflowBuildContext:
    """传入 @workflow 函数的构建上下文，记录步骤声明。"""

    def __init__(self) -> None:
        self.steps: List[WorkflowStep] = []

    def run(
        self,
        target: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        name: str = "",
        inventory: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> WorkflowStep:
        """按 ``"device_id/action_name"`` 追加一步（显式设备实例）。"""

        return self._append("run", target, params, name, inventory)

    def run_template(
        self,
        target: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        name: str = "",
        inventory: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> WorkflowStep:
        """按 ``"class_name/action_name"`` 追加一步（设备类解析，单实例自动填）。"""

        return self._append("run_template", target, params, name, inventory)

    def _append(
        self,
        kind: str,
        target: str,
        params: Optional[Dict[str, Any]],
        name: str,
        inventory: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> WorkflowStep:
        head, sep, action = str(target).partition("/")
        head = head.strip()
        action = action.strip()
        if not sep or not head or not action:
            raise ValueError(
                f'工作流步骤 target 必须是 "{"device_id" if kind == "run" else "class_name"}/action_name"：{target!r}'
            )
        step = WorkflowStep(
            kind=kind,
            target=head,
            action=action,
            params=dict(params or {}),
            name=name or f"{head}.{action}",
            inventory=_normalize_inventory(inventory, f"{head}/{action}"),
        )
        self.steps.append(step)
        return step


def _normalize_inventory(
    inventory: Optional[Sequence[Mapping[str, Any]]], step_label: str
) -> List[Dict[str, Any]]:
    """步骤库存需求在声明时就按 InventoryRequirement 校验，键唯一。"""

    if not inventory:
        return []
    normalized = [
        InventoryRequirement.model_validate(dict(item)).model_dump(
            mode="json", exclude_none=False
        )
        for item in inventory
    ]
    keys = [item["key"] for item in normalized]
    if len(keys) != len(set(keys)):
        raise ValueError(f"工作流步骤 {step_label} 的库存需求 key 重复：{keys}")
    return normalized


def workflow(
    display_name: str,
    *,
    description: str = "",
    tags: Optional[Sequence[str]] = None,
):
    """把模块级函数注册为默认子工作流。

    Args:
        display_name: 工作流显示名（必填，即权威侧 workflow name）。
        description: 工作流描述。
        tags: 工作流标签。
    """

    if not str(display_name).strip():
        raise ValueError("@workflow 必须提供非空 display_name")

    def decorator(fn: Callable[[WorkflowBuildContext], Any]):
        source_path = f"{fn.__module__}:{fn.__qualname__}"
        definition = WorkflowDefinition(
            uuid=workflow_uuid_for(source_path),
            source_path=source_path,
            display_name=str(display_name).strip(),
            description=str(description or ""),
            tags=[str(tag) for tag in (tags or [])],
            fn=fn,
        )
        existing = _registered_workflows.get(definition.uuid)
        if existing is not None and existing.fn is not fn:
            logger.warning(
                f"[Workflow] 重复注册 {source_path}（uuid={definition.uuid}），覆盖旧定义"
            )
        _registered_workflows[definition.uuid] = definition
        fn._workflow_registry_meta = definition  # type: ignore[attr-defined]
        return fn

    return decorator


def get_registered_workflows() -> Dict[str, WorkflowDefinition]:
    """返回当前进程已注册的工作流（uuid -> 定义）。"""

    return dict(_registered_workflows)


def clear_registered_workflows() -> None:
    """清空注册表（仅测试使用）。"""

    _registered_workflows.clear()


# ---------------------------------------------------------------------------
# 设备目录与节点构建
# ---------------------------------------------------------------------------


@dataclass
class DeviceCatalog:
    """构建 workflow 节点所需的设备清单投影。

    - by_device_id: device_id -> {"class": registry 设备类 id, "uuid": 资源 uuid}
    - by_class: 设备类 id -> [device_id ...]

    设备类 id 取节点 ``template_name``（图契约字段；旧图 ``class`` 已在读取边界回填）。
    """

    by_device_id: Dict[str, Dict[str, str]] = field(default_factory=dict)
    by_class: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_resource_tree_set(cls, tree_set: Any) -> "DeviceCatalog":
        """从启动图 ResourceTreeSet 提取设备清单（设备节点带 registry 类 id）。"""

        catalog = cls()
        if tree_set is None:
            return catalog
        for node in getattr(tree_set, "all_nodes", []):
            content = node.res_content
            if str(getattr(content, "type", "")) != "device":
                continue
            klass = str(getattr(content, "template_name", "") or "")
            if not klass:
                continue
            device_id = str(content.id)
            catalog.add(device_id, klass, str(getattr(content, "uuid", "") or ""))
        return catalog

    def add(self, device_id: str, klass: str, resource_uuid: str) -> None:
        self.by_device_id[device_id] = {"class": klass, "uuid": resource_uuid}
        self.by_class.setdefault(klass, []).append(device_id)

    def resolve_class(self, class_name: str) -> str:
        """设备类 -> 唯一实例 device_id；0 个或多个实例时报错。"""

        instances = self.by_class.get(class_name, [])
        if len(instances) == 1:
            return instances[0]
        if not instances:
            raise ValueError(
                f"设备图中没有类 {class_name!r} 的实例，无法解析 run_template 步骤"
            )
        raise ValueError(
            f"设备类 {class_name!r} 有多个实例 {instances}，"
            f'请改用 ctx.run("<device_id>/<action>") 显式指定'
        )

    def material_uuid_of(self, device_id: str) -> str:
        """设备的资源 uuid；不在目录（如 slave 侧设备）时按 device_id 稳定占位。

        device_action 节点要求 material_uuid 非空；调度以
        meta_data.target_device_id 优先解析目标设备，占位 uuid 仅满足图校验。
        """

        info = self.by_device_id.get(device_id)
        if info and info.get("uuid"):
            return info["uuid"]
        return str(uuid_module.uuid5(WORKFLOW_NAMESPACE, f"device:{device_id}"))


def _step_node_uuid(workflow_uuid_value: str, index: int) -> str:
    """步骤节点 uuid：由工作流 uuid + 步骤序号构造，字典序 == 步骤序。

    本地权威无节点模板/handle 体系，步骤间不连线；执行计划对同批节点按
    (create_time, uuid) 排序，序号编码进第二段保证拓扑序即声明序。
    """

    if not 0 <= index <= 0xFFFF:
        raise ValueError(f"工作流步骤数超出上限 65536：{index}")
    seed = uuid_module.UUID(workflow_uuid_value).hex
    return (
        f"{seed[:8]}-{index:04x}-4{seed[8:11]}-8{seed[11:14]}-{seed[14:26]}"
    )


def _action_type_from_registry(klass: str, action: str) -> str:
    """从 registry 设备类条目查动作类型（UniLabJsonCommand 等）；未知返回空。"""

    try:
        from unilabos.registry.registry import lab_registry
    except Exception:  # noqa: BLE001 - registry 不可用时按空类型透传
        return ""
    if lab_registry is None:
        return ""
    entry = lab_registry.device_type_registry.get(klass) or {}
    mappings = entry.get("class", {}).get("action_value_mappings", {}) or {}
    mapping = mappings.get(action)
    if not isinstance(mapping, Mapping):
        return ""
    return str(mapping.get("type") or "")


def build_workflow_payload(
    definition: WorkflowDefinition,
    catalog: DeviceCatalog,
) -> Dict[str, Any]:
    """把 @workflow 定义构建为权威可落库的 node-link 载荷。

    Returns:
        {"workflow_uuid", "name", "description", "tags", "nodes", "edges"}
    """

    steps = definition.collect_steps()
    nodes: List[Dict[str, Any]] = []
    for index, step in enumerate(steps):
        if step.kind == "run_template":
            device_id = catalog.resolve_class(step.target)
            klass = step.target
        else:
            device_id = step.target
            info = catalog.by_device_id.get(device_id) or {}
            klass = str(info.get("class") or "")
        action_type = _action_type_from_registry(klass, step.action) if klass else ""
        # 声明式步骤严格串行：每步依赖上一步（handle 连线属于节点模板体系，
        # 执行序依赖走 execution_policy，由调度器翻译成 DAG 边）。
        execution_policy: Dict[str, Any] = {}
        if index > 0:
            execution_policy["depends_on"] = [
                _step_node_uuid(definition.uuid, index - 1)
            ]
        meta_data: Dict[str, Any] = {"target_device_id": device_id}
        if step.inventory:
            meta_data["inventory_requirements"] = [dict(item) for item in step.inventory]
        nodes.append(
            {
                "uuid": _step_node_uuid(definition.uuid, index),
                "name": step.name,
                "type": "device_action",
                "material_uuid": catalog.material_uuid_of(device_id),
                "action_name": step.action,
                "action_type": action_type,
                "param": dict(step.params),
                "meta_data": meta_data,
                "pose": {},
                "execution_policy": execution_policy,
            }
        )
    return {
        "workflow_uuid": definition.uuid,
        "name": definition.display_name,
        "description": definition.description,
        "tags": list(definition.tags),
        "nodes": nodes,
        "edges": [],
    }


# ---------------------------------------------------------------------------
# 上报（host 启动时 upsert 到 Workflow Authority）
# ---------------------------------------------------------------------------


def import_workflow_modules(module_paths: Sequence[str]) -> None:
    """import 含 @workflow 的模块，触发装饰器注册；单个失败不影响其余。"""

    for module_path in dict.fromkeys(module_paths):
        try:
            importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001 - 上报是尽力而为
            logger.warning(f"[Workflow] 导入工作流模块 {module_path} 失败: {exc}")


def report_workflows_to_service(
    service: Any,
    catalog: DeviceCatalog,
    *,
    definitions: Optional[Mapping[str, WorkflowDefinition]] = None,
) -> Dict[str, str]:
    """把已注册的 @workflow 幂等 upsert 到本机 Workflow Authority。

    Returns:
        成功上报的 {workflow_uuid: display_name}；单个失败只告警不中断。
    """

    reported: Dict[str, str] = {}
    for definition in (definitions or get_registered_workflows()).values():
        try:
            payload = build_workflow_payload(definition, catalog)
            _upsert_workflow(service, payload)
        except Exception as exc:  # noqa: BLE001 - 单个工作流失败不阻断启动
            logger.warning(
                f"[Workflow] 上报工作流 {definition.source_path} 失败: {exc}"
            )
            continue
        reported[definition.uuid] = definition.display_name
        logger.info(
            f"[Workflow] 已上报默认子工作流 {definition.display_name}"
            f"（uuid={definition.uuid}，{len(payload['nodes'])} 步）"
        )
    return reported


def _upsert_workflow(service: Any, payload: Dict[str, Any]) -> None:
    """create-or-update：uuid 已存在则取当前 revision 覆盖节点图。"""

    workflow_uuid_value = payload["workflow_uuid"]
    try:
        record = service.create_workflow(
            name=payload["name"],
            tags=payload["tags"],
            description=payload["description"] or None,
            meta_data={},
            workflow_uuid=workflow_uuid_value,
        )
    except Exception as create_error:  # noqa: BLE001 - 已存在（conflict）则走更新
        try:
            record = service.get_workflow(workflow_uuid_value)
        except Exception as lookup_error:  # noqa: BLE001
            # 不是"已存在"：把 create 的真实错误抛出去，别让 404 盖住格式/校验问题
            raise create_error from lookup_error
        service.update_workflow(
            workflow_uuid_value,
            name=payload["name"],
            tags=payload["tags"],
            description=payload["description"] or None,
            meta_data=record.get("meta_data") or {},
        )
        record = service.get_workflow(workflow_uuid_value)
    service.save_graph(
        workflow_uuid_value,
        revision=record["revision"],
        nodes=payload["nodes"],
        edges=payload["edges"],
    )


__all__ = [
    "WORKFLOW_NAMESPACE",
    "DeviceCatalog",
    "WorkflowBuildContext",
    "WorkflowDefinition",
    "WorkflowStep",
    "build_workflow_payload",
    "clear_registered_workflows",
    "get_registered_workflows",
    "import_workflow_modules",
    "report_workflows_to_service",
    "workflow",
    "workflow_uuid_for",
]
