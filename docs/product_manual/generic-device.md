# 通用设备规范

:::{admonition} 阅读角色
- **业务负责人**：确认设备用途、动作名称、参数单位、成功标准和安全边界。
- **开发人员**：实现设备合同、连接方式、启动图节点、模拟能力和测试。
- **验收人员**：验证参数越界、断线、超时、联锁、未知结果和真机恢复。
:::

本章说明所有设备都要遵守的共同规则。业务人员先确认设备能做什么、需要填写哪些参数、怎样判断成功；开发人员再把这些要求写成系统接口。后续无论使用 PLC、串口还是厂商软件，页面和工作流看到的名称都应保持一致。

```{important}
工作流只使用“设置温度、启动、停止、读取结果”等业务动作，不直接使用品牌命令、寄存器地址或软件按钮坐标。
可以把标准模板理解成设备的“服务清单”，把具体驱动理解成完成这些服务的“控制方法”。
```

## 每台设备必须说明四件事

| 部分 | 作用 | 约束 |
| --- | --- | --- |
| 身份 | 系统编号、分类、页面名称和用途 | 系统编号稳定且唯一 |
| 构造配置 | 连接地址、端口、超时和可选认证引用 | 参数必须有类型和安全默认值，构造阶段不执行危险动作 |
| 动作 | `@action` 方法 | 参数、单位、范围、返回值和失败语义明确 |
| 状态 | `@topic_config` 只读属性 | 无副作用，支持 `unknown/offline/fault`，刷新周期明确 |

## 先按用途选择设备类别

1. 按工艺能力查看当前 Workspace Catalog，选择语义最接近的设备大类，不要先按品牌建类。
2. 确认标准动作和状态能表达真实设备能力。
3. 品牌协议、寄存器地址、量程或单位不同，仍应复用同一大类。
4. 只有工作流语义发生变化时，才提出新增大类或兼容性扩展。

业务人员在开发前应确认三件事：设备完成什么业务动作、每个动作需要哪些输入及单位、
以及怎样判断成功、失败、超时和需要人工处理。不要用 `POST /run`、寄存器地址或按钮坐标
描述业务动作，应使用“设置温度”“正转”“启动读板”等跨协议语义。

| 术语 | 业务含义 | 在模板中的位置 |
| --- | --- | --- |
| 设备 | 可被系统识别和调度的一台仪器或工站 | `@device` |
| 动作 | 对外提供的一项完整操作 | `@action` 方法 |
| 状态 | 定期读取、不会改变设备的只读信息 | `@topic_config` 属性 |
| 注册 ID | 启动图（Graph JSON）和历史工作流引用的稳定身份 | `@device(id=...)` |
| 模拟验证 | 不连接真实设备，只检查动作、参数和流程 | 使用模拟设备或离线试运行 启动图 |

## 动作和状态的填写规则

- `device_id` 和 Python 接口名使用 `snake_case`，大类 ID 不含品牌或型号。
- 接口类型只能是 `action` 或 `property`。
- 动作参数必须保持名称、类型、默认值和返回注解一致。
- 动作参数使用明确的 Python 类型；只有当前运行版本实际支持时才使用复杂嵌套类型。
- 动作返回结构化结果，例如 `{"success": true, "message": "..."}`。
- 属性只暴露状态，不得在读取属性时触发有副作用的设备动作。
- 驱动边界必须校验范围和单位；发生换算时必须显式记录。
- 超时、设备拒绝、无效响应和断线应抛出可定位问题的异常。
- 不得绕过设备或 PLC 内的门禁、急停、报警和安全联锁。

## 设备包目录模板

```text
my-device-package/
├── pyproject.toml
├── package.yaml
├── README.md
├── my_device_package/
│   ├── __init__.py
│   └── devices/
│       └── vendor_model_peristaltic_pump.py
├── resources/
├── workflows/
└── tests/
```

`pyproject.toml` 至少声明 Python 版本、`unilabos` 依赖和包发现规则；`package.yaml` 保存包身份
和工作流清单。连接地址属于 启动图 实例配置；账号和密钥通过 Secret/环境变量注入，不应写入标准类或提交到仓库。

## 设备基本信息怎么填写

```python
@device(
    id="my_lab_peristaltic_pump",
    category=["liquid_handling", "pump", "peristaltic_pump"],
    displayname="蠕动泵",
    description="按指定转速输送液体，并提供运行和故障状态。",
)
```

| 参数 | 写法 |
| --- | --- |
| `id` | `snake_case`，不带实例编号；同一 Python 类永久稳定 |
| `category` | 从大类到小类排列；不写 IP、房间号或厂商网址 |
| `displayname` | 操作者可理解的短名称 |
| `description` | 一句话写清能力、对象和限制 |

动作参数应在签名中完成类型和默认值，在方法开头完成范围/枚举校验：

```python
@action(description="设置转速；单位 rpm，允许范围 0-300")
def set_speed(self, speed_rpm: float) -> dict[str, object]:
    if not 0.0 <= speed_rpm <= 300.0:
        raise ValueError("speed_rpm 必须在 0-300 rpm")
    ...
```

危险动作不要提供会立即运动/加热的默认值。无法确认结果时抛出异常或返回明确的 `unknown`，不得返回假成功。

## 标准类示例：蠕动泵

下面用蠕动泵展示一份完整的动作和状态清单。标准模板不负责实际通信；具体型号的驱动必须提供相同的动作名称、参数和返回结果。

```python
from typing import Any, Dict, Optional

from unilabos.registry.decorators import action, device, topic_config


@device(
    id="peristaltic_pump",
    category=["合成制备仪器与设备", "实验泵", "蠕动泵"],
    description="蠕动泵标准接口：同一大类跨品牌统一的动作与参数。",
    displayname="蠕动泵",
)
class PeristalticPump:
    def __init__(
        self,
        device_id: Optional[str] = None,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 9600,
        timeout: float = 2.0,
        **kwargs,
    ):
        """
        Args:
            device_id[设备实例编号]: 与 启动图 中的设备 id 一致。
            port[串口]: 设备连接的串口名称。
            baudrate[波特率]: 必须与设备通信设置一致。
            timeout[超时时间(s)]: 单次通信最长等待时间，必须大于 0。
        """
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0 秒")
        self.device_id = device_id or "peristaltic_pump"
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.data: Dict[str, Any] = {"status": "Idle", "fault": False}

    @action(description="设置转动速度")
    def set_speed(self, speed_rpm: float) -> Dict[str, Any]:
        raise NotImplementedError("请在设备包中实现该动作")

    @action(description="正转")
    def rotate_forward(self) -> Dict[str, Any]:
        raise NotImplementedError("请在设备包中实现该动作")

    @property
    @topic_config()
    def status(self) -> str:
        return self.data.get("status", "Unknown")

    @property
    @topic_config()
    def fault(self) -> bool:
        return self.data.get("fault", False)
```

具体型号应使用自己的类型编号，但继续归入相同类别，并保持
`set_speed(speed_rpm: float)`、`rotate_forward()`、`status: str` 和 `fault: bool` 等动作与状态定义不变。

## 怎样确认设备已经可以交付

```bash
python -m compileall my_device_package
unilab package inspect --path . --out ./inspect-output
```

- 标准模板与具体实现的动作、参数、返回类型和属性一致；
- 每个动作都有协议命令或 PLC 节点映射；
- 每个属性都有数据来源、类型、单位和刷新策略；
- 已覆盖正常、非法参数、超时、设备故障和断线恢复；
- 未连接真机或可靠模拟器时，只能标记为静态/模拟验证通过。

## 常见问题

| 现象 | 常见原因 | 处理建议 |
| --- | --- | --- |
| 设备出现但没有动作 | 动作没有登记，或系统没有加载对应文件 | 核对 `@action` 并重新执行设备包检查 |
| 页面参数名称难以理解 | 缺少中文说明、单位或清楚的字段名 | 修改设备的统一动作定义，不要只在某一个页面临时改名 |
| 状态一直为空或旧值 | 属性来源不可靠或缓存没有有效期 | 核对 `@topic_config`、查询方式和刷新策略 |
| 换品牌后工作流失效 | 厂商驱动改变了标准接口 | 恢复同大类接口一致性，把差异封装在驱动内部 |
| 重试后设备执行两次 | 把未知结果直接当失败重放 | 先查询设备状态，再由恢复流程决定是否重试 |
