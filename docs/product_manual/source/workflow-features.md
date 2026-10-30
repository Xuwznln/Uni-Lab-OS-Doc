# 工作流编排特性

:::{admonition} 阅读角色
- **业务负责人**：确认实验目的、输入输出、参数单位、成功标准和失败处理。
- **开发人员**：编写或编排节点、物料流、控制结构、设备绑定和正式合同。
- **验收人员**：执行静态检查、预检、模拟运行、异常路径和受控真机验收。
:::

本页是工作流作者和 AI 代码审查者的特性地图。每一节都说明“解决什么问题、怎样写、运行时怎样解释、有哪些边界”。示例采用通用名称，使用时必须替换为用户设备包中实际存在的设备、资源和动作；完整闭环见[用 AI 编写工作流（推荐）](ai-workflow-authoring.md)。

## 特性总览

| 特性 | 作者写法 | 运行含义 | 常见用途 |
| --- | --- | --- | --- |
| 类型化输入 | `Annotated`、`Literal`、`ResourceSlot` | 生成表单并在提交前校验 | 数值范围与物料选择 |
| 动作与数据边 | `result = device.action(...)` | 创建 Job；结果引用形成依赖 | 设备结果驱动下一步 |
| 展示分组 | `with group(name=...)` | 组织画布，不是屏障 | 按工艺阶段分组 |
| 条件 | 原生 `if / elif / else` | 只物化命中分支，其他分支跳过 | 根据检测结果分流 |
| 有界循环 | `repeat_until` + `loop.next` + `until` | 按轮惰性创建 Job，达到条件或上限退出 | 重复处理直到合格 |
| 并行 | `parallel()` 下至少两个 group | DAG 提供并行机会；资源锁仍可串行化 | 两件独立物料并行处理 |
| 结构化资源 | `resources("alias")` | 一段节点共同持有资源区间 | 编译器合同与测试 |
| 物料来源 | `material_source(...)` | 创建/选择物料并声明保管语义 | 样品与容器来源 |
| 动态库位 | `site_group(...)` | Task 创建时冻结候选站点 | 多库位工站选择 |
| 数量要求 | `quantity_requirement(...)` | Task 接纳时预留数量，消费后记账 | 加液体积 |
| 人工确认 | `@action(node_type=NodeType.MANUAL_CONFIRM)` | 取得资源后等待批准，再下发同一 Job | 需要人员放行的设备动作 |
| 子工作流 | 调用已发布 `experiment_operation` | 按固定合同展开并冻结执行图 | 标准转运操作 |

## 类型化输入

参数必须位于 `*` 之后且有类型。`Annotated` 和 `Field` 可以把产品约束写进合同：

```python
from typing import Annotated, Literal

from pydantic import Field


def control_flow(
    *,
    stop_after: Annotated[
        int,
        Field(
            title="退出轮次",
            description="到达该轮次后结束",
            ge=1,
            le=10,
        ),
    ] = 3,
    route: Literal["sample", "blank"] = "sample",
):
    ...
```

当前 `Field` 支持 `title`、`description`、`unit`、`ge`、`le`、`min_length` 和 `max_length`。Uni-Lab OS 会把整数、数值、字符串和枚举约束映射为表单校验；对象和数组使用 JSON 输入。

使用 `ResourceSlot` 时，用户选择的是一件完整 PLR Resource 引用，不是随意字符串：

```python
from typing import Annotated

from unilabos.registry.annotations import AllowedResourceTemplates
from unilabos.registry.placeholder_type import ResourceSlot
from example_device_package.resources.materials import beaker_500ml


def transfer(
    *,
    sample: Annotated[
        ResourceSlot,
        AllowedResourceTemplates(beaker_500ml),
    ],
):
    ...
```

允许模板应引用当前 Catalog 中的真实资源模板对象；不要用随手编写的字符串代替。

## 设备动作与结果引用

规范动作形状固定为：

```python
# unilab:node_uuid=<唯一 UUID>
result = selector.action_name(
    parameter_a=workflow_input,
    parameter_b=previous.output,
)
```

规则：

- selector 在模块级用 `device(...)` 声明；
- 动作必须赋给一个简单变量；
- 只接受命名参数；
- 参数只能来自 JSON 字面量、工作流输入、前序输出、循环 carry 或受支持的资源标记；
- 读取 `previous.output` 会建立真实数据边；
- 未知动作、缺参数或类型不兼容按失败关闭处理。

普通标量或结构化结果可以供多个后继读取；后继只有在所有数据和控制前提满足后才进入可调度集合。`ResourceSlot` 另有物理线性约束：可沿严格有序的动作链连续使用，但不能同时流向多个彼此无先后的物理消费者。

## 展示分组 `group`

```python
from unilabos.workflow.authoring import group


# [样品预处理]: 把两步工艺显示为一个区域
# unilab:node_uuid=<分组 UUID>
with group(name="Preparation"):
    # unilab:node_uuid=<动作 UUID>
    prepared = reactor.prepare(sample=sample)
```

`group` 只保存展示和源码组织信息，不创建执行节点，也不是串并行屏障。名称必须是非空字符串；分组至少包含一个可执行节点；当前不支持展示 group 直接嵌套另一个展示 group。

## 条件 `if / elif / else`

下面是 用户设备包 已登记的无硬件副作用条件示例：

```python
# unilab:node_uuid=07880b05-d9a4-4290-8614-00554cac071a
if observed.value:
    # unilab:node_uuid=5b867034-42c0-4442-a9ac-fa22989934c6
    true_branch = control_flow_probe.record_branch(
        branch="true",
        iteration=0,
    )
else:
    # unilab:node_uuid=9f2d1466-db25-421c-a0a0-d3c2f63718f3
    false_branch = control_flow_probe.record_branch(
        branch="false",
        iteration=0,
    )
```

运行时严格求值得到布尔结果，只创建命中分支的动作；未命中分支不会偷偷执行。支持：

- `and`、`or`、`not`；
- `==`、`!=`、`>`、`>=`、`<`、`<=`；
- `+`、`-`、`*`、`/`、`//`、`%`；
- 输入、前序结果的字段与索引；
- `len`、`min`、`max`、`abs`、`round`、`contains`、`get`。

每个实际分支至少包含一个工作流节点。分支内部变量不能在条件外引用；需要汇总结果时，让各分支写入后续动作可统一读取的显式资源，或重构合同，不能依赖普通 Python 的局部变量逃逸。

## 有界循环 `repeat_until`

当前循环是 do-while 语义：先执行一轮，再在循环尾判断。用户设备包 示例：

```python
from unilabos.workflow.authoring import repeat_until, until


# unilab:node_uuid=d75de496-24b9-54be-b719-2f3f5a185875
with repeat_until(max_iterations=10, carry={"iteration": 1}) as loop:
    # unilab:node_uuid=c00fee26-22b7-549e-934f-97d22b76e10e
    prepared = control_flow_probe.prepare_iteration(
        iteration=loop.carry["iteration"],
    )
    # unilab:node_uuid=0ef93d91-e6bc-5be1-9b2f-2b4d3f7f7d0c
    executed = control_flow_probe.execute_iteration(
        iteration=prepared.iteration,
    )
    # unilab:node_uuid=f29d242f-ebf6-5211-b2f6-b24f7447c559
    decision = control_flow_probe.evaluate_iteration(
        iteration=executed.iteration,
        stop_after=stop_after,
    )
    loop.next(iteration=decision.next_iteration)
    until(decision.done)
```

运行时不会提前展开最大轮数，而是每轮惰性物化 Job。约束：

- `max_iterations` 是正整数字面量；
- `carry` 是静态字符串键字典；
- 非空 carry 必须且只能调用一次 `loop.next(...)`，键集合完全一致；
- `until(...)` 是循环体最后一条语句，结果必须是严格布尔；
- 循环体至少有一个节点；
- 循环内动作变量不能在循环外引用；
- 控制结构最大嵌套深度为 8。

达到 `max_iterations` 仍未满足退出条件时，系统按有界循环语义停止继续物化，而不是无限运行。作者应把上限设为工艺允许的安全值，而不是一个“几乎无限”的数字。

## 并行 `parallel`

下面的 `beaker` 和 `sample_bottle` 是两件不同的 `ResourceSlot`；不要把同一物料同时送给两个无序物理分支。

```python
from unilabos.workflow.authoring import group, parallel


with parallel():
    # unilab:node_uuid=<分支 A group UUID>
    with group(name="拍照"):
        # unilab:node_uuid=<拍照动作 UUID>
        image = camera.capture(sample=beaker)

    # unilab:node_uuid=<分支 B group UUID>
    with group(name="开盖"):
        # unilab:node_uuid=<开盖动作 UUID>
        opened = robot.open_cap(sample=sample_bottle)

# unilab:node_uuid=<汇合动作 UUID>
joined = analyser.consume(
    image=image.result,
    opened=opened.result,
)
```

结构要求：`parallel()` 不接受参数，直接子项只能是至少两个 group。分支不能读取同级分支的中间结果；离开 parallel 后才能汇合。

编译器不会制造虚假的 fork/join 设备节点。并行只说明 DAG 允许同时就绪，不保证物理同时执行：如果两个分支绑定同一台设备、同一工位或同一结构化资源，Uni-Lab OS 的锁仍会把它们串行化。例如，可以用并行表达“一只容器拍照”和“另一只容器开盖”的同时执行机会。

## 结构化资源区间 `resources`

```python
from unilabos.workflow.authoring import resources


with resources("sample_preparation_region"):
    # unilab:node_uuid=<动作 1 UUID>
    first = device_a.action(...)
    # unilab:node_uuid=<动作 2 UUID>
    second = device_b.action(input=first.output)
```

`resources(...)` 声明整个区间共同持有的调度资源。别名必须是非空、唯一的字符串字面量；动态变量会被拒绝。它可以嵌套，但不会产生可执行节点。根工作流也可以在装饰器元数据中声明结构化资源。

这是通用资源锁语义，不识别具体业务动作。`resources("resource_a", "resource_b")`
中的参数是资源别名集合；设备、物料、放置位（Site）、区域或工站互斥量只要在冻结计划中绑定为
规范 `lock_key`，都会走同一套申请、等待、Fence 和释放流程。

运行时由**每个 Job 在自己的 pre-dispatch 阶段**复验资源，而不是由前一个 Job
替后继申请动作锁。请求分为两部分：区间内已经连续持有的公共资源（preheld），以及
当前 Job 才需要的新增资源。新增资源必须全有或全无地取得；若其中任一资源冲突，
当前 Job 保持等待，公共区间资源仍不释放。

只有新 Job 的 Permit 已经持久化后，系统才把公共资源从上一 Claim 交接给当前 Claim。
因此，其他 Task 不能在区间中插入任何会使用相交资源的 Job；资源完全不相交的 Task
仍可并行执行。

搬运只是一个应用例子：一段“移动来源库位 → 搬运 → 移动目标库位”若要求机械臂、
来源库位和目标库位在三步之间都不被其他 Task 使用，就应把三者的别名一起声明为
同一 `resources(...)` 区间的公共资源；若要求工站内任何作业都不能插入，则应声明
一个所有相关 Task 都会申请的工站级互斥资源，而不是在运行时写死搬运流程的特殊规则。

区间一旦开始，preheld 必须能对应到同一 Task 的活动前驱 Claim；若重启恢复时交接
凭据缺失、前驱 Claim 已释放或 Fence 无法连续证明，运行时会失败关闭，绝不会把该
资源悄悄当作“新增资源”重新申请后继续执行。

时序图中的“后继作业继承占用，整组申请新增资源”更准确的表述是：
“后继 Job 在自身 pre-dispatch 复验完整资源集，复用区间内已持有资源，并原子取得
本 Job 的新增资源”。这里的“继承”只描述连续所有权，不表示前驱替后继申请资源。

它和 group 的区别：group 管展示，resources 管锁；需要二者时应分别表达。

## 物料来源 `material_source`

物料来源把“这件实验输入从哪里来”写进工作流。下面假设 `source_site` 是该工作流的字符串输入；固定库位也可以直接使用当前 启动图与 Catalog 中的真实 放置位 UUID：

```python
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy,
    MaterialFlowRole,
    material_source,
    resource_ref,
)


# unilab:node_uuid=<物料来源 UUID>
source_solvent = material_source(
    resource_template=liquid_reagent_bottle_100ml,
    mode="existing",
    mount=resource_ref("s10_liquid_reagent"),
    material_uuid=None,
    site=source_site,
    slot_range=None,
    flow_role=MaterialFlowRole.REAGENT,
    custody_policy=MaterialCustodyPolicy.SHARED_SOURCE,
)
```

- `mode="existing"` 选择现有资源；`create_new` 按模板建立新资源；
- `resource_ref("s10_liquid_reagent")` 使用部署资源业务 ID，由库存权威解析实例；
- `site` 可以是 启动图/Catalog 中的稳定 放置位 UUID，也可以是字符串工作流输入；动态形式只适用于 `mode="existing"` 且 `material_uuid=None`，会在创建 Task 时于指定 mount 下解析并冻结；
- `slot_range` 可以列出允许的稳定 放置位 UUID；它不能与固定 `site` 同时使用，但可限制动态 `site` 输入的解析结果；
- `PRIMARY_SAMPLE`、`REAGENT`、`CONSUMABLE` 表达流程角色；
- `TASK_EXCLUSIVE` 表示 Task 独占保管，`SHARED_SOURCE` 适合受数量预留保护的共享来源。

不要在源码中硬猜当前库存实例 UUID；稳定部署身份交给 `resource_ref`，本次具体材料交给输入绑定和库存权威。物料 UUID、资源模板 UUID 与 放置位 UUID 是三种身份，不能互换。

同一 `ResourceSlot` 可以被多个严格有序的动作连续处理，因为每个后继都位于前一个后继之后。若两个物理消费者彼此无依赖，编译器会以 `material_flow_fan_out` 拒绝；真正分样应先调用受支持的 split/aliquot Action，产生不同子物料身份。

## 动态库位 `site_group`

```python
from unilabos.workflow.authoring import site_group


placed = robot.transfer_material_atomic(
    resource=sample,
    target_warehouse=resource_ref("dosing_process_warehouse"),
    target_site=site_group(
        "dosing_container_sites",
        exact=preferred_site,
    ),
)
```

`site_group` 引用部署中的命名站点组。创建 Task 时，系统按当前部署代际解析并冻结候选 UUID；这避免运行中因为布局变化悄悄换位。`exact` 只能直接引用字符串工作流输入或留空，不能引用任意运行表达式。

## 数量预留 `quantity_requirement`

```python
from unilabos.workflow.authoring import quantity_requirement


# unilab:node_uuid=<加液动作 UUID>
added = pump.add_solvent_with_materials(
    solvent_pump_1=source_solvent,
    volume_pump_1=volume_ml,
)

quantity_requirement(
    requirement_key="solvent",
    source=source_solvent,
    consume=added,
    quantity=volume_ml,
    quantity_unit="mL",
    scale=1.0,
    description="本次加液量",
)
```

Task 接纳时会按当前库存建立数量预留，动作结果收敛后再进入消费记账。约束：

- `requirement_key` 在工作流中唯一；
- `source` 直接引用一个 `material_source` 结果；
- `consume` 直接引用一个普通或组合动作结果；
- `quantity` 是有限正数字面量或工作流输入；动态输入为 `0` 时本次不生成要求；
- `scale` 是有限正数字面量；
- 当前不能在条件或循环内部声明数量要求。

数量合同只负责调度与账本，不替代设备测量。指令加液量、泵回执与真实称量应分别保留。

## 人工确认

人工确认不是无设备的独立工作流节点。用户应在设备包中把一个真实设备动作声明为
`NodeType.MANUAL_CONFIRM`，工作流仍按普通设备动作调用它。Uni-Lab OS 会在取得该动作所需资源后等待批准，批准后再执行同一个设备 Job。

### 1. 在设备类中声明“执行前确认”

```python
# example_lab/devices/reactor/device.py
from typing import TypedDict

from unilabos.registry.decorators import NodeType, action, device


class HeatResult(TypedDict):
    status: str
    actual_temperature: float


@device(
    id="example_reactor",
    category=["reaction", "reactor"],
    displayname="反应器",
    description="执行受控升温。",
)
class Reactor:
    @action(
        node_type=NodeType.MANUAL_CONFIRM,
        displayname="确认后升温",
        description="操作员批准后执行升温。",
    )
    def heat(self, temperature: float) -> HeatResult:
        # 此处必须调用具体型号的真实驱动，不能返回假成功。
        result = self._driver.heat(temperature=temperature)
        return {
            "status": result.status,
            "actual_temperature": result.actual_temperature,
        }
```

关键规范只有一条：`node_type=NodeType.MANUAL_CONFIRM` 必须写在真实设备动作的 `@action` 上；动作参数、返回值、资源合同和驱动实现与普通设备动作完全相同。

### 2. 在工作流中正常调用设备动作

```python
# example_lab/experiment_operations/heat_after_confirmation.py
from unilabos.workflow.authoring import device, workflow
from example_lab.devices.reactor.device import HeatResult, Reactor

reactor: Reactor = device("reactor_01")


@workflow(
    workflow_uuid="d25efee5-618f-4ca9-8f27-3a64e35b64ca",
    displayname="反应器升温前人工确认",
    description="人工核对反应器和样品后，再执行升温动作。",
    workflow_type="experiment_operation",
)
def heat_after_confirmation(*, temperature: float = 42.0) -> HeatResult:
    # unilab:node_uuid=41a9ee20-02af-44de-bb45-a78f6647cb8b
    heated = reactor.heat(temperature=temperature)
    return {
        "status": heated.status,
        "actual_temperature": heated.actual_temperature,
    }
```

`reactor_01` 必须是启动图中的真实设备实例。编译器从 `Reactor.heat` 的动作元数据识别人工确认，不需要也不支持 `manual_confirm()` 或 `with manual_confirm()`。

### 3. 通过 Uni-Lab OS API 批准或拒绝

调用任务 Job 查询接口取得等待确认的 `job_uuid`，再通过 Uni-Lab OS HTTP API 提交决定：

```bash
curl "<UNILABOS_URL>/api/v1/workflow-tasks/<TASK_UUID>/jobs"

curl -X POST \
  "<UNILABOS_URL>/api/v1/workflow-node-jobs/<JOB_UUID>/manual-confirmation" \
  -H "Content-Type: application/json" \
  -d '{"action":"approve"}'
```

拒绝时把 `approve` 改为 `reject`。当前 Python 声明使用默认确认超时 3600 秒；拒绝或超时会取消整个 Task，批准后原设备动作只下发一次。

## 子工作流与实验操作

可复用子流程先声明并发布为实验操作：

```python
@workflow(
    workflow_uuid="e7c53119-9fde-5250-9bf5-264f23d157a8",
    displayname="用户设备包 标准物料转运",
    workflow_type="experiment_operation",
)
def s_z_lab_标准物料转运(*, resource: ResourceSlot):
    ...
```

父工作流用绝对导入和命名参数调用：

```python
from example_device_package.experiment_operations.material_transfer import (
    standard_material_transfer,
)


# unilab:node_uuid=<本次调用 UUID>
moved = standard_material_transfer(
    resource=source_beaker,
    source_warehouse=resource_ref("source_container_warehouse"),
    target_device="dosing_station_01",
    target_warehouse=resource_ref("dosing_process_warehouse"),
    source_site="SOURCE_01",
    target_site="PROCESS_01",
)
```

编译器不会真的 import 并运行子模块，而是从目录解析合同。每次调用有独立 invocation 身份；父图冻结子合同 UUID 和摘要。合同损坏、pin 过期或普通 `normal` 工作流被当作子流程时都会失败关闭。

在允许未发布源码的 Runtime 冷启动中，系统会按依赖从子到父应用已登记源码，直到目录达到固定点；无需人为逐个打开子流程。发布、画布插入和运行合同仍要求子流程是已发布的 `experiment_operation`。

## 图编辑与源码往返

Python 是人维护的来源，画布图是它的投影。保存复杂编辑时，系统必须完成“Python → AST → 图 → Python → 图”的语义固定点：两张图等价，Workflow/节点身份、类型 Handle、物料身份与支持的标题说明保持不变，再次生成不继续漂移。

不要用 `# unilab:parallelize`、空 `pass` 或伪造的 Fork/Join 节点保存拓扑。不是所有 DAG 都能由当前结构化语言表达；遇到不可表达的断边或交叉依赖时，应保留原编辑并根据诊断恢复依赖或重构源码，不能把差异藏进注释。

## 常见错误写法

| 不要这样写 | 原因 | 正确方向 |
| --- | --- | --- |
| `python workflow.py` | DSL marker 在普通运行时会报错 | 通过 Uni-Lab OS 页面、API 或 Workspace 编译 |
| `device.action(1, 2)` | 动作不接受位置参数 | `device.action(a=1, b=2)` |
| `result = device.action(**params)` | 不允许动态展开 | 逐个写出命名参数 |
| 在源码中调用 `uuid.uuid4()` | 身份必须可重复解析 | 预先生成并粘贴 UUID 字面量 |
| 分支外读取 `true_branch` | 条件局部结果不逃逸 | 重构显式后继/资源合同 |
| 循环外读取 `decision` | 每轮结果身份不稳定 | 把输出写入允许的资源或重构流程 |
| 用 group 期待加锁 | group 只控制展示 | 使用 `resources(...)` |
| 用 parallel 保证同时动作 | 资源锁仍可串行 | 把它理解为并行机会 |
| 用 magic comment 或空 `pass` 保存断边 | 不是规范拓扑 | 用真实 `group`/`parallel`，不可表达时按诊断重构 |
| `manual_confirm()` | 当前没有这种 Python marker | 在真实设备动作上声明 `@action(node_type=NodeType.MANUAL_CONFIRM)` |
| 调用未发布子操作 | 没有可冻结合同 | 先发布 `experiment_operation` |

## 作者提交前检查表

这份清单同样适用于 AI 生成的源码；AI 自己报告“已检查”不能代替人工复核和产品编译。

- 模块级只包含 import、可选 docstring、设备声明、至多一个输出 `TypedDict` 和一个工作流函数；
- 只使用绝对 import，不使用星号 import；
- 每个可识别结构都有唯一、紧邻的节点 UUID；
- 所有参数是关键字专用且有类型；
- 所有动作与子工作流调用只用命名参数并赋给简单变量；
- 输入、输出和 `ResourceSlot` 合同清楚且不重名；
- 条件、循环、并行和资源区间满足本页结构限制；
- 物料来源、保管策略、站点和数量单位与现场一致；
- 子工作流已发布，父流程重新编译并发布；
- 复杂画布编辑已达到 Python→图→Python→图语义固定点，没有 magic comment、空块或伪节点；
- 先在 `dry-run` 编译、预检和运行，再进入真机验收。
