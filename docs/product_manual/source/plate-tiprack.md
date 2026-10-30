# 孔板与吸头盒（Plate／Tiprack）

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本页说明怎样登记孔板和吸头盒。业务人员需要提供外形、孔位或吸头位数量、编号方向、容量及适用设备；开发人员把这些信息写成可检查的模板。完成后，系统应能准确选择 A1、B2 等位置，并拒绝不兼容或已经使用的物料。

本页说明如何在自己的设备包中接入孔板和吸头盒。两者都是“外层资源 + 有序子资源”：Plate 包含 Well，TipRack 包含 TipSpot/Tip。

:::{admonition} 本页完成条件
在 `resources/materials.py` 中登记具体孔板或吸头盒；为每个孔或吸头位置建立固定编号；在载架放置位的 `content_type` 中写入允许的物料类型，并完成逐孔或逐吸头测试。
:::

实际接入时可以建立公共 Plate/TipRack 基类来复用尺寸和元数据，再通过 `@resource` 为每种规格注册独立模板。需要特别注意：完整生成 96/384 个子节点会增大资源树；如果省略子节点，页面可以展示外壳，但工作流也不能再逐孔/逐吸头位寻址。

## 1. 什么情况下需要新增一种规格

以下任一项不同，都应评估新建模板，而不是只改显示名称：

- 外形尺寸、行列数或孔距；
- Well 的截面、底形、深度或最大体积；
- Plate 的 skirt/trough 类型或设备兼容性；
- Tip 的最大体积、长度、装配深度、滤芯或复用策略；
- 模型、方向或条码规则发生不兼容变化。

## 2. 按模板登记可逐孔操作的孔板

```python
from pylabrobot.resources import Plate, Well, create_ordered_items_2d
from pylabrobot.resources.well import CrossSectionType, WellBottomType
from unilabos.registry.decorators import resource


def make_96_wells():
    return create_ordered_items_2d(
        Well,
        num_items_x=12,
        num_items_y=8,
        dx=10.98,
        dy=7.84,
        dz=1.7,
        item_dx=9.0,
        item_dy=9.0,
        size_x=6.8,
        size_y=6.8,
        size_z=10.5,
        max_volume=350.0,
        cross_section_type=CrossSectionType.CIRCLE,
        bottom_type=WellBottomType.FLAT,
    )


@resource(
    id="my_lab_96_well_assay_plate",
    displayname="My Lab 96 孔检测板",
    category=["my_lab", "labware", "plate"],
    description="按 A1-H12 寻址的 96 孔检测板。",
    metadata={"well_count": 96, "volume_unit": "uL"},
)
class MyLab96WellPlate(Plate):
    def __init__(self, name: str = "my-lab-96-well-plate", **kwargs):
        kwargs.setdefault("ordered_items", make_96_wells())
        super().__init__(
            name=name,
            size_x=127.76,
            size_y=85.48,
            size_z=14.35,
            plate_type="skirted",
            **kwargs,
        )
```

示例数值必须按供应商图纸或测量结果替换。创建后检查 `A1`、`H12` 的名称、顺序、中心距和坐标方向是否与设备协议一致。

## 3. 按模板登记吸头盒

```python
from pylabrobot.resources import Tip, TipRack, TipSpot, create_ordered_items_2d
from unilabos.registry.decorators import resource


def make_tip_spots():
    def make_tip():
        return Tip(
            has_filter=True,
            maximal_volume=200.0,
            total_tip_length=52.0,
            fitting_depth=5.0,
        )

    return create_ordered_items_2d(
        TipSpot,
        num_items_x=12,
        num_items_y=8,
        dx=10.0,
        dy=7.0,
        dz=2.0,
        item_dx=9.0,
        item_dy=9.0,
        size_x=8.0,
        size_y=8.0,
        make_tip=make_tip,
    )


@resource(
    id="my_lab_200ul_filter_tip_rack",
    displayname="My Lab 200 µL 滤芯吸头盒",
    category=["my_lab", "labware", "tip_rack"],
    metadata={"tip_count": 96, "max_volume_ul": 200, "has_filter": True},
)
class MyLab200uLTipRack(TipRack):
    def __init__(self, name: str = "my-lab-200ul-tip-rack", **kwargs):
        kwargs.setdefault("ordered_items", make_tip_spots())
        super().__init__(
            name=name,
            size_x=127.76,
            size_y=85.48,
            size_z=60.0,
            category="tip_rack",
            with_tips=True,
            **kwargs,
        )
```

实际 PyLabRobot 版本的 `TipSpot` 工厂参数以设备包锁定依赖为准。重点是把 Tip 的容量、长度、装配深度、滤芯属性和放置位（Site）顺序变成可检查的定义。

## 4. 决定是否需要逐孔或逐吸头追踪

| 需求 | 建议 |
| --- | --- |
| 工作流按 `A1`/`B2` 操作 | 必须生成稳定 Well 子资源 |
| 系统追踪每支吸头是否用过 | 必须生成 TipSpot，并维护使用状态 |
| 只整板搬运、不逐孔操作 | 可只保留板外壳，但文档中明确能力限制 |
| 页面因 96/384 个位置数据过多而变慢 | 先测量数据量，再考虑分批加载或简化画面；不能在没有说明的情况下删除逐孔操作能力 |

某些设备包为了控制前后端通信体积，会对部分 96/384 孔板和 TipRack 使用空 `ordered_items/ordering`。这是特定场景的性能折中，不应直接作为所有设备包的默认做法。

## 5. 说明哪些位置和设备允许使用它

孔板或吸头盒本身不记录它能放到哪个工位。应在载架或设备的放置位上用 `content_type` 明确允许的物料类型，例如：

```python
plate_site.unilabos_extra = {
    "content_type": ["my_lab_96_well_assay_plate"],
}
tip_site.unilabos_extra = {
    "content_type": ["my_lab_200ul_filter_tip_rack"],
}
```

## 6. 交付前必须检查

- Catalog 能分别发现每种 Plate/TipRack 模板；
- 外形、孔距、容量、方向和单位有来源记录；
- `A1`、末孔/末 TipSpot 和排序方向与工艺一致；
- 超容量、错误板型、错误吸头规格和已用吸头会被拒绝；
- 整板搬运与逐孔/逐 Tip 操作的能力边界写清楚；
- 模型是精确、代理还是仅布局用途已标注；
- 离线试运行能够完成装载、逐项选择、状态更新和卸载。
