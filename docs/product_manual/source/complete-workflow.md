# 完整工作流

:::{admonition} 阅读角色
- **业务负责人**：确认实验目的、输入输出、参数单位、成功标准和失败处理。
- **开发人员**：编写或编排节点、物料流、控制结构、设备绑定和正式合同。
- **验收人员**：执行静态检查、预检、模拟运行、异常路径和受控真机验收。
:::

完整工作流面向一次完整实验目标。它负责选择物料、安排实验操作和设备动作、建立先后关系、记录消耗并返回最终结果。

本页用“取出样品 → 搬到混合位 → 混合 → 放回目标位”说明完整写法。示例不代表任何现场设备，所有类型、实例和动作都要替换为用户设备包实际定义。

## 1. 确认启动图（Graph JSON）中的实例

在编写前确认以下 ID 已存在：

| ID | 用途 |
| --- | --- |
| `transport_robot_01` | 执行标准转运 |
| `mixing_station_01` | 执行混合动作 |
| `source_rack` | 样品来源仓库 |
| `mixing_process_rack` | 混合工艺位 |
| `finished_rack` | 完成后的目标仓库 |

还要确认 `sample_container` 已登记为物料模板，实验操作“标准物料转运”已经检查并发布。

## 2. 完整工作流撰写规范

完整工作流应把一次实验从开始条件写到最终结果。它负责组合已登记的物料、设备动作和已发布实验操作，不负责实现底层通信。

### 用业务阶段组织，不按设备清单堆动作

先把实验写成可验收的阶段，例如：

```text
准备物料 → 搬运到工位 → 执行处理 → 检查结果 → 归位 → 输出结果
```

每个阶段说明输入物料、使用设备、完成标志和失败后的现场状态。代码中的 `group(name="...")` 可以对应这些业务阶段，名称应让操作人员能直接理解。分组只改善展示，不会自动建立先后关系；真正的顺序由上下游数据引用和控制结构决定。

### 物料来源集中声明

在流程前部使用 `material_source(...)` 声明任务所需的样品、试剂和耗材。每一项至少明确：

- `resource_template`：允许选择的物料模板；
- `mode`：使用已有物料还是按流程创建；
- `mount`：物料所在仓库或设备资源；
- `site` 或可选范围：允许选择的库位；
- `flow_role`：主样品、试剂或耗材等业务角色；
- `custody_policy`：任务独占还是允许作为共享来源。

`material_uuid` 只在流程明确绑定某一个固定实例时填写。通用流程通常留空，由任务创建时选择合规物料。不要把某次部署生成的内部 UUID 写进通用示例。

### 用真实结果连接步骤

同一物料在整条流程中只能沿设备动作的实际输出继续传递：

```text
source_sample → moved.resource → processed.sample → stored.resource
```

不允许在处理完成后又使用旧的 `source_sample` 继续搬运。旧引用代表处理前的状态，会造成位置和状态记录不一致。试剂瓶、吸头盒、粉料盒等参与动作后，也应继续传递动作返回的对应资源字段。

### 子操作调用

- 只调用已经检查并发布的实验操作；
- 为每次调用分别绑定公开输入、设备实例、资源来源和上游结果；
- 子操作返回什么字段，上层就按该合同读取什么字段，不读取其内部临时变量；
- 子操作已经完成的物料提交、安全检查或重试处理，上层不得重复实现；
- 子操作合同变化后，先验证并发布子操作，再检查和发布完整工作流。

### 参数与固定配置的边界

| 信息 | 放置位置 |
| --- | --- |
| 每次实验会变化的速度、时间、体积、样品编号 | 工作流公开输入 |
| 参数单位、范围、默认值、选项 | 类型注解和 `Field` 约束 |
| 设备地址、串口、凭据、协议超时 | 设备实例配置 |
| 设备、仓库及其父子关系 | 启动图 |
| 设备类型、动作合同、物料模板 | 设备包源码与登记清单 |
| 某次任务选择的具体物料 | 任务输入或 `material_source` 解析结果 |

### 条件、循环和并行

- 条件表达式只读取公开输入或上游节点结果；未选择的分支不能产生物理副作用；
- 循环必须有 `max_iterations` 和可验证的 `until(...)`，并说明达到上限时如何结束；
- 每轮会改变物料或设备状态时，将本轮结果传入下一轮，不能继续使用循环前的旧状态；
- 只有业务上互不依赖、不会同时占用同一物料的步骤才放入 `parallel()`；
- 并行分支共享设备、库位或库存时，仍以 Uni-Lab OS 的资源锁和调度结果为准；
- 需要操作员决定时使用人工确认节点，并设置合理超时，不能用普通 Python `input()`。

### 最终输出和完成标准

返回值应让业务人员能判断实验结果并继续追溯，通常包括：

- 最终样品或产品的 `ResourceSlot`；
- 最终库位或挂载位置；
- 设备实际返回的关键测量值；
- 成功、失败或未知状态及可读消息；
- 需要归档的文件路径、批次号或检测结果。

不得用固定成功值掩盖中间失败。只要关键动作没有得到确定结果，完整工作流就不应继续执行依赖步骤或声明成功。

### 推荐撰写顺序

1. 写清实验目的、开始条件、成功标准和失败处理；
2. 列出物料清单和 启动图 实例清单；
3. 将可复用步骤先写成实验操作并发布；
4. 声明全部公开输入和正式输出；
5. 声明物料来源，再按实际物料流连接动作；
6. 最后添加条件、循环、并行、数量预留和人工确认；
7. 登记 `package.yaml`，运行静态检查、预检和 dry-run；
8. 逐项验证异常路径后，再进行受控真机验收。

## 3. 创建工作流文件

创建：

```text
example_lab/workflows/mix_sample.py
```

## 4. 编写完整示例

```python
from typing import Annotated, TypedDict

from pydantic import Field
from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy,
    MaterialFlowRole,
    device,
    group,
    material_source,
    resource_ref,
    workflow,
)

from example_lab.devices.mixing_station.device import MixingStation
from example_lab.experiment_operations.standard_material_transfer import (
    standard_material_transfer,
)
from example_lab.resources.materials import sample_container


class MixSampleResult(TypedDict):
    sample: ResourceSlot
    final_site: str
    success: bool
    message: str


mixing_station: MixingStation = device("mixing_station_01")


@workflow(
    workflow_uuid="e96ea082-d18d-40da-907d-13cfc5b899af",
    displayname="样品混合与归位",
    description="取得一件样品，完成混合后放入指定完成库位。",
)
def mix_sample(
    *,
    source_site: str,
    target_site: str,
    speed_rpm: Annotated[
        int,
        Field(title="混合速度", description="设备目标转速", ge=100, le=1000),
    ] = 300,
    duration_seconds: Annotated[
        int,
        Field(title="混合时间", description="持续时间，单位秒", ge=1, le=600),
    ] = 30,
) -> MixSampleResult:
    # unilab:node_uuid=2d6ff315-01ac-4df6-9268-27e7c26732d1
    source_sample = material_source(
        resource_template=sample_container,
        mode="existing",
        mount=resource_ref("source_rack"),
        material_uuid=None,
        site=source_site,
        slot_range=None,
        flow_role=MaterialFlowRole.PRIMARY_SAMPLE,
        custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE,
    )

    # unilab:node_uuid=21c18b30-7ab7-4e6f-906c-dd9b33a78685
    with group(name="搬运到混合工站"):
        at_mixer = standard_material_transfer(
            resource=source_sample,
            source_warehouse=resource_ref("source_rack"),
            target_device="mixing_station_01",
            target_warehouse=resource_ref("mixing_process_rack"),
            source_site=source_site,
            target_site="MIX_01",
        )

    # unilab:node_uuid=27489c55-4678-428f-b655-c06bc97b5bfc
    with group(name="执行混合"):
        mixed = mixing_station.mix(
            sample=at_mixer.resource,
            speed_rpm=speed_rpm,
            duration_seconds=duration_seconds,
        )

    # unilab:node_uuid=98e5407f-4368-4f89-ad8f-cbabb76af20c
    with group(name="放入完成区"):
        stored = standard_material_transfer(
            resource=mixed.sample,
            source_warehouse=resource_ref("mixing_process_rack"),
            target_device="host_node",
            target_warehouse=resource_ref("finished_rack"),
            source_site="MIX_01",
            target_site=target_site,
        )

    return {
        "sample": stored.resource,
        "final_site": stored.site,
        "success": mixed.success,
        "message": mixed.message,
    }
```

## 5. 理解依赖关系

代码没有手工画箭头，但数据引用会形成顺序：

```text
source_sample
    ↓
at_mixer.resource
    ↓
mixed.sample
    ↓
stored.resource
```

后一步使用前一步输出，因此只有前一步成功后才会继续。不要绕过 `mixed.sample` 再次使用旧的 `source_sample`，否则物料链会与实际位置不一致。

## 6. 输入参数规范

| 参数 | 类型 | 规范 |
| --- | --- | --- |
| `source_site` | `str` 或受控 `Literal` | 必须属于来源仓库，并且有匹配物料 |
| `target_site` | `str` 或 `site_group` | 必须属于目标仓库且允许放置该模板 |
| `speed_rpm` | `Annotated[int, Field]` | 标题、业务含义、单位和范围明确 |
| `duration_seconds` | `Annotated[int, Field]` | 字段名直接包含单位，设置最大值 |

固定且数量较少的库位可以使用 `Literal["SRC_01", "SRC_02"]`。库位可能动态变化时，使用设备包正式定义的放置位组（Site Group），不要把现场 UUID 硬编码到通用模板。

## 7. 添加并行的原则

只有两条分支操作不同物料，且业务上互不依赖时才使用 `parallel()`：

```python
from unilabos.workflow.authoring import group, parallel

with parallel():
    with group(name="样品检测"):
        sample_result = analyzer.inspect(sample=sample_a)
    with group(name="容器准备"):
        prepared = cap_station.open(container=container_b)
```

同一 `ResourceSlot` 不能同时交给两个物理分支。即使写成并行，共享同一设备、工位或区域时，Uni-Lab OS 仍会按资源锁串行执行。

## 8. 添加数量消耗

加液、投料等动作应分别保留指令量、设备回执和实测量。需要库存预留时，在动作之后声明数量关系：

```python
from unilabos.workflow.authoring import quantity_requirement

quantity_requirement(
    requirement_key="solvent_for_sample",
    source=solvent_source,
    consume=dispensed,
    quantity=volume_ml,
    quantity_unit="mL",
    description="本次样品处理需要的溶剂体积",
)
```

`quantity_requirement` 管理预留和账本，不代替泵、秤或传感器测量。

## 9. 添加条件和循环

- 条件只使用已定义的输入或动作结果；
- 每个分支都说明未命中时的结果；
- 循环使用 `repeat_until`，必须设置 `max_iterations`；
- `until(...)` 使用可验证的布尔结果；
- 循环内有物理副作用时，要明确每轮物料状态和失败恢复。

没有最大次数的“直到成功”会产生不可控运行，不允许进入生产流程。

## 10. 登记与验证

在 `package.yaml` 中加入：

```yaml
- workflow_uuid: e96ea082-d18d-40da-907d-13cfc5b899af
  source: example_lab/workflows/mix_sample.py
```

然后执行：

```bash
python -m compileall example_lab/workflows
unilab package inspect --path . --out /tmp/device-package-inspect

unilab workspace start \
  --workspace . \
  --graph deployment/graphs/simulation.json \
  --runtime-mode dry-run \
  --wait 300 \
  --json
```

在 Uni-Lab OS 中依次完成：检查输入表单 → 发布修订 → 零写入预检 → 创建 dry-run Task → 查看每个 Job 和最终输出。

## 11. 必须验证的失败路径

| 场景 | 预期结果 |
| --- | --- |
| 来源库位没有物料 | 预检失败，不下发设备动作 |
| 目标库位被占用 | 等待或预检失败，不覆盖原物料 |
| 转速或时间越界 | 表单或编译校验失败 |
| 设备离线 | 不派发动作，显示具体设备原因 |
| 搬运超时、结果未知 | 停止后继步骤，要求核对现场，不自动重发 |
| 混合失败 | 不执行“放入完成区”，保留可追踪失败结果 |

## 12. 发布前清单

- [ ] 完整流程有清楚的开始条件和成功标准；
- [ ] 每个输入都有类型、单位、范围和业务说明；
- [ ] 设备选择器与当前 启动图 实例 ID 一致；
- [ ] 每个动作、物料来源和控制结构 UUID 唯一且稳定；
- [ ] `ResourceSlot` 从来源连续传到最终输出；
- [ ] 子工作流已发布，输入输出合同匹配；
- [ ] 共享资源、并行、数量预留和人工确认符合现场规则；
- [ ] 静态检查、预检、dry-run 和失败路径全部通过；
- [ ] 真实运行前已完成设备、物料和安全验收。
