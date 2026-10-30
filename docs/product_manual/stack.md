# 光电堆栈与旋转堆栈：物料 + 设备

:::{admonition} 必须同时定义物料面和设备面
:class: warning

光电堆栈和旋转堆栈都是**物料资源 + 设备能力**组成的复合对象，不能只登记其中一面：物料面登记整机、纵向 Stack、层位 Site、交互位和库存；设备面登记连接、动作、传感器状态、故障和生命周期。

Uni-Lab OS 当前已有旋转堆栈的设备动作参考；光电堆栈只有物料面可以按本页规范交付，设备面尚无统一动作与状态规范。因此，光电堆栈可以完成资源布局和库存登记，但在设备规范补齐前不能宣称“光电能力已经接入”。
:::

:::{admonition} 阅读角色
- **业务负责人**：同时确认承载结构、库位、库存规则，以及运动、光电、门和故障能力。
- **开发人员**：用设备节点登记整机身份与动作，在其下面分别声明每个纵向 Stack、层位 Site 和交互位；设备地址来自已验收的 Stack/层位表。
- **验收人员**：分别验收物料树和设备能力，再验收两者地址映射；任何一面缺失都不能算完整接入。
:::

现场的一台堆栈同时有两个不可替代的侧面。一面是**物料资源**：保存有哪些纵向 Stack、每根 Stack 有哪些层位、每位允许放什么、现在被谁占用；另一面是**设备能力**：电机旋转、选 Stack/层、光电判断有无料、开关门和报故障。两面描述的是同一台实物，不是两台互不相关的对象。

两类设备当前状态不同：

| 现场对象 | 物料面 | 设备面 | 当前状态 |
| --- | --- | --- | --- |
| 带光电传感器的堆栈 | 必须登记整机、Stack、Site、兼容类型和库存 | 光电状态与故障接口尚无统一规范 | <span class="status status-limited">当前受限</span>：物料面可用，设备面不能按官方规范验收 |
| 可驱动的旋转堆栈 | 必须登记整机、Stack、Site、交互位和库存 | 必须登记出库、入库、状态、故障和连接生命周期 | <span class="status status-ready">当前可用</span>：两面都按本页规范接入 |
| 没有电机、没有光电的多层料架 | 按仓库登记 | 无设备面 | <span class="status status-ready">当前可用</span>：按仓库页登记 |

:::{admonition} 本页完成条件
静止料架并入[工作站台面与仓库](deck-warehouse.md)。光电堆栈必须交付物料树，并把设备能力标为未规范；旋转堆栈必须同时交付设备定义和挂在该设备节点下的物料树。工作流只调用已登记动作和 Site，设备成功后才更新库存。
:::

## 1. 设备 + 物料的强制定义规范

同一台光电或旋转堆栈必须包含下面四项交付物：

| 交付物 | 定义方式 | 保存什么 | 不保存什么 |
| --- | --- | --- | --- |
| 整机设备类型 | `@device` | 动作、状态、故障、连接参数和生命周期 | 不保存每个 Site 当前放了什么 |
| Stack 物料类型 | `@resource` | 单根纵向 Stack 的规格、层数、层间距和 Site 模板 | 不发送电机或光电命令 |
| 现场复合实例 | 启动图 | 一个 `type: device` 的整机根节点，以及挂在它下面的 Stack、Site 和交互位 | 不重复定义类型规范 |
| 地址映射合同 | Stack/Site 元数据 + 驱动校验 | 每个 Site 唯一对应 `stacker` / `level`，交互位唯一对应 `TFS1` | 不用画布坐标或数组下标猜控制器地址 |

启动图中的设备节点不只是执行入口。实例同步会为它建立 Material 身份，并把它的子资源纳入同一棵资源树。因此，推荐结构是一个复合根，而不是两个平级根：

```text
cytomat_01（type=device；同时是整机 Material 身份）
├── stack_01（type=plate_carrier；物料资源）
│   ├── S01-L01（Site）
│   └── ...
├── stack_02（type=plate_carrier；物料资源）
│   └── ...
└── transfer_station（物料资源）
    └── TFS1（Site）
```

设备动作必须通过同一地址映射找到这棵树中的 Site；库存系统必须通过同一 Site 找到设备命令所用的 `stacker` / `level`。禁止建立一个孤立的设备节点和一个无法追溯到它的仓库根，也禁止让设备与物料各维护一份可独立修改的地址表。

因此：

- 没有电机、没有光电、只是多层/多列料架 → 不要读本页，去仓库页。
- 带光电 → 先完整登记物料树；设备面记为未规范缺口，不要把“物料树已完成”说成“光电已接入”。
- 能旋转驱动 → 在同一复合实例中同时交付「Stack/Site 物料树 + 设备动作」，任何一面都不能省略。

## 2. 光电堆栈：物料面必须定义，设备面尚无统一规范

带光电传感器的堆栈看起来像架子，实际上是一台要轮询状态的设备。典型能力包括：某层/某列是否有料、交互位是否到位、门是否关闭、故障或遮挡。

物料面仍按第 1 节定义整机 Material 身份、各纵向 Stack、逐层 Site 和交互位。**只有设备面到此为止。** Uni-Lab OS 核心仓库没有光电堆栈的标准设备类、标准动作名或示例驱动。下面几条只是将来补齐设备规范时仍应遵守的通用原则，**不是**现在可以照着实现并验收通过的设备清单：

- 将来用 `@device` 登记身份和类别，不要用仓库工厂冒充设备；
- 将来用 `@action` 表达业务动作，不要把光电地址暴露给工作流；
- 将来用 `@topic_config` 发布占位、门、故障等只读状态；
- 光电读数不能代替库存权威。传感器说“有料”、库存说“空位”时，先停在人工核对。

在设备规范发布前：新项目可以交付光电堆栈的物料模板与资源树，但不要自封“官方光电堆栈设备模板”；不要把“页面上能画出一排槽位”理解成“光电堆栈已经可驱动”。需要接入光电能力时，先作为项目缺口登记，再按[通用设备规范](generic-device.md)另开设备类，并在验收记录里写明：这是项目私有实现，不是产品设备规范。

## 3. 旋转堆栈：物料树 + 标准设备类

可驱动的旋转堆栈是复合对象。整机设备节点同时拥有 Material 身份；纵向 Stack、库位 Site 和交互位作为它的子资源，旋转、选层、出库、入库走设备动作。只有载架循环而没有 `@device` 动作，或只有驱动而没有物料树，都不能称为已经接入旋转堆栈。

必须同时交付三件事：

| 交付物 | 职责 | 不负责 |
| --- | --- | --- |
| 设备类 | 出库、入库；把 Stack/层位转换成控制器命令；返回结构化成功/失败 | 不保存“这个槽位现在是谁” |
| 启动图复合实例 | 现场这一台堆栈的设备/Material 身份、连接参数、超时和子资源关系 | 不代替类型定义 |
| 仓库 / 放置位 | 每根纵向 Stack、每一层、允许物料类型和交互位 | 不代替电机命令 |

工作流只调用业务动作 `outbound` / `inbound`，参数只用已标定的 Stack 编号和层号。驱动在内部映射到控制器协议。工作流不得出现品牌命令名、寄存器地址或画布 `x/y/z`。

### 3.1 业务能力怎样落到动作

现场常说的“旋转、回零、选层/列、报告在位”，按本规范这样落地，不要再发明一套平行接口：

| 现场说法 | 本规范怎么做 | 不要怎么做 |
| --- | --- | --- |
| 选 Stack / 选层 | 出库、入库的必填参数 `stacker`（第几根纵向 Stack）、`level`（第几层） | 不要传视觉坐标；不要让驱动猜默认地址 |
| 旋转 | 出库、入库时，设备把该 Stack/层位转到交互位（或从交互位收回） | 不要单独暴露“转多少度”给工作流 |
| 回零 | **不是必选动作**。已验收协议只有出库、入库两项，没有独立回零命令。零点由现场标定写入启动图/驱动配置 | 不要在动作里给行列默认值来冒充回零；不要返回假成功 |
| 在位状态 | 设备报告运行状态 `status` / `fault`。槽位占用以库存树为准，在动作成功后更新一次 | 不要用圆心、半径、层高推断占位；协议没有按槽查询时不要伪造光电读数 |

某型号若另外提供回零或槽位光电，作为**该型号扩展**登记，不改变本大类必选动作。扩展动作仍须遵守[通用设备规范](generic-device.md)：有协议映射、有成功/失败语义、未知结果不得写成成功。

### 3.2 建模原则：先声明每个纵向 Stack，再组装整机

旋转堆栈不能直接定义成一张扁平的“行 × 列”库位表。一个完整设备必须按实物结构拆成以下资源树：

```text
cytomat_01（整机设备节点 / Material 身份）
├── stack_01（纵向 Stack 1）
│   ├── L01
│   ├── L02
│   └── ...
├── stack_02（纵向 Stack 2）
│   └── ...
├── ...
└── transfer_station（交互位）
    └── TFS1
```

每个纵向 Stack 都是一个独立载架实例，拥有自己的稳定编号、层数、层间距和逐层 Site。整机只负责组装这些 Stack、保存它们在转盘上的相对位置，并提供设备动作。不得把 `stack=03, level=07` 压成一个不透明的连续序号，否则现场更换单个 Stack、调整层间距或停用某层时会破坏全部库位身份。

| 层级 | 稳定身份示例 | 发布后允许变化的内容 | 不允许静默变化的内容 |
| --- | --- | --- | --- |
| 整机 | `cytomat_01` | 连接参数、超时 | 设备实例 ID |
| 纵向 Stack | `stack_03` | 经变更确认的层间距 | Stack 编号、父级 |
| 层位 Site | `S03-L07` | 占用物料 | Site 名称、对应的 `stacker=3, level=7` |
| 交互位 | `TFS1` | 占用物料 | 名称和机械交接含义 |

下面用仓库已有 Cytomat 串口原型中的命令作为贯穿示例。它展示完整的定义和生命周期，但**不是某个 Cytomat 型号的出厂参数**；Stack 数量、每 Stack 层数、层间距、应答成功码、门和联锁必须用项目所装设备的手册与现场验收记录替换。

### 3.3 Cytomat 资源定义：纵向 Stack 与整机组装

先定义“一个纵向 Stack”。同类 Stack 可以复用同一工厂，但必须逐个创建独立实例，不能让多个 Stack 共享 `sites` 或 Site 对象。

```python
from math import cos, radians, sin
from typing import Dict

from pylabrobot.resources import Coordinate, PlateCarrier, PlateHolder, Resource
from unilabos.registry.decorators import resource


def make_cytomat_vertical_stack(
    *,
    name: str,
    stacker: int,
    levels: int,
    pitch_mm: float,
) -> PlateCarrier:
    """声明一个纵向 Stack；构造期间只创建资源，不连接或驱动设备。"""
    if stacker <= 0 or levels <= 0 or pitch_mm <= 0:
        raise ValueError("stacker、levels 和 pitch_mm 必须大于 0")

    sites: Dict[int, PlateHolder] = {}
    for level in range(1, levels + 1):
        site = PlateHolder(
            name=f"S{stacker:02d}-L{level:02d}",
            size_x=128.0,
            size_y=86.0,
            size_z=pitch_mm,
            pedestal_size_z=0.0,
        )
        site.location = Coordinate(0.0, 0.0, (level - 1) * pitch_mm)
        site.unilabos_extra = {
            "content_type": ["my_lab_sbs_plate"],
            "controller_address": {"stacker": stacker, "level": level},
        }
        sites[level - 1] = site

    stack = PlateCarrier(
        name=name,
        size_x=140.0,
        size_y=100.0,
        size_z=levels * pitch_mm,
        sites=sites,
        category="cytomat_vertical_stack",
    )
    stack.unilabos_extra = {
        "stacker": stacker,
        "levels": levels,
        "pitch_mm": pitch_mm,
    }
    return stack


@resource(
    id="my_lab_cytomat_carousel",
    category=["warehouse", "plate_storage", "rotating_stack", "cytomat"],
    displayname="Cytomat 旋转堆栈总成",
    description="由多个独立纵向 Stack 和一个 TFS1 交互位组装。",
)
class MyLabCytomatCarousel(Resource):
    def __init__(
        self,
        name: str = "cytomat_carousel",
        setup: bool = False,
        **kwargs,
    ):
        super().__init__(
            name=name,
            size_x=1200.0,
            size_y=1200.0,
            size_z=900.0,
            category="rotating_stack",
        )
        if setup:
            self.setup()

    def setup(self) -> None:
        # 每项都代表一根实物纵向 Stack。示例值必须替换为本机验收表。
        stack_specs = [
            {"stacker": 1, "levels": 21, "pitch_mm": 25.0},
            {"stacker": 2, "levels": 21, "pitch_mm": 25.0},
            {"stacker": 3, "levels": 15, "pitch_mm": 35.0},
            {"stacker": 4, "levels": 15, "pitch_mm": 35.0},
        ]
        radius_mm = 430.0

        for index, spec in enumerate(stack_specs):
            stacker = spec["stacker"]
            stack = make_cytomat_vertical_stack(
                name=f"stack_{stacker:02d}",
                **spec,
            )
            angle = index * 360.0 / len(stack_specs)
            self.assign_child_resource(
                stack,
                location=Coordinate(
                    radius_mm * cos(radians(angle)) + 600.0,
                    radius_mm * sin(radians(angle)) + 600.0,
                    0.0,
                ),
            )

        tfs_site = PlateHolder(
            name="TFS1",
            size_x=128.0,
            size_y=86.0,
            size_z=40.0,
            pedestal_size_z=0.0,
        )
        tfs_site.location = Coordinate.zero()
        tfs_site.unilabos_extra = {"content_type": ["my_lab_sbs_plate"]}
        transfer_station = PlateCarrier(
            name="transfer_station",
            size_x=160.0,
            size_y=120.0,
            size_z=80.0,
            sites={0: tfs_site},
            category="plate_transfer_station",
        )
        self.assign_child_resource(
            transfer_station,
            location=Coordinate(520.0, 0.0, 300.0),
        )
```

`setup=False` 必须保留。Registry 扫描类型时不能顺便生成数百个 Site；只有启动图实例明确传入 `setup: true` 时才组装整机。生产设备包宜把 `stack_specs` 移到经过版本管理的型号配置中，但仍应逐项列出每个 Stack，不能只写一个无法审计的总容量。

示例中的尺寸和四根 Stack 只是演示如何组装，不代表任何 Cytomat 型号。正式交付至少要为每根 Stack 登记：`stacker`、`levels`、`pitch_mm`、启用/停用层，以及每层允许的板型。

### 3.4 Cytomat 设备定义：地址解析与动作边界

设备驱动使用同一组 `stacker` / `level` 地址访问上面的 Site。仓库现有原型使用 `ll:in` 初始化、`se:cs` 设置单个 Stack 层间距、`mv:st` 从库位送到 TFS1、`mv:ts` 从 TFS1 收回库位；设备包应把串口对象封装进类中，不能在模块导入时打开端口。

```python
from typing import Any, Dict, Optional

from unilabos.registry.decorators import action, device, topic_config


@device(
    id="my_lab_cytomat",
    category=["plate_storage", "rotating_stack", "cytomat"],
    description="Cytomat 参考接口；按独立 Stack 编号和层号存取微孔板。",
    displayname="Cytomat 旋转堆栈",
)
class MyLabCytomat:
    def __init__(
        self,
        device_id: Optional[str] = None,
        port: str = "COM18",
        baudrate: int = 9600,
        timeout: float = 15.0,
        stack_levels: Optional[Dict[int, int]] = None,
        **kwargs,
    ):
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0 秒")
        self.device_id = device_id or "cytomat_01"
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.stack_levels = stack_levels or {1: 21, 2: 21, 3: 15, 4: 15}
        self._serial = None
        self.data: Dict[str, Any] = {"status": "Offline", "fault": False}

    def _validate_address(self, stacker: int, level: int) -> None:
        if isinstance(stacker, bool) or not isinstance(stacker, int):
            raise TypeError("stacker 必须是整数")
        if isinstance(level, bool) or not isinstance(level, int):
            raise TypeError("level 必须是整数")
        if stacker not in self.stack_levels:
            raise ValueError(f"未登记的 Stack: {stacker}")
        if not 1 <= level <= self.stack_levels[stacker]:
            raise ValueError(f"Stack {stacker} 没有第 {level} 层")

    def _send_checked(self, command: str) -> str:
        """发送命令，并按本机协议校验应答；失败或未知应答必须抛异常。"""
        raise NotImplementedError("按所装 Cytomat 型号实现串口收发和成功码解析")

    def connect(self) -> None:
        """显式打开串口；模块导入和构造函数中不得连接设备。"""
        raise NotImplementedError("创建 serial.Serial，并保存到 self._serial")

    def initialize(self, pitch_by_stack: Dict[int, int]) -> Dict[str, Any]:
        self._send_checked("ll:in")
        for stacker, pitch_mm in pitch_by_stack.items():
            if stacker not in self.stack_levels or pitch_mm <= 0:
                raise ValueError("初始化参数与资源定义不一致")
            self._send_checked(f"se:cs {stacker:02d} {pitch_mm}")
        self.data.update(status="Idle", fault=False)
        return {"success": True, "message": "Cytomat 初始化完成"}

    @action(description="从指定纵向 Stack 的层位送板到 TFS1")
    def outbound(self, stacker: int, level: int) -> Dict[str, Any]:
        self._validate_address(stacker, level)
        response = self._send_checked(f"mv:st {stacker:02d} {level:02d}")
        return {"success": True, "stacker": stacker, "level": level, "response": response}

    @action(description="从 TFS1 收板到指定纵向 Stack 的层位")
    def inbound(self, stacker: int, level: int) -> Dict[str, Any]:
        self._validate_address(stacker, level)
        response = self._send_checked(f"mv:ts {stacker:02d} {level:02d}")
        return {"success": True, "stacker": stacker, "level": level, "response": response}

    def shutdown(self) -> None:
        """停止轮询并关闭串口；不改写库存归属。"""
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        self.data["status"] = "Offline"

    @property
    @topic_config()
    def status(self) -> str:
        return self.data.get("status", "Unknown")

    @property
    @topic_config()
    def fault(self) -> bool:
        return self.data.get("fault", False)
```

这里将通用动作参数明确为 `stacker` / `level`，因为它们与资源树和 Cytomat 协议一一对应。若上层合同仍使用 `row` / `column`，必须在设备包中固定映射 `column → stacker`、`row → level`，对外只保留一种叫法，不得让同一 Site 同时存在两套可漂移地址。

动作至少返回 `success: bool`。只有 `_send_checked` 已确认设备成功应答时才能返回 `success=true`；超时、断线、设备拒绝、无效响应或结果不确定时必须抛异常或返回明确失败。出库、入库会改变设备姿态和物料位置，不能标记为 `always_free`。

### 3.5 启动图：一个设备根节点挂载完整物料总成

启动图中只使用一个整机根节点：它以 `type: device` 提供串口连接和动作，同时由实例同步取得 Material 身份。资源总成作为它的子节点，继续承载各 Stack、Site 与库存。设备面和物料面职责分开，但在同一棵树中通过父子关系明确绑定。

```json
{
  "nodes": [
    {
      "id": "cytomat_01",
      "name": "Cytomat 01 控制器",
      "type": "device",
      "class": "community.my_device_package.MyLabCytomat",
      "children": ["cytomat_01_storage"],
      "parent": null,
      "config": {
        "port": "COM18",
        "baudrate": 9600,
        "timeout": 15.0,
        "stack_levels": {"1": 21, "2": 21, "3": 15, "4": 15}
      },
      "data": {}
    },
    {
      "id": "cytomat_01_storage",
      "name": "Cytomat 01 库位总成",
      "type": "warehouse",
      "class": "community.my_device_package.MyLabCytomatCarousel",
      "children": [],
      "parent": "cytomat_01",
      "config": {"setup": true},
      "data": {}
    }
  ]
}
```

示例省略了由 `setup()` 生成的子树序列化结果；系统加载后必须能从 `cytomat_01` 展开到 `cytomat_01_storage`，再看到 `stack_01` 至 `stack_04`。每个 Stack 下分别出现自己的 Site，以及独立的 `transfer_station/TFS1`。验收时应导出实际资源树，逐项比对设备上的标签，并确认设备注册中的 `material_uuid` 指向这个整机 Material 实例。

### 3.6 完整生命周期：定义、启动、出库、入库、恢复、停机

| 阶段 | 设备动作 | 资源 / 库存动作 | 失败规则 |
| --- | --- | --- | --- |
| 定义 | 登记 Cytomat 驱动类型 | 分别声明每个纵向 Stack，再组装整机与 TFS1 | Stack 数量、层数或地址表不一致则禁止发布 |
| 实例化 | 读取端口、波特率、超时，不运动 | `setup=true` 创建资源树并恢复库存 | 构造期间不得打开串口或发送命令 |
| 启动 | `connect()`，再执行 `ll:in`；按 Stack 执行 `se:cs` | 核对资源树地址表与驱动 `stack_levels` 完全一致 | 任一初始化命令失败则保持未就绪 |
| 出库前 | 确认设备空闲、门和联锁满足 | 锁定 `Sxx-Lyy`、其中的板以及空的 `TFS1` | 空位、TFS1 被占或类型不兼容时不发命令 |
| 出库 | `mv:st stacker level` | 暂不改库存 | 超时或未知结果进入人工核对 |
| 出库确认 | 解析明确成功应答 | 只更新一次：板从 `Sxx-Lyy` 移到 `TFS1` | 未确认成功不得改归属 |
| 入库前 | 确认设备空闲 | 锁定 TFS1 中的板和空目标层位 | 目标已占用时不发命令 |
| 入库 | `mv:ts stacker level` | 暂不改库存 | 超时或未知结果进入人工核对 |
| 入库确认 | 解析明确成功应答 | 只更新一次：板从 `TFS1` 移到目标层位 | 未确认成功不得改归属 |
| 重连恢复 | 查询 Basic State（原型命令 `ch:bs`）并恢复就绪态 | 对照 TFS1、目标层位和现场实物；不自动猜位置 | 设备状态与库存冲突时冻结相关 Site |
| 停机 | 停止轮询并关闭串口 | 持久化已确认库存，不移动物料 | 正在动作或结果不确定时先进入人工处置 |

工作流绑定启动图里的设备实例 ID，并显式传递资源树中解析出的 Stack 编号和层号：

```python
from unilabos.workflow.authoring import device, workflow

cytomat = device("cytomat_01")


@workflow(workflow_uuid="...", displayname="Cytomat 单板往返验证")
def cytomat_round_trip(*, stacker: int, level: int) -> None:
    cytomat.outbound(stacker=stacker, level=level)
    # 设备成功后，由库存转移合同把板从 Sxx-Lyy 更新到 TFS1。
    cytomat.inbound(stacker=stacker, level=level)
    # 设备成功后，再把同一块板从 TFS1 更新回原 Site。
```

工作流不得根据列表下标猜 `stacker`，也不得把画布 `x/y/z` 当控制器地址。库存权威在资源树；设备成功只是更新库存的前提，见第 4 节。

## 4. 设备确认成功后再更新库存

无论将来接入光电还是现在接入旋转，库存更新顺序相同：

1. 校验来源放置位有物料、交互位为空且类型兼容；
2. 锁定设备、目标放置位和物料实例；
3. 调用设备动作（出库、入库等）；
4. 确认设备返回成功，并核对接线的在位/光电信号（若有；光电规范未发布前不得把未接入的光电当成已核对）；
5. 只更新一次库存归属，把物料挂到交互位或目标载架；
6. 失败或结果不确定时不改归属，进入人工核对。

动作参数使用稳定放置位或设备层/列；由驱动转换成控制器字段。工作流不要传视觉 `x/y/z`。

## 5. 交付前必须检查

- 静止料架已经在仓库页完成；
- 光电堆栈的物料树已经验收；设备面在没有产品规范时不得因槽位画完就通过；
- 旋转堆栈已分别声明每个纵向 Stack，并组装为包含交互位的完整资源树；
- 旋转堆栈只有一个整机设备根，其 Material 身份、子资源树和设备注册 `material_uuid` 能互相对应；
- `outbound` / `inbound` 的名称、`stacker` / `level` 参数类型和返回结构与设备类一致；
- 启动图设备实例 ID 与工作流 `device(...)` 一致；每个 Site 与唯一的 Stack/层位地址一致；
- 空位、占用、错误类型、满栈和重复请求会被拒绝；
- Stack 编号和层号来自标定/库存目录；驱动无默认位置；非法类型（浮点、字符串数字、布尔）会被拒绝；
- 旋转堆栈的零点、方向、超时与联锁不由视觉模型推断；
- 光电占位与库存不一致时有人工路径，不会静默改树；
- 离线试运行走完“出库 → 交互位 → 入库”，库存只更新一次；
- 真机前单独验证门、急停、掉电和动作结果不确定场景。
