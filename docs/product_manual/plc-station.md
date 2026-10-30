# PLC 控制的工站

:::{admonition} 阅读角色
- **业务负责人**：确认设备用途、动作名称、参数单位、成功标准和安全边界。
- **开发人员**：实现设备合同、连接方式、启动图节点、模拟能力和测试。
- **验收人员**：验证参数越界、断线、超时、联锁、未知结果和真机恢复。
:::

本页适用于由 PLC 控制的自动化工站，例如传送、抓取、加液或装配工位。业务人员负责确认动作、完成条件和安全联锁；自动化工程师提供点位表；开发人员把点位转换成 Uni-Lab OS 能调用的业务动作。

PLC 接入是标准设备接口的一种底层实现。PLC 负责实时顺序、执行器控制和安全联锁，Uni-Lab
负责下发标准动作、读取状态并编排工作流。本页给出第一次接入 PLC 工站所需的资料、点表、代码、启动图（Graph JSON）和验收约束。

:::{admonition} 本页完成条件
交付标准设备类、PLC 驱动、节点映射表、启动图 节点和模拟测试；动作握手、范围、超时、复位、报警与联锁均有验证结果。
:::

## 各方需要负责什么

- 工作流调用“启动、停止、设置温度”等业务动作，不直接读写 PLC 点位地址（NodeId）。
- PLC 驱动与标准类的动作名、参数名、类型、默认值和属性集合必须一致。
- 通用动作定义只保存容易理解的点位名称，不保存现场 PLC 地址。
- 现场点位表使用 CSV 文件保存中文名称、英文名称、读写用途、数据类型和 PLC 地址。CSV 是可以用表格软件打开的文本表格。
- 安全联锁、急停和设备保护保留在 PLC；Python 驱动不得绕过。

## 开始前要准备哪些资料

- 工站名称、用途、设备大类和包含的执行部件；
- PLC/OPC UA 地址、控制器程序版本和正式点位表；
- 每个点位的读写方向、数据类型、单位和逻辑含义；
- 启动、已接收、运行、完成、报警、急停和门禁时序；
- 每个动作的正常时长、最大等待时间和人工恢复方式；
- 与现场点位同语义的仿真映射或 PLC 模拟器。

只提供带地址的截图是不够的；缺少含义、类型和单位时，不能可靠判断一个布尔值代表启动、
开门还是报警。

## 按这个顺序完成接入

1. 在 `devices/plc_station.py` 中先定义与协议无关的动作和状态；
2. 建立 `config/plc_station_nodes.csv`，把每个业务名称对应到现场 PLC 地址；
3. 在构造函数中读取 `url`、`csv_path`、超时和订阅参数，但不自动触发动作；
4. 有参动作先校验范围/单位，再写设定值；触发动作执行完整握手；
5. 在 启动图 中创建一个 `type: device` 节点；
6. 使用模拟 OPC UA 服务验证后，再连接隔离 PLC；
7. 最后验证急停、门禁、报警、掉线和结果未知场景。

构造参数规范：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `url` | `str` | 是 | 完整 OPC UA URL，不在代码中写死 |
| `csv_path` | `str` | 是 | 相对设备包根目录或明确的绝对部署路径 |
| `username` | `str \| None` | 否 | 不写入 启动图，优先通过 Secret 注入 |
| `password` | `str \| None` | 否 | 禁止日志输出 |
| `action_timeout` | `float` | 是 | 秒，必须大于 0；按最长正常动作加余量 |
| `subscription_interval` | `int` | 否 | 毫秒；过小会造成 PLC/网络压力 |
| `auto_connect` | `bool` | 否 | 初次接入固定为 `false` |

## PLC 点位常见的三种用法

| 接口 | PLC 交互 | 示例 |
| --- | --- | --- |
| 有参设置动作 | 写入设定值节点 | `set_speed(speed)` → `Speed_Setpoint` |
| 无参触发动作 | 触发、等待完成、复位、等待完成复位 | `rotate_forward()` |
| 状态属性 | 强制从服务端读取逻辑状态节点 | `fault` → `Fault` |

触发动作的完整时序为：

```text
写 Trigger=True
→ 等待 Complete=True
→ 写 Trigger=False
→ 等待 Complete=False
→ 返回成功
```

轮询完成节点时必须强制服务端读取，不能只相信可能过期的订阅缓存。

## 把业务名称和 PLC 地址对应起来

```text
Name,EnglishName,NodeType,DataType,NodeId
正转触发,Rotate_Forward_Trigger,Variable,Boolean,ns=4;s=Pump.RotateForward.Trigger
正转完成,Rotate_Forward_Complete,Variable,Boolean,ns=4;s=Pump.RotateForward.Complete
速度设定,Speed_Setpoint,Variable,Double,ns=4;s=Pump.Speed.Setpoint
运行状态,Status,Variable,String,ns=4;s=Pump.Status
故障状态,Fault,Variable,Boolean,ns=4;s=Pump.Fault
```

PLC 地址必须来自正式点位表或经过验证的模拟器，不能根据名称猜测。

## 开发人员可复制的代码模板

下面用蠕动泵展示接口和握手方式。`OpcUaClientWithSubscription` 表示设备包中经测试的 OPC UA 客户端封装：

```python
from typing import Any, Dict

from unilabos.registry.decorators import action, device, topic_config
from base_opcua_client import OpcUaClientWithSubscription


@device(
    id="vendor_model_peristaltic_pump_plc",
    category=["合成制备仪器与设备", "实验泵", "蠕动泵"],
    description="某型号蠕动泵 PLC/OPC UA 驱动",
    displayname="蠕动泵（PLC）",
)
class VendorModelPeristalticPumpPLC(OpcUaClientWithSubscription):
    def __init__(self, url: str, csv_path: str, action_timeout: float = 120.0,
                 username: str | None = None, password: str | None = None,
                 **kwargs):
        super().__init__(url=url, username=username, password=password, **kwargs)
        if action_timeout <= 0:
            raise ValueError("action_timeout 必须大于 0")
        self.action_timeout = action_timeout
        self.load_nodes_from_csv(csv_path)

    @action(description="设置转动速度")
    def set_speed(self, speed: float = 0.0) -> Dict[str, Any]:
        if speed < 0:
            raise ValueError("speed 必须大于或等于 0")
        self.set_node_value("Speed_Setpoint", speed)
        return {"success": True, "message": "设置转动速度已下发", "speed": speed}

    @action(description="正转")
    def rotate_forward(self) -> Dict[str, Any]:
        self.set_node_value("Rotate_Forward_Trigger", True)
        if not self._wait_until_true("Rotate_Forward_Complete", description="正转完成"):
            raise TimeoutError("正转失败：动作未完成")
        self.set_node_value("Rotate_Forward_Trigger", False)
        if not self._wait_until_false("Rotate_Forward_Complete", description="正转完成复位"):
            raise TimeoutError("正转失败：完成状态复位超时")
        return {"success": True, "message": "正转完成"}

    @property
    @topic_config()
    def fault(self) -> bool:
        value = self.get_node_value("Fault", force_read=True)
        return bool(value) if value is not None else False
```

## 把现场工站加入启动清单

```json
{
  "id": "pump_01",
  "type": "vendor_model_peristaltic_pump_plc",
  "config": {
    "url": "opc.tcp://127.0.0.1:4840",
    "csv_path": "config/pump_01_nodes.csv",
    "action_timeout": 120.0,
    "use_subscription": true,
    "cache_timeout": 5.0,
    "subscription_interval": 500
  }
}
```

真实地址和凭证应从部署配置或密钥系统注入；示例地址不能直接用于生产。

## 怎样确认工站可以交付

- 标准类与 PLC 类的公开接口完全一致；
- 现场点表的数据类型、读写权限和 Namespace 已核对；
- 触发与完成位在成功和失败路径都能正确复位；
- 设置值具有范围、单位和缩放验证；
- 超时后不会继续执行下一动作或报告假成功；
- 断线、重连、PLC 报警、急停和重启路径均有证据；
- 如果项目会根据点位表自动生成代码，不要直接修改生成结果；应修改动作规范或点位表后重新生成。

## 常见问题

| 现象 | 可能原因 | 建议处理 |
| --- | --- | --- |
| 一直显示未就绪 | PLC 未置位或逻辑名与现场映射不一致 | 先核对 PLC 画面，再检查映射 CSV |
| 写入类型不匹配 | 布尔值、整数或小数的定义不一致 | 以 PLC 服务器导出的类型为准，修正点位表和动作定义 |
| 动作完成但页面超时 | 完成位或复位时序不一致 | 对照 PLC 时序记录，不盲目延长超时 |
| 断线后重复执行 | 重连逻辑自动重放物理动作 | 重连只恢复通信，先查询状态并人工确认 |
| 仿真通过、现场失败 | 仿真与真实点表版本不一致 | 两份映射保持同名同语义并记录版本 |
