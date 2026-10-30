---
orphan: true
---

# 设备与动作

:::{admonition} 阅读角色
- **业务负责人**：确认设备用途、动作名称、参数单位、成功标准和安全边界。
- **开发人员**：实现设备合同、连接方式、启动图节点、模拟能力和测试。
- **验收人员**：验证参数越界、断线、超时、联锁、未知结果和真机恢复。
:::

## Uni-Lab OS 页面的入口边界

Uni-Lab OS 没有独立“设备管理”或“单动作调试”页面时，日常用户可以在总监控中查看运行概况，在工作流和任务中观察设备动作；设备目录与单动作 Task 由 API 或 Theia Workbench 提供。

这意味着“设备包里定义了动作”不等于“Uni-Lab OS 页面有一个按钮可以直接执行它”。对于真机动作，应优先使用经过发布、预检和资源锁保护的工作流。

设备开发者如果要创建自己的设备包，应先完成[工作区](workspace.md)：该页面说明 `@device`、启动图中的 `class`、实例 `id` 和工作流选择器之间的加载关系。

## Driver 作者合同

新驱动应把公开边界写成显式合同。下面展示一个最小设备类型；完整设备包、启动图和工作流结构见[工作区](workspace.md)。

```python
from typing import TypedDict

from unilabos.registry.decorators import action, device, not_action, topic_config
from unilabos.utils.decorator import subscribe


class RunResult(TypedDict):
    success: bool
    message: str


@device(id="example_station", category=["example"], displayname="示例工作站")
class ExampleStation:
    def __init__(self, endpoint: str = "sim://local") -> None:
        self.endpoint = endpoint
        self._upstream_status = "unknown"

    @action(displayname="运行", description="执行一次设备操作")
    def run(self, target: float = 1.0) -> RunResult:
        """执行一次设备操作。

        Args:
            target[目标值]: 经过设备合同校验的业务目标值。
        """
        return {"success": True, "message": "completed"}

    @action(always_free=True, displayname="读取健康状态")
    def read_health(self) -> RunResult:
        """只读取状态，不发送命令或改变物料。"""
        return {"success": True, "message": "ready"}

    @property
    @topic_config(period=1.0)
    def status(self) -> str:
        return "idle"

    @subscribe(device_id="upstream_1", status_name="status")
    def on_upstream_status(self, value) -> None:
        self._upstream_status = value

    @not_action
    def connect(self) -> None:
        """公开生命周期方法，但不进入 Action Catalog。"""
        ...

    def _send_command(self) -> None:
        ...
```

所有业务操作都应显式使用 `@action()`。旧 Registry 直扫路径仍含未装饰公开方法的 `auto-*` 兼容投影，但现代 Package Catalog 不保证把它作为可用 Action；内部辅助方法使用 `_private`，必须公开的辅助或生命周期方法使用 `@not_action`。

`@action(always_free=True)` 会绕过同设备的普通排队，只能用于无副作用的轻量读取。发送命令、改变设备状态、移动物料、占用工位或依赖互斥顺序的动作都不能标记为 `always_free`。

源码顺序必须是 `@property` 在上、`@topic_config(...)` 在下。Python 会先执行靠近函数的 `@topic_config`，再由 `@property` 包装；顺序反过来会让 topic 元数据无法按当前合同附着。

`@subscribe` 当前从 `unilabos.utils.decorator` 导入，只订阅其他设备状态。本设备状态直接读取属性。若让 ROS 图自动识别消息类型，回调参数不要添加 `int`、`str` 等内置类型注解，以免它被误当作 ROS 消息类型。

Action 参数文档使用 `name[Title]: description`，如 `target[目标值]: ...`。默认值只写在函数签名中；类型、默认值、标题和描述会共同形成表单与 Job 合同。

驱动构造器也应使用与启动图 `config` 对齐的显式命名参数，不要用通用 `config` 或 `**kwargs` 静默吞掉拼写错误。动作具名结果、工作流输出和完整加载边界见[工作区](workspace.md)。

## 真实设备与模拟器

真实 Driver 与模拟实现可以更换 transport，但必须保持相同的 Action 名称、参数类型与默认值、具名结果 Schema，以及状态 topic 的名称和语义。这样同一工作流和 Workbench 表单才能在两种环境复用。

模拟器应有确定性的状态推进，并支持受控时延、超时和故障注入；模拟路径不得连接真实设备。`dry-run` 只生成模拟动作回执，不构造 Driver。要验证 PLC-Sim 等协议模拟器，应使用隔离 启动图 和 `normal`，具体步骤见[PLC-Sim 仿真器](plc-sim.md)。

模拟实现如何被选择，应以当前 OS 和设备包实际支持的 启动图/构造参数为准。现行公共接口没有通用 `device_pair.yaml` 或 `--sim_engine`，不要自行发明这些文件或启动参数。

## 如何确认设备已加载

设备包中有 Driver 定义，不代表本次启动已经创建了设备实例。业务人员应运行 `unilab package inspect` 确认类型可发现，再通过 `unilab workspace status --json` 或设备目录核对 启动图 中的实例。

| 检查对象 | 示例 | 通过标准 |
| --- | --- | --- |
| Host 节点 | `host_node` | 能承担物料提交和主机侧协调 |
| 设备实例 ID | `mixing_station_01` | 与 启动图、工作流选择器完全一致 |
| 设备类型 | `community.example_lab.mixing_station` | 能从设备包检查报告找到 |
| 在线状态 | `connected` / `ready` | Uni-Lab OS 已登记，且状态更新时间正常 |
| 动作目录 | `run`、`read_health` | 名称、参数和结果与设备包合同一致 |

数量不应写死。每次安装都以实际 启动图 和状态输出为准；没有实例化到活动 启动图 的设备不能算在线设备。

## 动作如何执行

一次标准设备动作遵循以下路径：

1. 工作流节点绑定设备实例和动作模板。
2. 创建任务时冻结节点参数和设备绑定。
3. Uni-Lab OS 取得设备、物料和工位锁后创建 Job。
4. Uni-Lab OS 调用设备驱动执行完整 Job 参数。
5. 设备持续返回 feedback，结束时返回成功、失败、取消或未知结果。
6. Uni-Lab OS 推进后继节点；物料转移动作在物理成功后唯一一次提交位置。

设备动作和 Task 使用幂等身份，防止网络重试产生第二次物理动作。不要通过重复提交新任务来代替恢复原任务。

## 单动作运行

如果必须单独验证设备动作：

- 使用连接到 Python Local Profile 的 Theia Workbench“设备”页，或由管理员使用设备动作 Task API；
- 核对设备、动作、材料绑定和参数；
- 只在现场安全条件成立、设备允许远程自动运行时提交；
- 在任务页观察 feedback 和结果，必要时取消；
- 不要使用已退役的 `/api/v1/debug/*` 接口。

## 设备边界必须怎样写

设备包应为每类设备提供一张边界表，让业务人员知道系统返回的到底是什么。

| 设备类型 | 必须说明的边界 | 使用影响 |
| --- | --- | --- |
| 相机或检测设备 | 返回触发成功、文件路径还是算法结论 | 不能把空路径或触发回执当作检测结果 |
| 加液或投料设备 | 目标值、指令值、传感器实测值分别是什么 | 结果字段必须带单位，工艺容差单独判断 |
| 开合盖或搬运设备 | 容器状态、占位和互锁由谁确认 | 缺少传感器确认时必须增加人工或设备侧校验 |
| 移液或称量设备 | 稳定标志、校准状态和精度 | 不能只凭动作完成判断数据合格 |
| 机械臂 | 超时、断线和重启后的在途命令如何处理 | 结果未知时先核对现场，禁止自动重发 |

:::{danger}
强制释放设备锁只改变调度器的逻辑占用，不会停止机械运动，也不能证明设备已经自然结束。必须先执行现场停机/确认，核对精确 holder 和 fencing 信息，并填写审计原因。
:::

## PLC-Sim 的作用

PLC-Sim 可以模拟 OPC UA 命令、动作过程、传感器变化、延时、超时和故障；仓库还提供独立的 Modbus TCP/RTU/ASCII 模拟器。它适合离线联调，不负责工作流调度、物料权威状态或机器人运动安全。仿真通过不能代替真机的 IP、NodeId、联锁、急停、工位、容量和恢复语义验收。

仿真器的交付要求、启动顺序和联调步骤见[PLC / OPC UA 仿真器](plc-sim.md)。
