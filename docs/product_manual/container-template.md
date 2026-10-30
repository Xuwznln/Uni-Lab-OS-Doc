# 容器（Container）

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本页说明怎样登记烧杯、样品瓶、试剂瓶、粉桶和废液桶等整体容器。如果需要对每个孔单独操作，请查看 [孔板与吸头盒（Plate／Tiprack）](plate-tiprack.md)；如果一个架子上能放多件物料，还要为每个位置建立固定的放置位。

:::{admonition} 本页完成条件
在 `resources/materials.py` 中登记容器规格；尺寸统一使用毫米，容量统一使用微升；再把现场实物加入启动图（Graph JSON），并检查容量、条码、允许放置的位置和搬运后的库存位置。
:::

推荐使用内部 `_container(...)` 工厂统一构造 `Container`，再用不同的 `@resource` 注册烧杯、样品瓶、试剂瓶和注粉瓶。

## 1. 收集定义数据

| 数据 | 放在哪里 | 注意事项 |
| --- | --- | --- |
| 长、宽、高 | `size_x/y/z` | 单位统一为 mm，并明确原点/放置方向 |
| 最大容量 | `max_volume` | PyLabRobot 示例使用 µL；不要和 mL 混写 |
| 安全工作容量 | `metadata` 或工艺约束 | 不等同于标称最大容量 |
| 用途分类 | `category` | 如 `sample_vial`、`liquid_reagent`、`powder_reagent` |
| 模型/形状 | `model` | 可选，但来源和精度必须可追溯 |
| 条码、批次、当前体积 | 物料实例 | 不写入模板默认常量 |

### 选择 `Bottle` 还是 `Container`

| 情况 | 建议类型 | 原因 |
| --- | --- | --- |
| 圆形瓶、烧杯、反应器，需要按容量追踪内容物 | `Bottle` | 当前代码将直径、高度和最大容量封装成统一瓶类 |
| 方形、异形或更通用的整体容器 | `Container` | 可以分别填写长、宽、高 |
| 有多个孔或多个独立位置 | `Plate`、`TipRack` 或 Carrier | 需要逐孔或逐位置追踪，不能只当成一个整体瓶子 |

当前 `Bottle` 的构造参数使用 `diameter`、`height` 和 `max_volume`。条码属于现场实例身份，应通过 启动图 或库存设置，不依赖构造函数中的固定默认值。

```python
from unilabos.registry.decorators import resource
from unilabos.resources.itemized_carrier import Bottle


@resource(
    id="my_lab_reagent_bottle_500ml",
    category=["my_lab", "bottle", "liquid_reagent"],
    displayname="500 mL 试剂瓶",
    description="用于保存液体试剂的圆形瓶。",
    metadata={"nominal_volume_ml": 500, "material": "glass"},
)
def reagent_bottle_500ml(name: str) -> Bottle:
    return Bottle(
        name=name,
        diameter=70.0,
        height=120.0,
        max_volume=500_000.0,
        category="liquid_reagent",
        model="my_lab_reagent_bottle_500ml",
    )
```

## 2. 按模板填写容器规格

```python
from pylabrobot.resources import Container
from unilabos.registry.decorators import resource


def _container(
    *, name: str, diameter_mm: float, height_mm: float,
    max_volume_ul: float, category: str,
) -> Container:
    return Container(
        name=name,
        size_x=diameter_mm,
        size_y=diameter_mm,
        size_z=height_mm,
        max_volume=max_volume_ul,
        category=category,
    )


@resource(
    id="my_lab_sample_vial_50ml",
    displayname="My Lab 50 mL 样品瓶",
    category=["my_lab", "container", "sample_vial"],
    description="由样品瓶载架承载，可整体转运至检测工位。",
    metadata={
        "nominal_volume_ml": 50,
        "working_volume_ml": 40,
        "material": "PP",
    },
)
def sample_vial_50ml(name: str = "MyLabSampleVial50mL") -> Container:
    return _container(
        name=name,
        diameter_mm=40.0,
        height_mm=80.0,
        max_volume_ul=50_000.0,
        category="sample_vial",
    )
```

圆柱容器可以把直径同时填入 `size_x` 和 `size_y`，表示它在桌面上占用的最大范围。异形容器应填写真实长宽，必要时补充简化形状或三维模型，不能为了套用圆柱模板而忽略会影响放置和抓取的外形。

## 3. 如有需要，补充三维模型

如果已经有三维模型，可以同时登记 Xacro 模型文件和简化形状文件 `shape.yml`：

```python
@resource(
    id="my_lab_sample_vial_50ml",
    category=["my_lab", "container", "sample_vial"],
    model={
        "format": "xacro",
        "entry": "models/my_lab_sample_vial_50ml/resource.xacro",
        "macro": "my_lab_sample_vial_50ml",
        "shape": {
            "format": "unilab.shape/v1",
            "entry": "models/my_lab_sample_vial_50ml/shape.yml",
        },
    },
)
```

模型文件描述视觉和碰撞包络，不授予机器人运动权限。夹取点、速度、重量和安全联锁属于设备接入/现场标定。

## 4. 说明哪些位置允许放这种容器

在承载它的放置位中声明：

```python
holder.unilabos_extra = {
    "content_type": ["my_lab_sample_vial_50ml"],
}
```

同一放置位（Site）确实兼容多个模板时可以列出多个 ID，但需要分别验证高度、直径、方向和夹持约束。

## 5. 分清“容器规格”和“现场实物”

```json
{
  "template_id": "my_lab_sample_vial_50ml",
  "instance_id": "vial_001",
  "barcode": "SMP-2026-0001",
  "lot": "LOT-A",
  "current_volume_ml": 12.5,
  "site": "sample_rack_01/slot_01"
}
```

上面展示的是一件现场实物的数据。具体字段以当前系统提供的 启动图 和库存字段说明为准。模板定义最大容量，现场实例记录当前内容物和状态。

## 6. 交付前必须检查

- `@resource` ID 唯一，Catalog 可发现；
- 尺寸、容量、材质、方向和单位有来源；
- 标称容量与安全工作容量分开；
- 正确 放置位 可装载，错误类型、超尺寸和已占用 放置位 被拒绝；
- 加液/取液不会超过上限或低于死体积；
- 转运成功后只更新实例位置，不修改模板；
- 模型缺失或仅为代理时，页面明确显示其精度边界。
