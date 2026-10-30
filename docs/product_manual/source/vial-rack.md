# 小瓶载架

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本页说明怎样登记小瓶载架。可以把它理解为三层：载架是整个架子，每个孔位是一个固定放置位，现场的瓶子放在某个位置上。系统依靠这三层关系判断每只瓶子在哪里。

:::{admonition} 本页完成条件
先登记小瓶规格，再登记载架；为每个放置位设置固定编号和允许的物料类型，最后在启动图（Graph JSON）中建立“载架→放置位→小瓶”的归属关系。
:::

开发人员可使用 `BottleCarrier + ResourceHolder` 表示烧杯、样品瓶和试剂瓶载架，使用 `PlateCarrier + PlateHolder` 表示板型物料载架。无论使用哪一种代码类型，都要明确载架、放置位和物料之间的归属关系。

## 1. 先定义瓶子，再定义载架

载架的 `content_type` 要引用已经注册的小瓶模板，因此建议顺序为：

1. 在 `resources/materials.py` 定义 `my_lab_sample_vial_50ml`；
2. 在 `resources/carriers.py` 定义载架和放置位（Site）；
3. 在 `resources/__init__.py` 导入两个模块；
4. 在 启动图/库存中创建载架实例和小瓶实例；
5. 把小瓶实例挂到具体 放置位，而不是在模板中写死条码。

## 2. 给载架和每个放置位固定编号

```python
from pylabrobot.resources import Coordinate, ResourceHolder
from unilabos.registry.decorators import resource
from unilabos.resources.itemized_carrier import BottleCarrier


@resource(
    id="my_lab_vial_rack_12",
    displayname="My Lab 12 位样品瓶载架",
    category=["my_lab", "carrier", "vial_rack"],
    description="3 行 × 4 列；每个位点最多放一只 50 mL 样品瓶。",
    metadata={"site_count": 12, "site_naming": "R01C01..R03C04"},
)
def make_vial_rack(name: str = "my-lab-vial-rack") -> BottleCarrier:
    sites = {}
    for row in range(3):
        for column in range(4):
            label = f"R{row + 1:02d}C{column + 1:02d}"
            holder = ResourceHolder(
                name=f"{name}_{label}",
                size_x=40.0,
                size_y=40.0,
                size_z=80.0,
            )
            holder.location = Coordinate(
                x=10.0 + column * 50.0,
                y=10.0 + row * 50.0,
                z=0.0,
            )
            holder.unilabos_extra = {
                "content_type": ["my_lab_sample_vial_50ml"],
            }
            sites[label] = holder

    carrier = BottleCarrier(
        name=name,
        size_x=210.0,
        size_y=160.0,
        size_z=90.0,
        sites=sites,
        category="vial_rack",
    )
    carrier.num_items_x = 4
    carrier.num_items_y = 3
    carrier.num_items_z = 1
    return carrier
```

示例坐标和尺寸只展示结构，必须替换为图纸/实测值。不要依赖 `dict` 的临时序号表达设备槽位；放置位 名称应直接对应操作员、启动图 和设备协议都能核对的编号。

## 3. 是否预先放入演示用物料

载架模板可以提供 `fill_placeholders=True`，用于在演示或调试时预先显示吸头盒、烧杯或试剂瓶。使用时必须明确：

- 占位物料只用于演示/仿真初始化；
- 生产库存必须由真实实例、条码和盘点结果建立；
- 不要让占位实例与真实条码同时存在；
- `fill_placeholders=False` 时应得到结构完整但槽位为空的载架。

## 4. 单瓶转运与整架转运

- **单瓶转运**：锁定来源位置和目标位置，动作成功后再把瓶子的库存位置改到目标放置位；
- **整架转运**：移动载架根节点，所有子瓶继续保持在原 放置位 下；
- **结果不确定**：不改变资源树，要求扫码/现场确认；
- **错瓶/重复条码**：提交动作前拒绝，不通过改 放置位 名称绕过。

## 5. 交付前必须检查

- 放置位 数量、行列、命名、方向和停用位与实物一致；
- 每个放置位的尺寸都能容纳目标小瓶，允许的物料类型能被系统识别；
- 空架、满架、错瓶、重复条码、占用位和扫描失败均有负例；
- 单瓶取放后只有目标放置位 的父子关系变化；
- 整架移动时全部子瓶随载架移动，不产生重复实例；
- 设备超时或断线时库存不会提前标记“已取出”。
