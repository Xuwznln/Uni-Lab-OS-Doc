# 弹夹

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本页说明怎样登记弹夹、料匣或按顺序取用的耗材盒。业务上要说清楚四件事：弹夹有多少位置、按什么顺序取用、每个位置允许放什么，以及当前已经取到哪个位置。当前代码同时支持“每个槽位放一件物料”的普通弹夹，以及“每个洞位堆叠多张片材”的片材弹夹。

:::{admonition} 本页完成条件
根据实物选择普通多槽载架或 `MagazineHolder`；填写固定取用顺序、方向、容量和允许物料；在现场实例中记录当前槽位、锁定状态和批次，并检查换夹、空槽、粘片和卡料后的恢复流程。
:::

## 1. 先确认弹夹的使用规则

弹夹与普通载架的区别通常不在几何，而在运行规则：

- 有明确取用顺序（FIFO、LIFO 或指定槽位）；
- 有正反方向、锁定/解锁或换夹动作；
- 整夹有条码，槽内物料也可能有独立条码；
- 设备维护当前槽位，但库存仍要保存每个放置位（Site）的物料归属。

如果没有这些顺序/换夹语义，按[小瓶载架](vial-rack.md)或[工作站台面与仓库](deck-warehouse.md)建模即可。带光电或旋转驱动的对象见[光电堆栈与旋转堆栈：物料 + 设备](stack.md)。

## 2. 先选择弹夹结构

| 实物结构 | 使用方式 | 典型例子 |
| --- | --- | --- |
| 每个槽位最多放一瓶或一盒 | `BottleCarrier` 加多个固定放置位 | 试剂瓶弹夹、吸头盒料匣 |
| 每个洞位上下堆叠多张片材 | `MagazineHolder`，每个洞位是一个 `Magazine` 堆栈 | 极片、隔膜、垫片弹夹 |
| 只有一个连续堆栈 | 单个 `Magazine` 或普通资源堆栈 | 单列片材供料 |

不要只因为业务名称都叫“弹夹”就使用同一种结构。能否在一个位置内堆叠多件物料，是选择类型的关键。

## 3. 普通多槽弹夹模板

```python
from pylabrobot.resources import Coordinate, ResourceHolder
from unilabos.registry.decorators import resource
from unilabos.resources.itemized_carrier import BottleCarrier


SLOT_ORDER = [f"slot_{index:02d}" for index in range(1, 7)]


@resource(
    id="my_lab_reagent_magazine_6",
    displayname="My Lab 六位试剂弹夹",
    category=["my_lab", "carrier", "magazine"],
    description="从 slot_01 到 slot_06 顺序取用的可更换试剂弹夹。",
    metadata={
        "slot_count": 6,
        "slot_order": SLOT_ORDER,
        "orientation": "front_to_back",
    },
)
def make_reagent_magazine(name: str = "my-lab-reagent-magazine") -> BottleCarrier:
    sites = {}
    for index, label in enumerate(SLOT_ORDER):
        holder = ResourceHolder(
            name=f"{name}_{label}",
            size_x=40.0,
            size_y=40.0,
            size_z=80.0,
        )
        holder.location = Coordinate(x=10.0 + index * 50.0, y=10.0, z=0.0)
        holder.unilabos_extra = {
            "content_type": ["my_lab_sample_vial_50ml"],
        }
        sites[label] = holder

    return BottleCarrier(
        name=name,
        size_x=310.0,
        size_y=60.0,
        size_z=90.0,
        sites=sites,
        category="magazine",
    )
```

`slot_order` 是模板规则；`current_slot`、剩余数量、锁定状态、弹夹条码和批次属于实例/设备运行状态。

## 4. 片材弹夹模板

当前代码提供 `MagazineHolder`、`Magazine` 和 `magazine_factory`。`MagazineHolder` 表示整个弹夹，`Magazine` 表示其中一个可以上下堆叠片材的洞位：

```python
from pylabrobot.resources import Coordinate
from unilabos.registry.decorators import resource
from unilabos.resources.battery.magazine import magazine_factory


@resource(
    id="my_lab_sheet_magazine_6",
    displayname="六洞片材弹夹",
    category=["battery_material", "magazine_holder"],
    description="六个洞位，每个洞位最多堆叠 100 张片材。",
    metadata={"hole_count": 6, "max_sheets_per_hole": 100},
)
def make_sheet_magazine(name: str):
    return magazine_factory(
        name=name,
        size_x=80.0,
        size_y=80.0,
        size_z=40.0,
        locations=[
            Coordinate(20.0, 20.0, 30.0),
            Coordinate(40.0, 20.0, 30.0),
            Coordinate(60.0, 20.0, 30.0),
            Coordinate(20.0, 50.0, 30.0),
            Coordinate(40.0, 50.0, 30.0),
            Coordinate(60.0, 50.0, 30.0),
        ],
        hole_diameter=14.0,
        hole_depth=10.0,
        max_sheets_per_hole=100,
        category="magazine_holder",
        model="my_lab_sheet_magazine_6",
    )
```

| 参数 | 单位 | 约束 |
| --- | --- | --- |
| `locations` | mm | 每个洞位中心坐标，顺序决定 A1、A2 等编号 |
| `hole_diameter` | mm | 必须大于片材直径并保留经验证的间隙 |
| `hole_depth` | mm | 与最大堆叠高度和取料安全距离一致 |
| `max_sheets_per_hole` | 张 | 不能只按几何理论值计算，要考虑公差、粘片和安全余量 |
| `direction` | `x/y/z` | 片材堆叠方向；通常为 `z` |
| `klasses` | 物料工厂列表 | 仅用于明确的模拟初始化；生产库存应由真实盘点建立 |

## 5. 设备需要提供哪些业务动作

弹夹设备通常应提供：装载、卸载、锁定、解锁、读取状态和按 放置位 取料等动作。动作参数使用稳定 放置位 名称或经确认的槽位编号，由驱动转换为协议地址。

一次“取下一件物料”应：

1. 根据实例状态和 `slot_order` 解析目标放置位；
2. 校验 放置位 有兼容物料、弹夹已锁定且设备就绪；
3. 执行取料动作；
4. 设备确认成功后提交一次库存转移并推进 `current_slot`；
5. 卡料/超时后保持原库存事实，现场核对后再恢复。

## 6. 交付前必须检查

- 槽位数量、顺序和 0/1 起始约定与设备协议一致；
- 正反方向和锁定状态能阻止错误运行；
- 空槽、跳槽、错误类型、满夹、粘片和重复取料有失败测试；
- 换夹先卸载并盘点旧夹，再登记新夹，不覆盖历史实例；
- 整夹条码、槽内条码和批次均可追溯；
- 动作失败时不推进当前槽位、不改变物料归属。

普通瓶盒弹夹使用多槽载架即可；片材弹夹应优先使用当前代码已经提供的 `MagazineHolder` 和 `Magazine`，不要再开发一套含义重复的基础类型。
