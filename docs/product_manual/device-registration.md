# 设备登记、动作表单与设备联动

:::{admonition} 阅读角色
- **业务负责人**：确认设备用途、动作名称、参数单位、成功标准和安全边界。
- **开发人员**：实现设备合同、连接方式、启动图节点、模拟能力和测试。
- **验收人员**：验证参数越界、断线、超时、联锁、未知结果和真机恢复。
:::

本页说明怎样把业务人员确认的设备能力，登记成 Uni-Lab OS 页面和工作流可以使用的内容。设备定义使用 `@device`、`@action`、`@topic_config`、`ResourceSlot` 和 `DeviceSlot` 等正式接口。

:::{admonition} 本页完成条件
设备能出现在系统清单中；动作名称和输入表单对业务人员清楚；状态能稳定刷新；需要选择物料或其他设备时，页面提供选择器而不是要求填写内部编号；多设备联动在断线时能给出明确失败结果。
:::

## 1. 先登记设备基本信息

```python
from unilabos.registry.decorators import device


@device(
    id="my_lab_plate_reader",
    category=["analysis", "plate_reader"],
    displayname="微孔板读板机",
    description="读取微孔板检测结果；支持启动、查询进度和获取结果。",
    version="1.0.0",
    metadata={"supported_plate_formats": [96, 384]},
)
class MyLabPlateReader:
    ...
```

| 字段 | 业务含义 | 填写规则 |
| --- | --- | --- |
| `id` | 这一类设备的永久编号 | 只能使用英文字母、数字和下划线；设备发布后不要随意修改 |
| `category` | 设备按什么能力分类 | 从大类写到小类，不使用房间号、IP 或资产编号 |
| `displayname` | 页面显示名称 | 使用操作员熟悉的中文名称 |
| `description` | 设备用途 | 写清处理对象、主要能力和限制，不只写品牌型号 |
| `version` | 当前模板版本 | 动作或参数发生不兼容变化时升级版本 |
| `metadata` | 便于展示和筛选的固定规格 | 只写型号能力、量程、通道数等固定信息，不写运行状态或密码 |
| `model` | 可选三维模型 | 模型仅用于展示或布局；机器人运动仍以现场标定为准 |
| `available_sites` | 设备自带的固定放置位 | 例如读板机进样位、加热位；每个位置必须固定编号 |
| `handles` | 工作流画布上的输入输出端口 | 只有确实需要用连线表达数据或物料流向时才填写 |

同一个 Python 类确实对应多个型号时，可以用 `ids` 和 `id_meta` 一次登记多个设备类型。每个型号可以覆盖显示名称、端口、放置位和固定规格。型号之间动作或参数不同较大时，应拆成不同类，避免一个类中出现大量型号判断。

## 2. 让动作表单对业务人员可读

```python
from unilabos.registry.decorators import action
from unilabos.registry.placeholder_type import ResourceSlot


@action(
    displayname="开始读取微孔板",
    description="按指定检测方案读取一块微孔板。",
)
def read_plate(self, plate: ResourceSlot, protocol_name: str) -> dict:
    """
    Args:
        plate[待检测孔板]: 选择已经放在读板机进样位的孔板。
        protocol_name[检测方案]: 填写厂商软件中已经保存的方案名称。
    """
    ...
```

`displayname` 是页面上的动作名称，`description` 说明这个动作做什么。函数说明中的 `Args:` 会生成表单字段：方括号中写中文字段名，冒号后写填写要求。每个参数都应说明单位、范围、可选值和示例。

| 参数情况 | 应怎样写 |
| --- | --- |
| 数值 | 写明单位和允许范围，例如“温度，单位 °C，允许 20–80” |
| 固定选项 | 明确列出选项及含义，不让用户自由输入任意文字 |
| 危险动作 | 不提供会直接启动运动或加热的默认值 |
| 物料 | 使用 `ResourceSlot`，让页面从现有物料中选择 |
| 其他设备 | 使用 `DeviceSlot`，让页面从现有设备中选择 |
| 返回结果 | 固定包含 `success`；需要展示的信息放在 `message`、`data` 或 `error` 中 |

没有使用 `@action` 的公开方法可能被兼容扫描成 `auto-` 动作。新设备不要依赖这种自动猜测；需要给业务人员使用的动作应明确登记，内部辅助方法以下划线开头或使用 `@not_action` 排除。

## 3. 状态字段怎么定义

```python
from unilabos.registry.decorators import topic_config


@property
@topic_config(period=2.0)
def status(self) -> str:
    return self.data.get("status", "Unknown")
```

- 状态名称在同类设备间保持一致，例如 `Idle`、`Running`、`Error`、`Offline`、`Unknown`；
- `period=2.0` 表示每两秒刷新一次，刷新频率不能高到影响设备通信；
- 读取状态不能触发设备动作；
- 无法读取时返回 `Unknown` 或抛出明确错误，不能用“正常”掩盖失联；
- `self.data` 在设备启动时预先放入所有状态字段，避免页面首次加载时字段忽隐忽现。

## 4. 设备启动后的初始化

设备构造函数只保存配置，不应自动连接或执行危险动作。系统完成设备节点创建后，才会通过 `post_init` 提供运行环境：

```python
from unilabos.registry.decorators import not_action


@not_action
def post_init(self, ros_node) -> None:
    self._ros_node = ros_node
```

`post_init` 不是业务动作，必须排除在动作清单外。跨设备调用、系统计时等依赖运行环境的能力，只能在此步骤之后使用。

## 5. 设备自带放置位

如果设备本身有固定进样位、加热位或反应位，可通过 `available_sites` 登记：

```python
READER_SITES = [{
    "index": 0,
    "label": "plate_in",
    "visible": True,
    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
    "size": {"width": 128.0, "height": 86.0, "depth": 20.0},
    "content_type": ["my_lab_96_well_plate"],
    "description": "读板机进样位",
}]
```

同一设备模板内，`index` 和 `label` 都不能重复。尺寸不能为负数；`content_type` 只能填写已登记的物料类型。这里定义的是设备固定拥有的位置，不写当前由哪件物料占用，也不写实例 UUID。

## 6. 什么时候允许设备自动联动

只有业务确实要求“设备 A 的状态变化后，设备 B 自动响应”时，才使用跨设备订阅。订阅目标的设备实例编号必须与启动图（Graph JSON）中的 `id` 一致：

```python
from unilabos.utils.decorator import subscribe


@subscribe(device_id="pump_01", status_name="pressure", trigger_when_change=True)
def on_pressure_changed(self, value) -> None:
    ...
```

- `@subscribe` 只用于读取其他设备的状态；本设备状态直接读取自身属性；
- `trigger_when_change=True` 表示只有数值变化时才处理，避免重复触发；
- 发布设备尚未启动时系统会继续尝试建立订阅，但业务流程仍要能显示“等待设备”状态；
- 联动涉及运动、加热或放料时，必须再次检查联锁，不能把“收到状态”直接等同于“允许执行”；
- 调用其他设备动作失败时要保留失败原因，不能返回假成功。

## 7. 交付前检查

1. 页面名称和字段说明由不参与开发的业务人员试填一次；
2. 同类设备的动作名、参数名、单位和状态值保持一致；
3. 模拟设备覆盖成功、参数错误、超时、断线和设备拒绝五种结果；
4. 放置位编号与设备面板、现场标签和 启动图 完全一致；
5. 启动图 使用设备包检查报告给出的规范完整类型名称；
6. 密码、令牌和实验敏感信息没有写入代码、日志或 启动图。
