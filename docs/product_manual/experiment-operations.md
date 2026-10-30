# 实验操作（子工作流）

:::{admonition} 阅读角色
- **业务负责人**：确认实验目的、输入输出、参数单位、成功标准和失败处理。
- **开发人员**：编写或编排节点、物料流、控制结构、设备绑定和正式合同。
- **验收人员**：执行静态检查、预检、模拟运行、异常路径和受控真机验收。
:::

实验操作是可以重复使用的业务步骤。例如“把物料从一个库位转移到另一个库位”。它应像一份稳定合同：调用方只需要知道输入、输出和失败含义，不需要了解机器人通信细节。

它在 Uni-Lab OS 的“实验室操作”页面中维护，类型标识为 `experiment_operation`。完整工作流可以引用已经发布的实验操作，但不能把实验操作当作一次完整实验直接运行。

## 1. 什么时候使用

当多个工作流会重复使用同一组动作、参数和资源规则时，应先把这组步骤定义为实验操作。例如“标准物料转运”“开盖后移液”或“一次搅拌”。这样可以统一设备绑定、输入输出和异常处理，避免在多个完整工作流中复制同一段逻辑。

如果一组步骤只属于某一个实验，而且没有独立的业务输入和输出，就直接写在完整工作流中，不必为了拆分而拆分。

## 2. 先定义业务边界

以“标准物料转运”为例：

| 项目 | 定义 |
| --- | --- |
| 输入 | 待搬物料、来源仓库、目标仓库、来源放置位（Site）、目标放置位、目标设备 |
| 执行者 | 启动图（Graph JSON）中的搬运机器人实例 |
| 成功副作用 | 物料实际搬运成功，并唯一一次提交新位置 |
| 输出 | 搬运后的物料、目标挂载资源、目标放置位 和结果说明 |
| 失败 | 不提交计划位置；保留错误并要求核对现场 |

若底层设备只提供分散的 `pick`、`place` 动作，必须先明确断线、重试和位置提交策略。优先由设备包提供能够处理幂等与未知结果的原子搬运动作。

## 3. 实验操作撰写规范

实验操作应写成可以被不同完整工作流重复调用的稳定合同。撰写时必须满足以下要求。

### 职责只保留一个

名称应采用“业务对象 + 业务动作”，例如“标准物料转运”“样品容器开盖”“恒温混合”。一个实验操作只完成一个能够独立说明、独立验收的业务目的。

以下情况应拆分：

- 输入、输出和失败处理可以独立说明；
- 同一段步骤会被两个或更多完整工作流复用；
- 某段步骤需要单独发布、单独验收或单独升级。

不要把“准备全部物料、完成多站加工、生成结果并归位”写成一个实验操作。这属于完整工作流。

### 合同先于实现

编写代码前，先填写以下内容；缺少任何一项都不进入实现：

```text
名称：标准物料转运
目的：把一件物料从来源位安全送到目标位
公开输入：物料、来源仓库、目标仓库、来源放置位、目标放置位、目标设备
公开输出：搬运后的物料、挂载资源、最终 放置位、执行结果
成功副作用：物理搬运成功，并提交一次物料新位置
失败语义：不提交计划位置；结果未知时停止并要求核对现场
使用设备：启动图 中的搬运设备实例
最长等待：由动作合同和现场规程确定
```

### 装饰器和函数

- 使用 `@workflow(...)`；
- `workflow_uuid` 全局唯一，同一实验操作升级时保持不变；
- `displayname` 面向业务人员，不使用内部缩写；
- `description` 写明对象、结果和重要副作用；
- 必须设置 `workflow_type="experiment_operation"`；
- 函数使用关键字专用参数，并声明正式返回类型。

### 输入和输出

| 内容 | 规范 |
| --- | --- |
| 普通参数 | 声明类型、单位、范围、默认值和业务含义 |
| 物料输入 | 使用 `ResourceSlot`；需要限制类型时增加允许的物料模板约束 |
| 设备与仓库 | 绑定 启动图 实例或接收合规资源引用，不写设备类型 ID |
| 结果记录 | 使用命名明确的 `TypedDict` |
| 物料输出 | 返回设备动作确认后的物料字段，不返回调用前的旧引用 |
| 状态输出 | 区分成功、失败和未知；不能只写“已完成” |

输出字段一经被完整工作流使用，就属于发布合同。改变字段名、类型或含义前，要检查所有调用方并发布新的修订。

### 设备动作和物料副作用

- 设备通信、协议重试和原子操作由设备动作实现，实验操作只进行业务组合；
- 设备动作必须从当前设备包的动作目录选择，参数名和返回字段不能凭经验猜测；
- 同一物理变化只能由一个责任方提交。例如原子搬运动作已经提交物料位置，外层实验操作不能再次提交；
- 有开盖、加液、取样、消耗或搬运等副作用时，输出必须反映动作后的真实对象；
- 超时或断线导致结果未知时，不自动重发可能产生重复副作用的动作。

### 节点身份和可读性

每个动作或子操作调用前写一个稳定、唯一的 `node_uuid`。新建节点时生成新 UUID；只调整缩进、注释或展示顺序时保留原 UUID。变量名使用动作后的状态，例如 `opened_bottle`、`transferred_sample`，不要使用 `result1`、`tmp` 等无法审查的名称。

### 复用边界

实验操作只暴露业务调用方真正需要选择的参数。设备地址、账号、串口和协议细节留在设备配置中；固定库位关系留在 启动图 或部署配置中。只有需要由不同完整工作流选择的来源、目标或工艺参数才成为公开输入。

完成后的实验操作必须能够在不阅读内部代码的情况下，仅凭名称、说明、输入、输出和失败语义被正确调用。

## 4. 选择创建方式

实验操作可以通过两种方式创建：流程设计人员可以在“实验室操作”页面编排；开发人员也可以编写静态定义文件后导入。两种方式生成的是同一种实验操作合同，最终都要经过检查和发布。

### 方式一：编写或导入定义文件

创建文件：

创建：

```text
example_lab/experiment_operations/standard_material_transfer.py
```

### 编写结构模板

```python
from typing import TypedDict

from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import device, workflow

from example_lab.devices.transport_robot.device import TransportRobot


class StandardMaterialTransferResult(TypedDict):
    resource: ResourceSlot
    mount_resource: ResourceSlot
    site: str
    result: str


transport_robot: TransportRobot = device("transport_robot_01")


@workflow(
    workflow_uuid="41d51b13-8269-47cc-ad16-a553ed926f11",
    displayname="标准物料转运",
    description="搬运一件物料，并在成功后提交新的库位归属。",
    workflow_type="experiment_operation",
)
def standard_material_transfer(
    *,
    resource: ResourceSlot,
    source_warehouse: ResourceSlot,
    target_device: str,
    target_warehouse: ResourceSlot,
    source_site: str,
    target_site: str,
    check_source_presence: bool = True,
    check_target_presence: bool = True,
    check_gripper_payload: bool = True,
) -> StandardMaterialTransferResult:
    # unilab:node_uuid=8a2d717d-8c45-4d88-8fdc-4b308f69db95
    moved = transport_robot.transfer_material_atomic(
        resource=resource,
        source_warehouse=source_warehouse,
        target_warehouse=target_warehouse,
        target_device=target_device,
        source_site=source_site,
        target_site=target_site,
        check_source_presence=check_source_presence,
        check_target_presence=check_target_presence,
        check_gripper_payload=check_gripper_payload,
    )
    return {
        "resource": moved.resource,
        "mount_resource": moved.mount_resource,
        "site": moved.site,
        "result": moved.result,
    }
```

这段代码是结构模板。`TransportRobot`、实例 ID、动作名、参数和返回字段必须替换为用户设备包实际定义。

### 理解关键写法

| 写法 | 作用 | 约束 |
| --- | --- | --- |
| `TypedDict` | 固定调用方能取得的输出字段 | 字段必须来自真实动作结果 |
| `device("transport_robot_01")` | 绑定 启动图 中的实例 | 填实例 ID，不填设备类型 ID |
| `workflow_type="experiment_operation"` | 允许其他工作流复用 | 发布后才能作为稳定子合同使用 |
| `*` | 强制所有输入使用名称传入 | 避免参数顺序错误 |
| `ResourceSlot` | 传递真实物料或仓库引用 | 不能用普通字符串替代 |
| `node_uuid` | 稳定标识动作节点 | 新节点生成新 UUID，已有节点保持不变 |
| `moved.resource` | 将动作结果传给调用方 | 不返回计划中的旧物料位置 |

### 方式二：在“实验室操作”页面编排

1. 新建实验操作，填写稳定的名称、用途和说明。
2. 从设备与动作目录选择动作，并绑定当前 启动图 中的设备实例。
3. 为每个动作参数选择固定值、公开输入、上游结果或物料来源。
4. 按需要添加条件、循环和已发布的子操作。
5. 定义公开输入和输出，核对字段名称、类型、单位、是否必填和资源约束。
6. 保存后处理页面给出的节点、连线、绑定或类型错误。

人工确认不是独立的 Python 控制语句。需要人员确认时，在页面中用人工确认包装已经绑定的真实设备动作，并设置 1 到 86400 秒的合理超时。超时不会自动视为批准，拒绝或超时会取消任务。完整约束见[人工确认](workflow-features.md#人工确认)。

## 5. 安全约束

- presence、夹爪负载和目标空位检查默认开启；
- 只有设备能力与现场规程明确允许时才关闭某项检查；
- 超时后不能自动再次搬运，因为原动作可能已经完成；
- 动作失败或结果未知时，不提交计划目标位置；
- 外层工作流不得再次提交同一次位置变化；
- 返回 `result` 应能区分成功、失败和未知，而不是只返回模糊文本。

## 6. 登记、导入和检查

在 `package.yaml` 中登记：

```yaml
- workflow_uuid: 41d51b13-8269-47cc-ad16-a553ed926f11
  source: example_lab/experiment_operations/standard_material_transfer.py
```

然后运行：

```bash
python -m compileall example_lab/experiment_operations
unilab package inspect --path . --out /tmp/device-package-inspect
```

如果使用页面创建或导入，还要核对节点、连线、设备绑定、公开合同和资源来源。导入成功只代表系统已经读取定义，不代表该操作可以直接运行。

## 7. 发布和复用

检查通过后发布当前修订。发布会形成稳定的可引用合同；之后再次编辑会产生新修订，已有任务继续使用创建时冻结的执行图和源码快照。

在完整工作流中引用时，需要为本次调用分别配置：

- 公开输入参数；
- 设备实例绑定；
- 输入输出连接；
- 物料、试剂或站点来源。

发布只固定合同，不代表真机安全审批已经完成。设备、动作参数或工艺发生变化后，仍需重新测试和验收。

删除实验操作前，必须确认没有已发布工作流仍在引用它。历史任务会保留冻结快照，但上层工作流之后可能因合同缺失而无法检查或发布。

## 8. 单独验收

1. 使用模拟 启动图 启动 Uni-Lab OS。
2. 确认实验操作显示为“标准物料转运”。
3. 选择一件测试物料、有效来源位和空目标位。
4. 运行预检，确认错误来源、占位或设备离线会被阻止。
5. 创建 dry-run Task，检查动作参数和输出字段。
6. 模拟失败和结果未知，确认不会错误更新物料位置。

## 9. 常见错误

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 找不到机器人 | `device()` 使用了类型 ID | 改为 启动图 实例 ID |
| 调用方拿不到物料 | 返回值未声明 `ResourceSlot` | 在结果合同和 `return` 中保留物料字段 |
| 位置更新两次 | 设备原子动作和外层流程都提交 | 只保留一个位置提交方 |
| 失败后显示已到目标位 | 使用了计划值而非动作结果 | 只返回并提交设备确认后的结果 |
| 子工作流无法引用 | 未发布或类型不是 `experiment_operation` | 修正类型，检查后发布 |

## 10. 完成清单

- [ ] 只承担一个可复用业务目的；
- [ ] 输入、输出和副作用写清楚；
- [ ] 使用 启动图 实例 ID 和真实动作合同；
- [ ] 物料使用 `ResourceSlot` 连续传递；
- [ ] 位置只在物理成功后提交一次；
- [ ] 成功、失败、超时和未知结果均已验证；
- [ ] 已登记、检查、发布并能被完整工作流调用。
