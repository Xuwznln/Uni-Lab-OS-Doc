# 2. 物料定义模板

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本目录写给第一次配置 Uni-Lab OS 物料的业务人员、项目实施人员和开发人员。目标是把实验室里的孔板、吸头盒、瓶子、载架、仓库和工位，按照统一规则登记到自己的设备包中。完成后，系统应能识别物料、判断它能放在哪里，并在搬运后继续追踪它的位置和状态。

你不需要先理解全部程序概念。可以先把现实关系说清楚：**是什么物料、外形和容量是多少、能放在哪些位置、现场这一件物料的条码是什么**。再按照本章给出的表格和代码模板填写。

:::{note} 本章中的几个词
**模板**表示一类物料的固定规格，例如“50 mL 样品瓶”；**实例**表示现场的一件实物，例如条码为 `SAMPLE-001` 的瓶子；**载架**表示能放置物料的架子；**放置位（Site）**表示载架上的一个具体位置；**启动图（Graph JSON）**是系统启动时读取的设备与物料清单；**资源树**就是“载架下面有哪些放置位、每个放置位上有什么物料”的层级关系。
:::

```{toctree}
:maxdepth: 1

deck-warehouse
stack
plate-tiprack
container-template
vial-rack
magazine
sheet-battery
material-state
other-materials
```

## 先分清“规格、实物和位置”

| 层次 | 回答的问题 | 设备包中的实现 |
| --- | --- | --- |
| 物料模板 | “这类东西是什么、规格是多少？” | 使用 `@resource` 登记，并选择 `Resource`、`Container`、`Plate`、`TipRack` 等合适的基础类型 |
| 载架/仓库模板 | “它能放在哪里？” | `PlateCarrier`、`BottleCarrier`、`ResourceHolder`、`PlateHolder` |
| 放置位（放置位） | “载架中的哪个位置？” | 给每个位置固定编号，并用 `content_type` 说明允许放什么 |
| 物料实例 | “当前是哪一个实物？” | 启动图/库存中的实例、条码、批次、当前位置与状态 |
| 工作流引用 | “流程使用哪一个实例？” | `ResourceSlot`，以及动作成功后的 `host.transfer_resource(...)` |

不要把这几层混在一起。模板保存不会随单件实物变化的规格，例如尺寸和容量；实例保存条码、批次、当前体积和当前位置；设备负责实际取放；只有设备确认动作成功后，系统才更新物料位置。

## 在自己的设备包中建立目录

下面是推荐的最小目录。业务人员只需知道：`materials.py` 放物料规格，`carriers.py` 放载架和放置位，`graph.json` 放现场实际存在的设备与物料。开发人员按下面结构建文件即可。

```text
your_lab_package/
├── package.yaml
└── your_lab/
    ├── resources/
    │   ├── __init__.py
    │   ├── materials.py       # 板、瓶、吸头盒等物料本体
    │   ├── carriers.py        # 载架、仓库、工位及 放置位
    │   └── models/
    │       └── <resource_id>/
    │           ├── resource.xacro
    │           ├── shape.yml
    │           └── meshes/
    ├── workflows/
    └── config/
        └── graph.json
```

三维模型可以按物料分别存放，也可以集中存放。无论选择哪一种方式，登记表中的模型路径必须能找到文件，物料类型编号和模型目录也不要随意改名。

在 `resources/__init__.py` 中加入下面两行，让系统启动检查设备包时能够找到这些物料定义：

```python
from . import carriers, materials

__all__ = ["carriers", "materials"]
```

## 第一步：选择最接近的物料类型

| 你的物料 | 建议基类 | 适用结构 |
| --- | --- | --- |
| 整体烧杯、样品瓶、试剂瓶、废液桶 | `Container` | 单体容器 |
| 规则孔板 | `Plate` + `Well` | 有序孔位 |
| 吸头盒 | `TipRack` + `TipSpot` + `Tip` | 有序吸头位 |
| 板库、缓存位、设备工位 | `PlateCarrier` + `PlateHolder` | 板型物料库位 |
| 瓶架、粉桶架、TIP 盒堆栈 | `BottleCarrier` + `ResourceHolder` | 通用物料库位 |
| 规则排列的多层仓库 | `WareHouse` + `warehouse_factory` | 按行、列、层生成固定位置 |
| 一台工作站的完整台面 | `Deck` | 组装多个仓库、载架和设备固定位置 |
| 极片、隔膜、垫片、箔材 | `ElectrodeSheet` | 需要厚度、质量和材料状态的片状物料 |
| 组装完成的电池 | `Battery` | 保存电解液、压力和检测结果等实例状态 |
| 每个洞位能堆叠多张片材的弹夹 | `MagazineHolder` + `Magazine` | 多洞位、多层片材堆栈 |
| 无孔、无容量但需要追踪的夹具/盖板 | `Resource` 或 `Container` | 可整体搬运和追踪的普通物料 |

优先选择表中已有类型。只有现有类型无法表达关键业务结构时，才开发新的类型。只是名称、品牌或容量不同，不需要再创造一套新的基础类型。

## 第二步：登记物料规格

下面使用可复用的 Container 工厂模式。把 ID、尺寸、容量和说明换成你已确认的数据：

```python
from pylabrobot.resources import Container
from unilabos.registry.decorators import resource


@resource(
    id="my_lab_sample_vial_50ml",
    displayname="My Lab 50 mL 样品瓶",
    category=["my_lab", "container", "sample_vial"],
    description="可在样品瓶载架与检测工位之间转运的 50 mL 样品瓶。",
)
def sample_vial_50ml(name: str = "MyLabSampleVial50mL") -> Container:
    return Container(
        name=name,
        size_x=40.0,
        size_y=40.0,
        size_z=80.0,
        max_volume=50_000.0,
        category="sample_vial",
    )
```

`id` 是系统识别“这一类物料”的永久编号，不是现场实物的条码。中文名称或供应商名称变化时不要修改它。如果尺寸、孔位或兼容规则发生变化，导致旧流程不能继续使用，应登记一个新的类型编号，并安排已有库存的数据迁移。

### 物料登记字段怎么填写

| 参数 | 是否必填 | 类型 | 规范 |
| --- | --- | --- | --- |
| `id` | 是 | 文字 | 物料类型的永久编号，使用小写英文和下划线；不能包含条码、实物编号或位置编号 |
| `displayname` | 建议 | `str` | 操作者可理解的名称和规格，如“50 mL 样品瓶” |
| `category` | 是 | 文字列表 | 从大类写到小类；它用于分类检索，不能代替“这个位置允许放什么”的兼容规则 |
| `description` | 是 | `str` | 写清用途、承载对象和限制，不只写型号 |
| `version` | 建议 | `str` | 采用明确版本规则；不兼容结构变化必须升级 |
| `class_type` | 视系统版本而定 | 文字 | 只有当前版本的设备包检查工具明确要求时才填写 |
| `model` | 可选 | `dict` | 只包含受支持格式、入口、宏和 shape；路径相对定义模块解析 |
| `metadata` | 可选 | 键值表 | 保存材质、规格来源、模型精度等补充信息；不保存某件实物的当前位置、条码或密码 |
| `available_sites` | 可选 | 放置位列表 | 物料模板自身固定拥有的位置；每个位置的编号和名称唯一，不保存当前占用信息 |
| `handles` | 可选 | 端口列表 | 只有工作流画布确实需要表达输入输出流向时才填写 |

下面这些字段描述物料的实际尺寸和结构：

| 参数 | 单位/类型 | 约束 |
| --- | --- | --- |
| `name` | `str` | 实例显示名；提供稳定且无副作用的默认值 |
| `size_x/y/z` | `float`，mm | 均大于 0；坐标系和放置方向必须记录 |
| `max_volume` | `float`，µL | 仅容器/Well 使用；不能用 mL 数值冒充 µL |
| `category` | `str` | 资源实例类别，与装饰器分类语义一致 |
| `sites` | 放置位清单 | 每个位置编号必须唯一且长期稳定；一个位置最多直接放一件物料 |
| `ordered_items/ordering` | 有顺序的位置清单 | 需要按孔或按位置操作时必须填写，名称和顺序发布后不能随意改变 |
| `unilabos_extra` | 补充信息 | 其中 `content_type` 必须填写系统已经登记的物料类型编号 |

## 第三步：给每个放置位置固定编号

对于堆栈和板库，每个实际位置都应有一个长期不变的放置位编号。开发人员用 `ResourceHolder` 表示普通物料放置位：

```python
from pylabrobot.resources import Coordinate, ResourceHolder

holder = ResourceHolder(
    name="sample_rack_slot_01",
    size_x=40.0,
    size_y=40.0,
    size_z=80.0,
)
holder.location = Coordinate(x=10.0, y=10.0, z=0.0)
holder.unilabos_extra = {
    "content_type": ["my_lab_sample_vial_50ml"],
}
```

`content_type` 表示“这个位置允许放哪些物料”。它必须填写前一步已经登记的物料类型编号，不能写“任何瓶子”等模糊文字。没有经过尺寸和工艺验证的物料，也不能为了让流程通过而加入允许列表。

## 第四步：补充外形和三维模型

如有三维模型，可登记 Xacro 模型文件和用于页面布局、碰撞检查的简化形状文件：

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

没有精确三维模型时，可以先用长、宽、高组成的外接盒表示占用空间，并明确标注“仅用于页面布局”。未经测量和现场标定的数据不能作为机器人抓取或运动坐标。

## 第五步：把现场实物加入启动清单

前面的代码定义“这一类物料的固定规格”，启动图 定义“现场现在有哪些实物”。下面示例表示一号载架的 `R01C01` 位置放着条码为 `SAMPLE-001` 的样品瓶。`class` 要替换成设备包检查报告中显示的完整类型名称：

```json
{
  "nodes": [
    {
      "id": "sample_rack_01",
      "name": "一号样品瓶载架",
      "children": ["vial_001"],
      "parent": null,
      "type": "warehouse",
      "class": "community.my_device_package.my_lab_vial_rack_12",
      "position": {"x": 0.0, "y": 0.0, "z": 0.0},
      "config": {
        "sites": [{
          "label": "R01C01",
          "name": "R01C01",
          "position": {"x": 10.0, "y": 10.0, "z": 0.0},
          "size": {"width": 40.0, "height": 40.0, "depth": 80.0},
          "content_type": ["my_lab_sample_vial_50ml"],
          "visible": true,
          "occupied_by": "vial_001"
        }]
      },
      "data": {}
    },
    {
      "id": "vial_001",
      "name": "样品瓶 001",
      "children": [],
      "parent": "sample_rack_01",
      "type": "container",
      "class": "community.my_device_package.my_lab_sample_vial_50ml",
      "position": {"x": 10.0, "y": 10.0, "z": 0.0},
      "config": {
        "size_x": 40.0,
        "size_y": 40.0,
        "size_z": 80.0,
        "max_volume": 50000.0,
        "category": "sample_vial"
      },
      "data": {"barcode": "SAMPLE-001", "current_volume_ul": 12500.0}
    }
  ]
}
```

载架的 `children`、瓶子的 `parent` 和放置位的 `occupied_by` 必须互相对应，三处都指向同一个瓶子编号。空位置不要填写 `occupied_by`。整份 启动图 中每个 `id` 都必须唯一，位置统一使用毫米。某件实物的条码只写在实例数据中，不能写进通用物料模板。

## 第六步：搬运成功后再更新物料位置

工作流需要同时传递“搬什么、从哪里搬、搬到哪里”。`ResourceSlot` 是系统用于传递这些物料对象的标准字段。只有机械设备明确返回成功后，才调用一次 `transfer_resource` 更新库存位置：

```python
from unilabos.registry.placeholder_type import ResourceSlot


def transfer(
    resource: ResourceSlot,
    source_warehouse: ResourceSlot,
    target_warehouse: ResourceSlot,
    source_site: str,
    target_site: str,
):
    got = robot.get_resource(
        resource=resource,
        warehouse=source_warehouse,
        site=source_site,
    )
    put = robot.put_resource(
        resource=got.resource,
        warehouse=target_warehouse,
        site=target_site,
    )
    return host.transfer_resource(
        resource=put.resource,
        target_device="target_device_id",
        mount_resource=target_warehouse,
        site=target_site,
    )
```

如果设备失败、超时，或无法确认物料是否已经搬走，系统中的位置先保持不变，并提示现场人员核对。不要在结果不确定时提前把物料标记到目标位置。

## 怎样确认已经接入成功

```bash
python -m compileall your_lab
unilab package inspect --path . --out ./inspect-output
```

然后依次验证：

1. Catalog 能发现每个 `@resource`，ID、类别和模型路径正确；
2. 启动图 能创建资源树，所有 放置位 名称唯一且 `content_type` 可解析；
3. 正确物料能放入，错误类型、超尺寸和已占用 放置位 会被拒绝；
4. 离线试运行中能完成选料、锁定、模拟动作和一次库存位置更新；
5. 真实设备验收前，坐标、方向、夹持、联锁和在位信号均有现场依据。

## 各类型适配指南

- [工作站台面与仓库](deck-warehouse.md)：Deck、WareHouse、普通堆栈/料架、位置坐标和排列顺序；
- [光电堆栈与旋转堆栈：物料 + 设备](stack.md)：同时定义整机设备能力和挂在设备节点下的 Stack/Site 物料树；光电设备面尚无统一规范；
- [孔板与吸头盒（Plate／Tiprack）](plate-tiprack.md)：孔、吸头位和资源树规模；
- [Container](container-template.md)：烧杯、样品瓶、试剂瓶与容量；
- [小瓶载架](vial-rack.md)：小瓶架、放置位以及载架与瓶子的归属关系；
- [弹夹](magazine.md)：顺序槽位、换夹和批次追踪；
- [片状物料与组装成品](sheet-battery.md)：极片、垫片、箔材和组装后成品；
- [物料状态与内容物](material-state.md)：条码、液体、固体和运行状态的保存规则；
- [其他可追踪物料](other-materials.md)：盖板、底座、夹具及自定义资源。
