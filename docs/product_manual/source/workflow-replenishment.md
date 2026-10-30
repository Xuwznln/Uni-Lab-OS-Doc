# 工作流补料与物料来源

:::{admonition} 阅读角色
- **业务负责人**：确认什么时候该补、补多少、补到哪个位置、谁来确认。
- **开发人员**：在工作流里声明物料来源、数量、放置位和补料步骤。
- **验收人员**：分别走“够用”和“不够用”两条路径，核对补完后的数量和位置。
:::

本页说明任务执行中物料不够时怎么写。示例里的模板、仓库、放置位和动作都要换成你设备包里的真实名称。这些函数只给编译器识别，不要用 `python` 直接运行工作流文件。

## 1. 什么情况下需要补料

执行到物料来源时，试剂余量或耗材件数可能不够完成本次任务，就需要补料。例如溶剂瓶里的体积不够本次加液，或吸头盒里的吸头不够下一段使用。

任务开始前库存已经够，并且运行中不会再缺，只声明物料来源即可，不必写补料。

## 2. 用 material_source 判断是否支持补料

`material_source(...)` 声明这次任务从哪里取物料。默认不支持补料。只有可能中途不够的来源，才写 `supports_replenishment=True`。

写上之后，来源结果带有 `sufficient`。工作流要有一个 `if 来源.sufficient` 和对应的 `else`：

- `sufficient` 为真：库存够，在这条分支里使用该来源；
- `sufficient` 为假：库存不够。此时来源不返回物料，要在这条分支里按同一放置位重新选择，再补料。

两条分支都要有步骤，而且步骤不能相同。原来源选中的物料只能交给“够用”分支。缺料分支里先写一个新的 `material_source`，不要再写 `supports_replenishment=True`，然后对这个新来源做 `refill`。后面的设备动作使用补料返回的 `material`。

这个判断发生在该来源步骤执行时。瓶子只是被其他任务占用时，这一步等待释放，不进入缺料分支。

```python
from my_lab.resources.materials import reagent_bottle_100ml
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy,
    MaterialFlowRole,
    material_source,
    resource_ref,
)

# unilab:node_uuid=<溶剂来源 UUID>
source_solvent = material_source(
    resource_template=reagent_bottle_100ml,
    mode="existing",
    mount=resource_ref("reagent_rack"),
    material_uuid=None,
    site="A1",
    slot_range=None,
    flow_role=MaterialFlowRole.REAGENT,
    custody_policy=MaterialCustodyPolicy.SHARED_SOURCE,
    supports_replenishment=True,
)
```

| 参数 | 写法 |
| --- | --- |
| `resource_template` | 导入的物料模板，例如试剂瓶、吸头盒 |
| `mode` | 使用现场已有物料时写 `"existing"` |
| `mount` | `resource_ref("仓库业务 ID")`，对应启动图里的仓库或台面 |
| `site` | 放置位名称，例如 `"A1"`。后面的 `refill` 使用同一个名称 |
| `flow_role` | 试剂用 `REAGENT`，吸头等耗材用 `CONSUMABLE` |
| `custody_policy` | 多任务共用的试剂瓶用 `SHARED_SOURCE`；本次任务独占的盒子用 `TASK_EXCLUSIVE` |
| `supports_replenishment` | 可能不够时写 `True` |

`material_uuid` 留空，由该来源步骤按模板、仓库和放置位选择。不要把某一次运行生成的物料 UUID 写进工作流。

## 3. 用 available_sites 提供可用放置位

`available_sites` 写在物料模板上，列出这个模板固定拥有的放置位。每一项至少有不重复的 `index` 和 `label`。`label` 是页面和现场使用的位置名，工作流里的 `site` 填这个名字，例如 `"A1"`。

这份清单只定义有哪些位置，不记录当前放了什么。装入该模板的物料时，系统按清单建立放置位。坐标和尺寸的完整写法见[工作站台面与仓库](deck-warehouse.md)。

```python
AVAILABLE_SITES = [
    {"index": "A1", "label": "A1", "description": "试剂架第 1 位"},
    {"index": "A2", "label": "A2", "description": "试剂架第 2 位"},
]
```

补料前先确认目标 `label` 在模板清单里，并且该位置当前能放下要装入的物料。

## 4. 用 quantity_requirement 表达数量

`quantity_requirement(...)` 声明这次任务要从某个来源容器里预留多少试剂或内容物。来源够用时，在该来源选中容器后预留；来源不够时，等缺料分支留下唯一容器后，再为条件后面的消耗动作预留。动作完成后记账。它负责账，不代替泵、秤或传感器的测量值。

```python
# 写在 if / else 之后。added 是条件后面的消耗动作，不要把 source_solvent 再传给它。
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

| 参数 | 含义 |
| --- | --- |
| `requirement_key` | 本工作流内唯一的需求名称 |
| `source` | 直接写一个 `material_source` 的结果 |
| `consume` | `if` 后面的消耗动作。分支里面的结果名在条件外面不可用 |
| `quantity` | 有限正数，或工作流输入；输入为 `0` 时本次不预留 |
| `quantity_unit` | 与库存一致的单位，如 `µL`、`mL`、`L`、`mg`、`g`、`kg` |
| `scale` | 正数倍率，编译时先乘到 `quantity` 上 |

数量要求可以和支持补料写在一起，但必须放在 `if` 后面。`source` 写支持补料的那个来源，`consume` 写条件后面的消耗动作。原来源的物料只能出现在够用分支里，所以这个消耗动作不要再接收 `source_solvent`。

够用时，按来源选中的容器预留。缺料时，按缺料分支留下的那一个容器预留，因此缺料分支结束时只能留下补量得到的瓶子。分支内部的加液结果不能当作 `consume`。补入原瓶的数量仍写在 `reagent.quantity` 里。

## 5. 用 material_io 装入、取走或补量

`material_io(...)` 是人工确认后的物料边界，不派发设备动作。人员确认后库存才更新。`timeout_seconds` 取 1 到 86400 秒。每次调用都要写齐 `mode`、`resource_template`、`mount`、`site`、`material`、`disposition`、`scope`、`timeout_seconds`。用不到的字段写 `None`。

| 操作 | 何时使用 | 结果 |
| --- | --- | --- |
| `load` | 放入一件新物料，例如换上新吸头盒 | 按模板建立新物料，输出 `material` |
| `unload` | 取走已有物料 | 按 `disposition` 和 `scope` 解除占用 |
| `refill` | 原容器还在原位，只补充剂余量 | 增加数量；不换位置，不新建物料 |

`load` 必须给出模板和放置位名称。`site` 填 `available_sites` 的 `label`。可选 `name`、`barcode`。若装入时就要带试剂，`reagent` 里写数量、单位，以及 `name`、`cas` 或 `reagent_info_uuid` 其中一种身份。

`unload` 的 `material` 引用已有物料。`disposition` 用 `"detach"` 或 `"soft_delete"`。`scope` 用 `"material_only"` 或 `"material_with_contents"`。

`refill` 的 `material` 引用已经占位的容器。缺料分支里引用的是重新选出的来源，不是上面那个 `sufficient` 为假、没有物料的来源。`site` 与该来源的放置位名称相同。`reagent` 只能有 `quantity` 和 `quantity_unit`。现场如果换成了另一瓶，使用 `unload` 再 `load`。

```python
# checked_solvent 是缺料分支里重新声明的 material_source
# unilab:node_uuid=<补量 UUID>
refilled = material_io(
    mode="refill",
    resource_template=None,
    mount=resource_ref("reagent_rack"),
    site="A1",
    material=checked_solvent,
    disposition=None,
    scope=None,
    timeout_seconds=600,
    reagent={"quantity": 20, "quantity_unit": "mL"},
)
```

`reagent` 必须写成对象字面量，`quantity` 也必须是正数字面量。本次加液量可以继续用工作流输入，例如后面的 `volume_ml`。

## 6. 补料前、补料中和补料后

执行到支持补料的来源时，系统检查当前余量或件数，并写入 `sufficient`。目标放置位要已经在模板的 `available_sites` 里，而且来源和 `refill` 使用同一个放置位名称。

够用时进入 `if` 分支，设备动作直接使用这个来源。写了 `quantity_requirement` 时，按这次选中的容器预留。

不够时来源步骤完成，但物料为空，任务进入 `else`。这一支先按同一放置位重新选择瓶子，再执行 `material_io`。`material_io` 停下等待人工确认。确认前不改库存，也不继续后面的设备动作。瓶子被其他任务占用时不进入这一支，来源步骤继续等待。

确认之后：

- `refill` 完成后，同一件容器的余量增加，放置位不变。后面的设备动作使用 `refilled.material`。
- `load` 完成后，新物料出现在指定放置位。后面的步骤使用 `loaded.material`。
- 写了 `quantity_requirement` 时，用缺料分支留下的那一个容器，为 `if` 后面的消耗动作预留。

## 7. 失败时怎么处理

| 情况 | 结果 |
| --- | --- |
| 声明了支持补料，但没有“够用”和“不够用”两条非空分支 | 工作流不能进入运行 |
| 缺料分支仍使用原来源 | 原来源此时没有物料。应先重新选择，再补量 |
| 来源的 `site` 和 `refill` 的 `site` 不是同一个放置位 | 补量失败，库存不变 |
| 瓶子被其他任务占用 | 来源步骤等待，不进入缺料分支 |
| 缺料分支留下的容器不是唯一一个 | 后面的数量预留失败，需要人工核对瓶子 |
| 数量要求大于当前可预留量，且该来源没有补料分支 | 任务停在等料 |
| `load` 的目标放置位已有物料 | 停在装入步骤，不覆盖原物料 |
| 人员拒绝，或等待超过 `timeout_seconds` | 停在该步骤，库存保持确认前的状态 |
| 现场换了一瓶，却使用 `refill` | 身份和位置都不会更新。应改为 `unload` 后再 `load` |

失败后先核对现场瓶子、放置位和余量，再处理这一步。系统不会自动换仓库或换设备继续跑。

## 8. 完整示例

下面是一次加液：`A1` 上的溶剂够就直接加；不够就在缺料分支里重新选择 `A1` 上的瓶子，人工补量后再加。`liquid_station.add_reagent` 要换成你设备包里的真实动作。两条分支里的加液都要保留，因为够用和不够用拿到的物料不同。

```python
from my_lab.devices.liquid import LiquidStation
from my_lab.resources.materials import reagent_bottle_100ml
from unilabos.workflow.authoring import (
    MaterialCustodyPolicy,
    MaterialFlowRole,
    device,
    material_io,
    material_source,
    resource_ref,
    workflow,
    workflow_output,
)

liquid_station: LiquidStation = device("liquid_station_01")


@workflow(
    workflow_uuid="<工作流 UUID>",
    displayname="溶剂不足时补量后再加液",
    description="溶剂够则直接加液；不够则人工补入原瓶后再加液。",
)
def add_solvent_with_refill(*, volume_ml: float):
    # unilab:node_uuid=<溶剂来源 UUID>
    source_solvent = material_source(
        resource_template=reagent_bottle_100ml,
        mode="existing",
        mount=resource_ref("reagent_rack"),
        material_uuid=None,
        site="A1",
        slot_range=None,
        flow_role=MaterialFlowRole.REAGENT,
        custody_policy=MaterialCustodyPolicy.SHARED_SOURCE,
        supports_replenishment=True,
    )
    # unilab:node_uuid=<是否够用 UUID>
    if source_solvent.sufficient:
        # unilab:node_uuid=<直接加液 UUID>
        added = liquid_station.add_reagent(
            solvent=source_solvent,
            volume_ml=volume_ml,
        )
    else:
        # unilab:node_uuid=<重新选择 UUID>
        checked_solvent = material_source(
            resource_template=reagent_bottle_100ml,
            mode="existing",
            mount=resource_ref("reagent_rack"),
            material_uuid=None,
            site="A1",
            slot_range=None,
            flow_role=MaterialFlowRole.REAGENT,
            custody_policy=MaterialCustodyPolicy.SHARED_SOURCE,
        )
        # unilab:node_uuid=<补量 UUID>
        refilled = material_io(
            mode="refill",
            resource_template=None,
            mount=resource_ref("reagent_rack"),
            site="A1",
            material=checked_solvent,
            disposition=None,
            scope=None,
            timeout_seconds=600,
            reagent={"quantity": 20, "quantity_unit": "mL"},
        )
        # unilab:node_uuid=<补量后加液 UUID>
        added_after_refill = liquid_station.add_reagent(
            solvent=refilled.material,
            volume_ml=volume_ml,
        )
    return workflow_output()
```

示例里的 `20` 是一次补入原瓶的毫升数，发布前改成现场规程要求的数量。`volume_ml` 是本次加液量。分支里的 `added` 和 `added_after_refill` 不能当作 `quantity_requirement` 的 `consume`。需要预留时，按第 4 节把数量要求写在这个 `if` 后面，`source` 仍写 `source_solvent`，`consume` 写条件后面的消耗动作。缺料分支结束时只留下 `refilled.material` 这一个瓶子。
