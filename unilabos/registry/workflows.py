"""@workflow 装饰器：把 Python 函数声明为设备包自带的**工作流模板**。

设备包用 ``@workflow`` 声明模板，函数体内通过 ctx 描述步骤：

    from unilabos.registry.workflows import WorkflowGuide, workflow

    @workflow(
        display_name="演示闭环",
        description="终止并重启一轮计数",
        guide=WorkflowGuide(
            preparation=["设备页确认 sub_reporter 在线", "物料仓储出库一块板，挂到 bench 的 T1 位"],
            expected=["计数从 0 重新开始", "台面报告里 T1 有板"],
        ),
    )
    def demo_flow(ctx):
        ctx.run("sub_reporter/stop_counting", {}, name="停止计数", description="计数器归零")
        ctx.run_template("status_reporter_demo/start_counting", {}, name="重新计数")

- ``ctx.run("device_id/action_name", params)``：角色 = 显式设备 id。
- ``ctx.run_template("class_name/action_name", params)``：角色 = registry 设备类；
  实例化时该类恰有一个设备则自动填充，否则由用户绑定。
- ``with ctx.loop_for(3):`` / ``with ctx.loop_while(condition):``：块内的步骤是循环体，
  运行时由调度器逐轮执行（不是配置时展开）。``for`` 固定轮数；``while`` 每轮前判定条件——
  ``ctx.device_state("heater", "temperature_c", "<", 80)`` 看设备状态字段，
  ``ctx.step_output(step, "ok", "==", False)`` 看某一步最近的返回值。循环体参数里可以写
  ``{{loop.index}}`` / ``{{loop.iteration}}`` / ``{{loop.count}}`` 引用当前轮次。
- ``guide`` + 每步的 ``name`` / ``description`` 组成前端模板卡片上的"全流程"说明：
  运行前该在前端做什么 → 每一步做什么 → 跑完该看到什么。
- 两者都接受 ``inventory=[...]``：该步骤的库存需求（``InventoryRequirement``
  形态，如 ``{"key": "water", "kind": "lot", "lot_uuid": ..., "quantity": 40,
  "unit": "ml"}``），实例化后写入节点 ``meta_data.inventory_requirements``；调度器在
  任务启动时 all-or-nothing 预留，数量不足则任务在派发前失败（``plan_not_executable``），
  动作开始时由执行面扣减。

模板不是工作流：它和 device / resource 条目一样随注册表快照上报到 Registry
Authority（``registry_type="workflow"``，同一套版本化 / 软移除），前端"工作流模板"
面板把它和内置模板、用户模板并列，插入画布时把角色绑到实际设备；脚本 / e2e 走
``POST /api/v1/workflows/from-template`` 由权威按绑定实例化成可运行的工作流。

模板 uuid 由函数相对路径（``module:qualname``）经 uuid5 派生，跨进程 / 跨机器稳定；
实例化出的工作流 uuid 由模板 uuid + 角色绑定派生，同一组设备反复实例化幂等覆盖。

实例化后的步骤节点不画 handle 连线（handle 边属于节点模板体系，声明式步骤没有
数据流），执行序依赖写在节点 ``execution_policy.depends_on``（上一步节点 uuid），
调度器把它翻译成 DAG 依赖边——同一层级的步骤严格按声明序串行执行；循环体是
``parent_uuid`` 指向循环节点的子节点，循环节点作为一个整体排在它的同级序列里。
节点 uuid 按步骤序构造，拓扑序 == 声明序。
"""

from __future__ import annotations

import importlib
import uuid as uuid_module
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence

from unilabos.protocol.materials import InventoryRequirement
from unilabos.protocol.runtime.loop import LOOP_NODE_TYPE, parse_loop_spec
from unilabos.utils.log import logger

#: 模板里 node_output 条件引用的是步骤 key（``step-N``），实例化时换成节点 uuid；
#: 校验时用这个占位 uuid 顶替。
_CONDITION_NODE_PLACEHOLDER = "00000000-0000-4000-8000-000000000001"

#: uuid5 命名空间：Uni-Lab 工作流（固定值，保证跨进程/跨机器一致）。
WORKFLOW_NAMESPACE = uuid_module.uuid5(uuid_module.NAMESPACE_URL, "unilabos://workflow")

#: 模块级注册表：workflow_uuid -> WorkflowDefinition（import 装饰器即注册）。
_registered_workflows: Dict[str, "WorkflowDefinition"] = {}


def workflow_uuid_for(source_path: str) -> str:
    """按函数相对路径（``module:qualname``）派生稳定 workflow uuid。"""

    return str(uuid_module.uuid5(WORKFLOW_NAMESPACE, source_path))


@dataclass(frozen=True)
class WorkflowStep:
    """一条 ctx.run/ctx.run_template/循环 记录（build 时解析为 workflow 节点）。"""

    kind: str  # "run"（device_id 显式）、"run_template"（class 解析）或 "loop"（循环容器）
    target: str  # device_id 或 class_name；循环为空
    action: str  # 循环为空
    params: Dict[str, Any]  # 循环时是 LoopSpec 的 json 形态（node_output 条件用 node_key 指步骤）
    name: str  # 节点显示名，缺省 f"{target}.{action}"
    #: 已校验的 InventoryRequirement（json 形态），落到节点 meta_data.inventory_requirements
    inventory: List[Dict[str, Any]] = field(default_factory=list)
    #: 这一步做什么 / 该看到什么（前端"全流程"说明里逐步展示）
    description: str = ""
    #: 所在循环步骤在 ctx.steps 里的下标；顶层为 None
    parent: Optional[int] = None
    #: 模板里的步骤 key（``step-N``，N 为声明序号）；条件引用与边都用它
    key: str = ""

    @property
    def is_loop(self) -> bool:
        return self.kind == LOOP_NODE_TYPE


@dataclass(frozen=True)
class WorkflowGuide:
    """模板的操作指引：告诉操作员在前端先做什么、跑完该看到什么。

    - preparation：运行前要在前端完成的准备（如去「物料仓储」出库、把物料挂到哪台
      设备的哪个位点、确认设备在线），按操作顺序写；
    - expected：任务成功后的预期效果（位点占用、库存余量、报告内容……）；
    - notes：注意事项 / 与其它模板的关系（可选）。
    """

    preparation: List[str] = field(default_factory=list)
    expected: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_payload(self) -> Dict[str, List[str]]:
        return {
            "preparation": list(self.preparation),
            "expected": list(self.expected),
            "notes": list(self.notes),
        }

    def is_empty(self) -> bool:
        return not (self.preparation or self.expected or self.notes)


def _normalize_guide(value: Any) -> Optional[WorkflowGuide]:
    """接受 WorkflowGuide 或同形 dict；空指引按 None 处理。"""

    if value is None:
        return None
    if isinstance(value, WorkflowGuide):
        guide = value
    elif isinstance(value, Mapping):
        unknown = set(value) - {"preparation", "expected", "notes"}
        if unknown:
            raise ValueError(f"@workflow guide 只接受 preparation / expected / notes，多了 {sorted(unknown)}")
        guide = WorkflowGuide(
            preparation=_string_list(value.get("preparation"), "preparation"),
            expected=_string_list(value.get("expected"), "expected"),
            notes=_string_list(value.get("notes"), "notes"),
        )
    else:
        raise ValueError("@workflow guide 必须是 WorkflowGuide 或 dict")
    for name in ("preparation", "expected", "notes"):
        _string_list(getattr(guide, name), name)
    return None if guide.is_empty() else guide


def _string_list(value: Any, name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"@workflow guide.{name} 必须是字符串列表")
    items = [str(item).strip() for item in value]
    if any(not item for item in items):
        raise ValueError(f"@workflow guide.{name} 不能有空条目")
    return items


@dataclass(frozen=True)
class WorkflowDefinition:
    """一个 @workflow 函数的注册条目。"""

    uuid: str
    source_path: str  # module:qualname，uuid 的派生源
    display_name: str
    description: str
    tags: List[str]
    fn: Callable[["WorkflowBuildContext"], Any]
    guide: Optional[WorkflowGuide] = None

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
        # 正在声明循环体的循环步骤下标栈（with ctx.loop_for(...) 嵌套）
        self._loop_stack: List[int] = []

    # ── 循环 ─────────────────────────────────────────────────

    @contextmanager
    def loop_for(
        self,
        count: int,
        *,
        name: str = "",
        description: str = "",
        interval_seconds: float = 0.0,
    ) -> Iterator[WorkflowStep]:
        """固定轮数循环：``with ctx.loop_for(3) as loop:`` 块内的步骤是循环体。"""

        spec = {"mode": "for", "count": count, "interval_seconds": interval_seconds}
        with self._loop(spec, name or f"循环 ×{count}", description) as step:
            yield step

    @contextmanager
    def loop_while(
        self,
        condition: Mapping[str, Any],
        *,
        max_iterations: int = 1000,
        interval_seconds: float = 0.0,
        name: str = "",
        description: str = "",
    ) -> Iterator[WorkflowStep]:
        """条件循环：每轮开始前判定 ``condition``（``ctx.device_state`` / ``ctx.step_output`` 构造），
        为真才执行循环体；``max_iterations`` 是安全上限，``interval_seconds`` 是两轮之间的等待。
        空循环体 + 设备状态条件 = "等到某状态"，此时必须给 ``interval_seconds``。
        """

        spec = {
            "mode": "while",
            "condition": dict(condition),
            "max_iterations": max_iterations,
            "interval_seconds": interval_seconds,
        }
        with self._loop(spec, name or "循环 while", description) as step:
            yield step

    @staticmethod
    def device_state(device_id: str, field_name: str, op: str, value: Any = None) -> Dict[str, Any]:
        """while 条件：设备状态字段（``@topic_config`` / status_fields 上报的最新值）。"""

        return {
            "source": "device_state",
            "device_id": str(device_id),
            "field": str(field_name),
            "op": op,
            "value": value,
        }

    @staticmethod
    def step_output(
        step: "WorkflowStep | str", data_key: str, op: str, value: Any = None
    ) -> Dict[str, Any]:
        """while 条件：某一步最近一次成功的返回值；该步没产出前循环体先跑一轮。

        ``step`` 是 ``ctx.run`` 返回的步骤，或步骤的 ``name``——循环体里的步骤在 ``with``
        打开时还没声明，用名字引用（名字须在本工作流内唯一），块结束时解析。
        """

        condition: Dict[str, Any] = {
            "source": "node_output",
            "data_key": str(data_key or ""),
            "op": op,
            "value": value,
        }
        if isinstance(step, WorkflowStep):
            if step.is_loop:
                raise ValueError("step_output 条件必须引用设备动作步骤，不能引用循环")
            if not step.key:
                raise ValueError("step_output 只能引用由 ctx.run / ctx.run_template 返回的步骤")
            condition["node_key"] = step.key
        else:
            name = str(step or "").strip()
            if not name:
                raise ValueError("step_output 需要步骤对象或非空步骤名")
            condition["node_name"] = name
        return condition

    @contextmanager
    def _loop(
        self, spec: Dict[str, Any], name: str, description: str
    ) -> Iterator[WorkflowStep]:
        condition = spec.get("condition")
        if isinstance(condition, Mapping) and ("node_key" in condition or "node_name" in condition):
            # node_output 条件在模板里引用步骤 key：校验时先顶上占位 uuid
            check_condition = {
                key: value for key, value in condition.items() if key not in {"node_key", "node_name"}
            }
            check_condition["node_uuid"] = _CONDITION_NODE_PLACEHOLDER
            check = dict(spec, condition=check_condition)
        else:
            check = spec
        parse_loop_spec(check)
        index = len(self.steps)
        step = WorkflowStep(
            kind=LOOP_NODE_TYPE,
            target="",
            action="",
            params=dict(spec),
            name=name,
            description=str(description or "").strip(),
            parent=self._loop_stack[-1] if self._loop_stack else None,
            key=_step_key(index),
        )
        self.steps.append(step)
        self._loop_stack.append(index)
        try:
            yield step
        finally:
            self._loop_stack.pop()
        body = [item for item in self.steps if item.parent == index]
        if not body and spec["mode"] == "while" and float(spec.get("interval_seconds") or 0) <= 0:
            raise ValueError(
                f"循环 {name!r} 没有循环体且未设置 interval_seconds：空 while 需要轮询间隔"
            )
        if not body and spec["mode"] == "for":
            raise ValueError(f"循环 {name!r} 没有循环体")
        if isinstance(condition, Mapping) and "node_name" in condition:
            # 按名字引用的步骤到块结束才全部声明完，此时解析成步骤 key
            target = str(condition["node_name"])
            matches = [item for item in self.steps if item.name == target and not item.is_loop]
            if len(matches) != 1:
                raise ValueError(
                    f"循环 {name!r} 的条件引用步骤 {target!r}："
                    f"{'找不到该步骤' if not matches else '有多个同名步骤'}"
                )
            resolved = {key: value for key, value in condition.items() if key != "node_name"}
            resolved["node_key"] = matches[0].key
            step.params["condition"] = resolved

    def run(
        self,
        target: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        name: str = "",
        description: str = "",
        inventory: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> WorkflowStep:
        """按 ``"device_id/action_name"`` 追加一步（显式设备实例）。

        ``description`` 写这一步做什么 / 操作员该看到什么，进模板的"全流程"说明。
        """

        return self._append("run", target, params, name, description, inventory)

    def run_template(
        self,
        target: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        name: str = "",
        description: str = "",
        inventory: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> WorkflowStep:
        """按 ``"class_name/action_name"`` 追加一步（设备类解析，单实例自动填）。"""

        return self._append("run_template", target, params, name, description, inventory)

    def _append(
        self,
        kind: str,
        target: str,
        params: Optional[Dict[str, Any]],
        name: str,
        description: str,
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
            description=str(description or "").strip(),
            parent=self._loop_stack[-1] if self._loop_stack else None,
            key=_step_key(len(self.steps)),
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
    guide: Optional[Any] = None,
):
    """把模块级函数注册为设备包自带的工作流模板。

    Args:
        display_name: 模板显示名（必填）。
        description: 一句话描述（模板卡片上展示）。
        tags: 模板标签。
        guide: 操作指引（``WorkflowGuide`` 或同形 dict）：``preparation`` 运行前要在前端做的
            准备（出库、挂到哪台设备哪个位点、确认设备在线……）、``expected`` 预期效果、
            ``notes`` 注意事项。配合每步的 ``description`` 组成前端的"全流程"说明。
    """

    if not str(display_name).strip():
        raise ValueError("@workflow 必须提供非空 display_name")
    normalized_guide = _normalize_guide(guide)

    def decorator(fn: Callable[[WorkflowBuildContext], Any]):
        source_path = f"{fn.__module__}:{fn.__qualname__}"
        definition = WorkflowDefinition(
            uuid=workflow_uuid_for(source_path),
            source_path=source_path,
            display_name=str(display_name).strip(),
            description=str(description or ""),
            tags=[str(tag) for tag in (tags or [])],
            fn=fn,
            guide=normalized_guide,
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

    @classmethod
    def from_materials_service(cls, materials: Any) -> "DeviceCatalog":
        """从物料权威提取设备清单：设备根物料的 resource_id 即 device_id，
        template_name 即 registry 设备类（Host 与 Slave 的设备都对齐在权威里，
        调度权威进程无需再问 Host 就能解析类角色）。"""

        catalog = cls()
        if materials is None:
            return catalog
        for aggregate in materials.list_materials(roots_only=True):
            material = getattr(aggregate, "material", aggregate)
            if str(getattr(material, "resource_type", "") or "") != "device":
                continue
            klass = str(getattr(material, "template_name", "") or "")
            device_id = str(getattr(material, "resource_id", "") or "")
            if klass and device_id:
                catalog.add(device_id, klass, str(material.material_uuid))
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


# ---------------------------------------------------------------------------
# 模板载荷（注册表条目）
# ---------------------------------------------------------------------------

#: 注册表条目类型：与 device / resource 并列，随包一起上报、版本化、软移除。
WORKFLOW_TEMPLATE_REGISTRY_TYPE = "workflow"
#: 步骤节点在模板里的 key 前缀；模板 edges 用它串成声明序链。
_STEP_KEY_PREFIX = "step-"


def class_role_id(class_name: str) -> str:
    """``run_template`` 步骤的角色 id：与设备 id 角色分命名空间，避免同名合并。"""

    return f"class:{class_name}"


def _step_key(index: int) -> str:
    return f"{_STEP_KEY_PREFIX}{index}"


def workflow_package_name(source_path: str) -> str:
    """模板所属设备包：``module:qualname`` 里模块路径的顶层包名（即挂载给注册表的包目录名）。"""

    module = str(source_path).partition(":")[0]
    return module.partition(".")[0] or module


def build_workflow_template_payload(definition: WorkflowDefinition) -> Dict[str, Any]:
    """把 @workflow 定义投影成注册表里的工作流模板条目（不依赖任何设备图）。

    模板与前端"工作流模板"同一形状：动作节点用**角色**占位，``run`` 的角色是
    显式设备 id，``run_template`` 的角色是设备类（插入 / 实例化时再绑到具体设备）；
    步骤按声明序用 edges 串成链。``id`` 是注册表条目名（``module:qualname``），
    ``uuid`` 是跨机器稳定的模板身份。
    """

    steps = definition.collect_steps()
    roles: Dict[str, Dict[str, Any]] = {}
    nodes: List[Dict[str, Any]] = []
    for index, step in enumerate(steps):
        node: Dict[str, Any]
        if step.is_loop:
            node = {
                "key": _step_key(index),
                "kind": LOOP_NODE_TYPE,
                "name": step.name,
                "param": dict(step.params),
            }
        else:
            if step.kind == "run_template":
                role = class_role_id(step.target)
                roles.setdefault(
                    role,
                    {
                        "role": role,
                        "label": step.target,
                        "kind": "class",
                        "device_class": step.target,
                        "matches": [step.target],
                    },
                )
            else:
                role = step.target
                roles.setdefault(
                    role,
                    {
                        "role": role,
                        "label": step.target,
                        "kind": "device",
                        "device_id": step.target,
                        "matches": [step.target],
                    },
                )
            node = {
                "key": _step_key(index),
                "kind": "action",
                "role": role,
                "action_name": step.action,
                "name": step.name,
                "param": dict(step.params),
            }
            if step.inventory:
                node["inventory_requirements"] = [dict(item) for item in step.inventory]
        if step.description:
            node["description"] = step.description
        if step.parent is not None:
            node["parent"] = _step_key(step.parent)
        nodes.append(node)
    # 同一层级（顶层 / 同一循环体）的步骤按声明序串成链；循环节点作为整体排在它的同级里
    edges: List[Dict[str, str]] = []
    previous_by_parent: Dict[Optional[int], int] = {}
    for index, step in enumerate(steps):
        previous = previous_by_parent.get(step.parent)
        if previous is not None:
            edges.append({"source": _step_key(previous), "target": _step_key(index)})
        previous_by_parent[step.parent] = index
    payload: Dict[str, Any] = {
        "id": definition.source_path,
        "registry_type": WORKFLOW_TEMPLATE_REGISTRY_TYPE,
        "uuid": definition.uuid,
        "display_name": definition.display_name,
        "description": definition.description,
        "tags": list(definition.tags),
        # 前端"设备包"徽标展示来源包；module 供排查定义出处
        "package": workflow_package_name(definition.source_path),
        "module": definition.source_path.partition(":")[0],
        "roles": list(roles.values()),
        "nodes": nodes,
        "edges": edges,
    }
    if definition.guide is not None:
        payload["guide"] = definition.guide.to_payload()
    return payload


def import_workflow_modules(module_paths: Sequence[str]) -> None:
    """import 含 @workflow 的模块，触发装饰器注册；单个失败不影响其余。"""

    for module_path in dict.fromkeys(module_paths):
        try:
            importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001 - 上报是尽力而为
            logger.warning(f"[Workflow] 导入工作流模块 {module_path} 失败: {exc}")


def collect_workflow_templates(registry: Any) -> List[Dict[str, Any]]:
    """把 Registry 扫描到的 @workflow 全部构建成模板条目（随注册表快照上报）。

    单个定义构建失败（步骤声明错误）只告警并跳过，不影响其余条目与设备 / 资源上报。
    """

    workflow_meta = getattr(registry, "workflow_registry", None) or {}
    if not workflow_meta:
        return []
    import_workflow_modules(
        [str(meta.get("module")) for meta in workflow_meta.values() if meta.get("module")]
    )
    templates: List[Dict[str, Any]] = []
    for definition in get_registered_workflows().values():
        try:
            templates.append(build_workflow_template_payload(definition))
        except Exception as exc:  # noqa: BLE001 - 单个模板失败不阻断上报
            logger.warning(f"[Workflow] 构建工作流模板 {definition.source_path} 失败: {exc}")
    templates.sort(key=lambda item: str(item["id"]))
    return templates


# ---------------------------------------------------------------------------
# 模板实例化（角色 -> 设备，生成可落库的 node-link 工作流）
# ---------------------------------------------------------------------------


class WorkflowTemplateBindingError(ValueError):
    """模板角色无法绑定到设备（缺绑定 / 类无实例或多实例）。"""


def resolve_template_bindings(
    template: Mapping[str, Any],
    catalog: DeviceCatalog,
    bindings: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """把模板角色解析成 device_id：显式绑定优先；设备角色默认就是其设备 id；
    类角色在目录里恰有一个实例时自动填充，否则必须显式绑定。"""

    explicit = {str(key): str(value).strip() for key, value in (bindings or {}).items()}
    unknown = sorted(set(explicit) - {str(role.get("role")) for role in template.get("roles") or []})
    if unknown:
        raise WorkflowTemplateBindingError(f"模板没有这些角色：{unknown}")
    resolved: Dict[str, str] = {}
    for role in template.get("roles") or []:
        role_id = str(role.get("role") or "")
        device_id = explicit.get(role_id, "")
        if not device_id:
            if role.get("kind") == "class":
                try:
                    device_id = catalog.resolve_class(str(role.get("device_class") or ""))
                except ValueError as exc:
                    raise WorkflowTemplateBindingError(
                        f"角色 {role_id} 未绑定设备，且无法自动解析：{exc}"
                    ) from exc
            else:
                device_id = str(role.get("device_id") or role.get("label") or "")
        if not device_id:
            raise WorkflowTemplateBindingError(f"角色 {role_id} 未绑定设备")
        resolved[role_id] = device_id
    return resolved


def instantiated_workflow_uuid(template_uuid: str, resolved: Mapping[str, str]) -> str:
    """同一模板 + 同一组设备绑定 => 同一工作流 uuid，反复实例化幂等覆盖而不堆积。"""

    seed = "|".join(f"{role}={resolved[role]}" for role in sorted(resolved))
    return str(uuid_module.uuid5(WORKFLOW_NAMESPACE, f"{template_uuid}|{seed}"))


def materialize_workflow_template(
    template: Mapping[str, Any],
    catalog: DeviceCatalog,
    bindings: Optional[Mapping[str, str]] = None,
    *,
    name: str = "",
) -> Dict[str, Any]:
    """把模板按角色绑定实例化为权威可落库的 node-link 载荷。

    Returns:
        {"workflow_uuid", "name", "description", "tags", "nodes", "edges", "bindings"}
    """

    resolved = resolve_template_bindings(template, catalog, bindings)
    workflow_uuid_value = instantiated_workflow_uuid(str(template["uuid"]), resolved)
    template_nodes = list(template.get("nodes") or [])
    key_to_index = {str(node.get("key")): index for index, node in enumerate(template_nodes)}
    depends_on: Dict[int, List[str]] = {}
    for edge in template.get("edges") or []:
        source = key_to_index.get(str(edge.get("source")))
        target = key_to_index.get(str(edge.get("target")))
        if source is None or target is None:
            raise ValueError(f"模板边引用了不存在的节点：{edge}")
        depends_on.setdefault(target, []).append(_step_node_uuid(workflow_uuid_value, source))

    def node_uuid_of_key(key: Any) -> str:
        index = key_to_index.get(str(key))
        if index is None:
            raise ValueError(f"模板引用了不存在的节点 key：{key!r}")
        return _step_node_uuid(workflow_uuid_value, index)

    nodes: List[Dict[str, Any]] = []
    for index, node in enumerate(template_nodes):
        kind = str(node.get("kind") or "")
        # 声明式步骤严格串行：执行序依赖走 execution_policy，由调度器翻译成 DAG 边
        # （handle 连线属于节点模板体系，声明式步骤没有数据流）。
        execution_policy: Dict[str, Any] = {}
        if depends_on.get(index):
            execution_policy["depends_on"] = list(depends_on[index])
        parent_uuid = node_uuid_of_key(node["parent"]) if node.get("parent") else None
        if kind == LOOP_NODE_TYPE:
            param = dict(node.get("param") or {})
            condition = param.get("condition")
            if isinstance(condition, Mapping) and "node_key" in condition:
                # 模板里按步骤 key 引用，落库时换成真实节点 uuid
                condition = dict(condition)
                condition["node_uuid"] = node_uuid_of_key(condition.pop("node_key"))
                param["condition"] = condition
            parse_loop_spec(param)
            entry: Dict[str, Any] = {
                "uuid": _step_node_uuid(workflow_uuid_value, index),
                "name": str(node.get("name") or "循环"),
                "type": LOOP_NODE_TYPE,
                "param": param,
                "meta_data": {},
                "pose": {},
                "execution_policy": execution_policy,
            }
        elif kind == "action":
            device_id = resolved[str(node.get("role") or "")]
            klass = str((catalog.by_device_id.get(device_id) or {}).get("class") or "")
            action = str(node.get("action_name") or "")
            meta_data: Dict[str, Any] = {"target_device_id": device_id}
            if node.get("inventory_requirements"):
                meta_data["inventory_requirements"] = [
                    dict(item) for item in node["inventory_requirements"]
                ]
            entry = {
                "uuid": _step_node_uuid(workflow_uuid_value, index),
                "name": str(node.get("name") or f"{device_id}.{action}"),
                "type": "device_action",
                "material_uuid": catalog.material_uuid_of(device_id),
                "action_name": action,
                "action_type": _action_type_from_registry(klass, action) if klass else "",
                "param": dict(node.get("param") or {}),
                "meta_data": meta_data,
                "pose": {},
                "execution_policy": execution_policy,
            }
        else:
            raise ValueError(f"模板节点 {node.get('key')!r} 不是设备动作或循环，无法实例化")
        if node.get("description"):
            entry["description"] = str(node["description"])
        if parent_uuid is not None:
            entry["parent_uuid"] = parent_uuid
        nodes.append(entry)
    return {
        "workflow_uuid": workflow_uuid_value,
        "name": str(name or template.get("display_name") or template.get("id") or ""),
        "description": str(template.get("description") or ""),
        "tags": [str(tag) for tag in (template.get("tags") or [])],
        "nodes": nodes,
        "edges": [],
        "bindings": resolved,
    }


def upsert_workflow(service: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """create-or-update：uuid 已存在则取当前 revision 覆盖节点图；返回工作流记录。"""

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
    return service.get_workflow(workflow_uuid_value)


__all__ = [
    "WORKFLOW_NAMESPACE",
    "WORKFLOW_TEMPLATE_REGISTRY_TYPE",
    "DeviceCatalog",
    "WorkflowBuildContext",
    "WorkflowDefinition",
    "WorkflowGuide",
    "WorkflowStep",
    "WorkflowTemplateBindingError",
    "build_workflow_template_payload",
    "class_role_id",
    "clear_registered_workflows",
    "collect_workflow_templates",
    "get_registered_workflows",
    "import_workflow_modules",
    "instantiated_workflow_uuid",
    "materialize_workflow_template",
    "resolve_template_bindings",
    "upsert_workflow",
    "workflow_package_name",
    "workflow",
    "workflow_uuid_for",
]
