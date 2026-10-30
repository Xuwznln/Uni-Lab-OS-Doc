# 鼠标模拟点击（可选）

:::{admonition} 阅读角色
- **业务负责人**：确认设备用途、动作名称、参数单位、成功标准和安全边界。
- **开发人员**：实现设备合同、连接方式、启动图节点、模拟能力和测试。
- **验收人员**：验证参数越界、断线、超时、联锁、未知结果和真机恢复。
:::

本页适用于设备只能通过桌面软件操作、又没有可用接口的特殊情况。系统通过识别窗口并模拟鼠标键盘完成操作。由于窗口位置、弹窗和软件升级都可能导致操作失效，这种方式只作为临时或最后选择。

鼠标/键盘自动化只用于厂商软件没有程序接口、串口、网络协议或 PLC 接口的情况。
即使使用鼠标点击，工作流中仍应显示“开始检测、导出结果”等业务动作，不能让操作员填写坐标、窗口标题或按钮编号。

:::{admonition} 本页完成条件
交付标准设备类、GUI 适配器、受控桌面基线和异常测试；每次操作都能确认窗口、控件和完成状态，任何不确定结果都会停止并转人工。
:::

```{warning}
不存在可直接复用于任意厂商软件的鼠标自动化驱动。示例选择器必须依据目标上位机的实际可访问性树验证。
```

## 先确认没有更稳定的控制方式

```text
官方 API / SDK
→ 串口或网络协议
→ PLC / OPC UA
→ 可访问性控件自动化
→ 固定坐标和图像识别（最后选择）
```

固定坐标最容易受分辨率、缩放、窗口位置、主题和弹窗影响。能够使用控件名称、Automation ID
或可访问性角色时，不要使用坐标。

| 场景 | 建议 |
| --- | --- |
| 厂商提供 API、SDK、串口或 PLC 接口 | 不使用鼠标自动化 |
| 旧仪器只能通过固定桌面软件操作 | 可在隔离桌面评估 |
| 无人值守启动高温、运动等危险动作 | 不建议使用 |
| 低风险读取或临时演示 | 只在离线试运行或专用测试电脑上使用 |

## 必须提前约定的运行条件

- 自动化进程与 Uni-Lab 设备驱动分层，窗口操作封装在私有适配器中。
- 公开类继续使用选定大类的 `@action`、参数和 `@topic_config` 属性。
- 每次动作先确认应用进程、窗口身份、设备连接状态和安全条件。
- 点击前确认目标控件唯一、可见且可操作；点击后读取可验证的完成状态。
- 弹窗、失焦、遮挡、分辨率变化和应用重启必须进入失败或人工介入状态。
- 不通过盲目重复点击“解决”超时；不确定上一次动作是否完成时停止并核对。
- 截图和日志不得包含账号、样品隐私或其他敏感信息。

配置参数建议：

| 参数 | 类型 | 约束 |
| --- | --- | --- |
| `app_path` | `str` | 固定到已验收的软件版本，不从网页输入任意程序 |
| `window_identity` | `str` | 使用进程/窗口身份规则，不只匹配易变化标题 |
| `action_timeout` | `float` | 秒，必须大于 0 |
| `baseline_version` | `str` | 记录操作系统、软件版本和控件基线 |
| `screenshot_on_error` | `bool` | 仅保存脱敏错误证据 |
| `auto_start` | `bool` | 初次接入使用 `false`，由人工确认环境后启动 |

## 开发人员可复制的代码模板

示例实现蠕动泵的动作和状态。`DesktopAdapter` 代表项目选定并经过验证的 UI 自动化库，不是 Uni-Lab OS 内置类，接入者必须自行实现或替换。

```python
from typing import Any, Dict

from unilabos.registry.decorators import action, device, topic_config


@device(
    id="vendor_model_peristaltic_pump_gui",
    category=["合成制备仪器与设备", "实验泵", "蠕动泵"],
    description="某型号蠕动泵厂商上位机 GUI 兼容驱动",
    displayname="蠕动泵（GUI 自动化）",
)
class VendorModelPeristalticPumpGUI:
    def __init__(self, app_path: str, window_identity: str, **kwargs):
        self._ui = DesktopAdapter(app_path=app_path)
        self._window_identity = window_identity

    def connect(self) -> None:
        self._ui.attach_or_start()
        window = self._ui.require_window(self._window_identity)
        if not window.is_ready():
            raise ConnectionError("厂商上位机窗口未就绪")

    @action(description="设置转动速度")
    def set_speed(self, speed: float = 0.0) -> Dict[str, Any]:
        if speed < 0:
            raise ValueError("speed 必须大于或等于 0")
        field = self._ui.require_control(role="textbox", automation_id="speed")
        field.replace_text(str(speed))
        self._ui.require_control(role="button", name="应用").invoke()
        shown = float(self._ui.require_control(automation_id="current-speed").value())
        if shown != speed:
            raise RuntimeError("上位机未确认新的转速")
        return {"success": True, "message": "转速设置成功", "speed": shown}

    @action(description="正转")
    def rotate_forward(self) -> Dict[str, Any]:
        self._ui.require_control(role="button", name="正转").invoke()
        self._ui.wait_until_text(automation_id="run-state", value="运行中", timeout=10.0)
        return {"success": True, "message": "正转已启动"}

    @property
    @topic_config()
    def status(self) -> str:
        return self._ui.require_control(automation_id="run-state").text()

    @property
    @topic_config()
    def fault(self) -> bool:
        return self._ui.require_control(automation_id="alarm-state").text() != "正常"
```

## 把桌面软件加入启动清单

```json
{
  "id": "pump_gui_01",
  "name": "GUI 蠕动泵 01",
  "children": [],
  "parent": null,
  "type": "device",
  "class": "community.my_device_package.my_lab_pump_gui",
  "position": {"x": 0.0, "y": 0.0, "z": 0.0},
  "config": {
    "app_path": "/Applications/VendorApp.app",
    "window_identity": "vendor-main-window",
    "action_timeout": 30.0,
    "baseline_version": "approved-baseline-1",
    "screenshot_on_error": true,
    "auto_start": false
  },
  "data": {"status": "offline"}
}
```

路径和窗口身份是部署配置，不是公共动作参数。软件升级或控件树变化时应让基线校验失败，并重新验收后再更新 `baseline_version`。

## 必须固定并记录的电脑环境

| 项目 | 示例 |
| --- | --- |
| 操作系统和版本 | Windows 11 23H2 |
| 厂商软件版本 | 明确到构建号 |
| 显示缩放 | 100%（仅坐标方案需要固定） |
| 窗口身份 | 进程名、标题规则、Automation ID |
| 控件定位 | 角色 + 名称/Automation ID |
| 完成判据 | 状态文本、进度控件或结果文件 |
| 异常证据 | 错误文本、可脱敏截图、时间和动作 ID |

## 怎样确认自动操作可以交付

- 标准动作和状态与同大类串口、API、PLC 实现一致；
- 正常、重复提交、弹窗、失焦、遮挡和窗口关闭均有测试；
- 更换分辨率、缩放或软件版本时会明确失败，不会点错控件；
- 每个动作都有可观测完成条件，不能以“点击成功”代替设备成功；
- 超时不会无限点击或继续工作流；
- 无真实上位机验证时明确标为示例或待验收。

## 常见问题

| 现象 | 可能原因 | 建议处理 |
| --- | --- | --- |
| 找不到窗口 | 软件未启动、标题变化或登录失效 | 停止并人工核对软件版本和窗口身份 |
| 找到错误按钮 | 只使用坐标，分辨率或缩放变化 | 改用角色、名称、Automation ID 和层级 |
| 点击后没有完成提示 | 设备忙、弹窗遮挡或等待条件错误 | 保存脱敏证据并转人工，不继续点击 |
| 远程桌面中失效 | 锁屏、会话切换或焦点被抢占 | 使用受控专用桌面并限制并发 |
| 软件升级后异常 | 控件结构或文案变化 | 绑定版本并重新评审、验收 |
