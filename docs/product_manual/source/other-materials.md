# 其他可追踪物料

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本页用于登记盖板、封膜底座、转接夹具、配平块、保护罩等其他物料。只要系统需要识别它、追踪它的位置，或在工作流中使用它，就应该按照本页规则登记。

:::{admonition} 本页完成条件
选择最接近的普通物料、容器或载架类型，登记长期稳定的类型编号；如果它能承载其他物料，必须建立固定放置位，并在启动图（Graph JSON）中检查现场实例、允许物料和状态追踪。
:::

盖板、封膜底座等整体搬运的物料可以使用 `Container`；自身还能放置另一件物料的底座，则使用带一个放置位的 `ItemizedCarrier`。这样系统可以自动判断物料能否放入，而不是只在备注里写“其他物料”。

## 1. 根据实际用途选择类型

| 对象特征 | 建议实现 |
| --- | --- |
| 只是整体可搬运对象 | `Resource` |
| 有整体包络/容量语义 | `Container` |
| 承载一个子物料 | 单个放置位（Site），使用 `ItemizedCarrier` / `PlateCarrier` |
| 承载多个独立物料 | 多 放置位 Carrier，转到对应载架章节 |
| 只是一种运行状态 | 不建物料；放在实例或设备状态中 |

## 2. 登记不能承载其他物料的对象

```python
from pylabrobot.resources import Container
from unilabos.registry.decorators import resource


@resource(
    id="my_lab_plate_cover",
    displayname="My Lab 孔板盖",
    category=["my_lab", "labware", "plate_cover"],
    description="可独立取放和追踪的孔板盖；不承载液体。",
    metadata={"reusable": True, "model_accuracy": "layout_only"},
)
class MyLabPlateCover(Container):
    def __init__(self, name: str = "my-lab-plate-cover", **kwargs):
        super().__init__(
            name=name,
            size_x=127.76,
            size_y=85.48,
            size_z=10.0,
            category="plate_cover",
            **kwargs,
        )
```

如果对象没有容量，可以选择普通 `Resource` 类型；应优先使用当前系统版本能够稳定保存和读取的最接近类型。

## 3. 登记带一个放置位的转接底座

```python
from collections import OrderedDict
from pylabrobot.resources import Coordinate, ResourceHolder
from unilabos.resources.itemized_carrier import ItemizedCarrier


def make_adapter_base(name: str) -> ItemizedCarrier:
    holder = ResourceHolder(
        name="slot",
        size_x=127.76,
        size_y=85.48,
        size_z=5.0,
        child_location=Coordinate(0.0, 0.0, 5.0),
    )
    holder.unilabos_extra = {
        "content_type": ["my_lab_96_well_assay_plate"],
    }
    return ItemizedCarrier(
        name=name,
        size_x=127.76,
        size_y=85.48,
        size_z=20.0,
        num_items_x=1,
        num_items_y=1,
        num_items_z=1,
        sites=OrderedDict({"A1": holder}),
        category="plate_adapter",
    )
```

用 `@resource` 为该工厂补充稳定 ID、类别、显示名称和描述。底座与板的关系由 放置位 表达，不要只写在 `metadata` 备注中。

## 4. 补充信息中可以写什么

可以在 `metadata` / `unilabos_extra` 中记录模型精度、供应商代码、外部系统物料类型码、视觉颜色和 放置位 角色，但这些数据应：

- 能保存为标准 JSON 数据；
- 不覆盖 Uni-Lab OS 已有的标准字段；
- 不包含某个实物的条码、当前位置或当前批次；
- 不把视觉近似值描述成真机标定值；
- 有明确消费者，避免堆积无人读取的自由文本。

外部物料类型码/ID可以合并到 `unilabos_extra`，并为代理模型记录来源和精度。这类映射适合放在设备包边界，便于与外部系统互操作。

## 5. 交付前必须检查

- 已确认标准物料类确实不能表达该对象；
- 资源 ID、类别、包络和用途清楚且稳定；
- 有子物料时使用 放置位/父子关系，而不是备注；
- 清洁、校准、寿命、条码等实例状态有明确归属；
- 代理模型和布局坐标标注精度，不参与真机授权；
- Catalog、启动图、兼容 放置位、转运和历史实例均能正确读取。
