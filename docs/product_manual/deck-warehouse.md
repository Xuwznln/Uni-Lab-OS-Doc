# 工作站台面与仓库

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本页说明怎样把一台工作站的台面、仓库和固定放置位登记到 Uni-Lab OS。可以把它们理解成一张分层地图：Deck 是整台工作站的台面，WareHouse 是台面上的一个仓库，放置位（Site）是仓库中的一个具体位置。

现场常说的“堆栈”，如果只是多层料架、板库或缓存架，**没有电机、也没有光电**，就按本页的仓库登记，不要再单独做一页物料模板。带光电传感器或可旋转驱动的堆栈必须同时定义物料面和设备面，见[光电堆栈与旋转堆栈：物料 + 设备](stack.md)。

:::{admonition} 本页完成条件
业务人员能用现场标签找到每个位置；页面显示的排列方向与实物一致；每个位置只接受经过验证的物料；系统重启后仍能恢复相同的位置名称和层级关系。
:::

## 1. 先画出现场地图

开始写代码前先形成一张经现场确认的表：

| 要确认的内容 | 示例 | 规则 |
| --- | --- | --- |
| 台面名称 | `assembly_deck` | 一台工作站只使用一个稳定名称 |
| 仓库名称 | `left_store` | 与现场标签一致，不使用临时昵称 |
| 行、列、层数 | 4 列 × 4 行 × 1 层 | 停用位置也要记录 |
| 第一个位置坐标 | X=10, Y=10, Z=10 mm | 相对仓库原点，来自图纸或实测 |
| 相邻位置间距 | X=147, Y=106, Z=130 mm | 是中心间距，不是物料尺寸 |
| 位置可用空间 | 127 × 86 × 100 mm | 用于判断物料能否放入 |
| 编号顺序 | 行优先或列优先 | 与操作员、设备协议和页面保持一致 |

## 2. 使用仓库工厂生成固定位置

当前代码提供 `warehouse_factory`，适合规则排列的仓库：

```python
from unilabos.registry.decorators import resource
from unilabos.resources.warehouse import warehouse_factory


@resource(
    id="my_lab_warehouse_4x4",
    category=["warehouse"],
    displayname="四行四列板库",
    description="用于存放标准微孔板载架。",
)
def my_lab_warehouse_4x4(name: str):
    return warehouse_factory(
        name=name,
        num_items_x=4,
        num_items_y=4,
        num_items_z=1,
        dx=10.0,
        dy=10.0,
        dz=10.0,
        item_dx=147.0,
        item_dy=106.0,
        item_dz=130.0,
        resource_size_x=127.0,
        resource_size_y=86.0,
        resource_size_z=100.0,
        layout="row-major",
    )
```

| 参数 | 业务含义 | 约束 |
| --- | --- | --- |
| `num_items_x/y/z` | X、Y、Z 三个方向的位置数量 | 必须与启用位置数量一致 |
| `dx/dy/dz` | 第一个位置相对仓库原点的偏移 | 单位 mm，来自实测或正式图纸 |
| `item_dx/dy/dz` | 相邻位置之间的距离 | 单位 mm，不能与物料长宽高混淆 |
| `resource_size_x/y/z` | 每个位置允许占用的空间 | 单位 mm，不得小于目标物料外形 |
| `removed_positions` | 不可用或被机械结构占用的位置 | 按生成顺序填写；上线前逐项核对 |
| `col_offset/row_offset` | 编号从哪一列或哪一行开始 | 只影响显示编号，不改变实际坐标 |
| `layout` | 位置编号的排列方式 | 发布后不要随意修改，否则库存会对应到错误位置 |

`row-major` 表示先按行编号，`col-major` 表示先按列编号，`vertical-col-major` 用于竖向仓库并反转 Y 方向。必须选用与现场标签和设备协议一致的一种。

## 3. 不规则层列用载架登记，仍算仓库

行列工厂适合规整网格。层数、列距不规则时，用 `BottleCarrier + ResourceHolder`（容器/小瓶）或 `PlateCarrier + PlateHolder`（微孔板）列出每个放置位。它们仍然是仓库/载架，不是设备：

```python
from pylabrobot.resources import Coordinate, ResourceHolder
from unilabos.registry.decorators import resource
from unilabos.resources.itemized_carrier import BottleCarrier


@resource(
    id="my_lab_vial_stack",
    displayname="My Lab 样品瓶堆栈",
    category=["my_lab", "warehouse", "stack"],
    description="2 层 × 3 列；每个放置位最多放一只 50 mL 样品瓶。",
    metadata={"site_count": 6},
)
def make_vial_stack(name: str = "my-lab-vial-stack") -> BottleCarrier:
    sites = {}
    for layer in range(2):
        for column in range(3):
            label = f"L{layer + 1}C{column + 1}"
            holder = ResourceHolder(
                name=f"{name}_{label}",
                size_x=40.0,
                size_y=40.0,
                size_z=80.0,
            )
            holder.location = Coordinate(
                x=20.0 + column * 60.0,
                y=20.0,
                z=20.0 + layer * 100.0,
            )
            holder.unilabos_extra = {
                "content_type": ["my_lab_sample_vial_50ml"],
            }
            sites[label] = holder

    return BottleCarrier(
        name=name,
        size_x=200.0,
        size_y=80.0,
        size_z=220.0,
        sites=sites,
        category="vial_stack",
    )
```

示例坐标必须换成实测或 CAD 数据。放置位名称一旦进入启动图、库存和工作流，不要随意变更。所有启用位置都要有长期稳定的编号，并写明允许的物料类型。

若这排槽位还要读光电或由电机转到交互位，必须在同一复合实例中保留物料树，并增加设备动作和状态，见[光电堆栈与旋转堆栈：物料 + 设备](stack.md)。

## 4. 在 Deck 中组装多个仓库

```python
from pylabrobot.resources import Coordinate, Deck
from unilabos.registry.decorators import resource


@resource(
    id="my_lab_station_deck",
    category=["deck"],
    displayname="装配工作站台面",
    description="包含左右两个板库。",
)
class MyLabStationDeck(Deck):
    def __init__(
        self,
        name: str = "my_lab_station_deck",
        size_x: float = 2700.0,
        size_y: float = 1080.0,
        size_z: float = 1500.0,
        setup: bool = False,
        **kwargs,
    ):
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z)
        if setup:
            self.setup()

    def setup(self) -> None:
        left = my_lab_warehouse_4x4("left_store")
        right = my_lab_warehouse_4x4("right_store")
        self.assign_child_resource(left, location=Coordinate(-200.0, 400.0, 0.0))
        self.assign_child_resource(right, location=Coordinate(2350.0, 400.0, 0.0))
```

`setup=False` 必须保留。启动图（Graph JSON）中填写 `setup: true` 时才组装默认台面，避免系统只是读取模板时就重复创建子物料。构造函数还应接受反序列化时可能传回的字段，通常通过明确参数和 `**kwargs` 兼容。

## 5. 不规则位置使用 `available_sites`

设备上的固定位置不规则时，不要强行套用行列工厂。可以直接登记位置清单：

```python
AVAILABLE_SITES = [{
    "index": "T1",
    "label": "T1",
    "visible": True,
    "position_x": 0.0,
    "position_y": 0.0,
    "position_z": 0.0,
    "width": 128.0,
    "length": 86.0,
    "depth": 20.0,
    "content_type": ["my_lab_96_well_plate"],
    "description": "台面左前方板位",
}]
```

`index` 是系统内部稳定索引，`label` 是页面和现场共同使用的位置名称。两者在同一模板中都不能重复。`visible=false` 只表示页面不显示，不能用来绕过库存和安全检查。

## 6. 交付前必须检查

- 用一件标准物料逐个核对所有启用位置；
- 页面上的上下、左右和层级方向与实物一致；
- 行列顺序、偏移量和停用位置有正式记录；
- 错误类型、超尺寸和已占用位置会被拒绝；
- 台面重新加载后，位置名称和物料归属没有变化；
- 页面布局坐标与机器人运动坐标分开管理，机器人仍需单独标定；
- 带光电或旋转驱动的对象已改走设备页，没有把电机和传感器写进仓库模板。
