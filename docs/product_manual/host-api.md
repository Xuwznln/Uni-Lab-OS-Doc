# 上位机 API 接入

:::{admonition} 阅读角色
- **业务负责人**：确认设备用途、动作名称、参数单位、成功标准和安全边界。
- **开发人员**：实现设备合同、连接方式、启动图节点、模拟能力和测试。
- **验收人员**：验证参数越界、断线、超时、联锁、未知结果和真机恢复。
:::

“上位机”是设备厂商提供的控制软件。本页适用于该软件提供 HTTP 接口或 SDK 的情况。接入完成后，业务人员在 Uni-Lab OS 中直接选择“开始检测、查询进度、读取结果”等动作，不需要打开厂商软件或填写接口地址。

当设备由厂商软件、网关服务或电脑后台程序控制时，应把厂商提供的接口转换成统一的业务动作。接口只是控制设备的技术方式，不应改变同一类设备在页面上显示的动作和状态名称。

:::{admonition} 本页完成条件
交付物应包含设备动作定义、接口连接代码、启动配置和返回数据检查。还要说明登录认证、任务是立即完成还是后台执行、重复请求会不会重复创建任务、超时后怎么办、服务重启后怎样恢复，以及各错误码代表什么。
:::

## 什么情况下使用这种方式

- 厂商提供稳定的 HTTP、WebSocket、gRPC、COM 或 Python/.NET SDK；
- 上位机能够返回任务 ID、状态或明确的完成结果；
- 可以配置连接、读取和动作完成超时；
- 鉴权信息能够从环境或密钥系统注入。

如果上位机仅有图形界面且没有受支持 API，再使用[鼠标模拟点击](mouse-automation.md)。

## 开始前要确认的规则

1. 先从当前工作区的设备类型清单中选择最接近的类别和动作。
2. 每个 API 路径映射到一个标准动作或状态，不向工作流暴露厂商路径。
3. 同步 API 只有在设备动作真正完成后才返回成功。
4. 异步 API 保存任务 ID，并轮询或订阅到终态；仅收到 HTTP 202 不算动作成功。
5. 将 HTTP 状态、厂商错误码和超时转换为可定位异常。
6. API 响应必须校验结构和类型，不接受缺字段时的猜测默认值。
7. 日志只记录请求 ID、动作和非敏感结果，不记录令牌或完整实验数据。

接入前应向厂商确认服务地址、接口版本、加密和登录方式、参数单位和范围、任务是否立即完成、哪些状态代表最终完成、错误码含义、用于防止重复创建任务的请求编号，以及服务重启后的任务恢复方式。

构造参数规范：

| 参数 | 类型 | 约束 |
| --- | --- | --- |
| `base_url` | `str` | 含协议和版本前缀；去除末尾 `/` 后保存 |
| `connect_timeout` | `float` | 秒，必须大于 0 |
| `read_timeout` | `float` | 秒，查询与动作轮询可分别配置 |
| `action_timeout` | `float` | 秒，大于最长正常动作时间 |
| `token_env` | `str` | 保存环境变量名，不保存 Token 值 |
| `verify_tls` | `bool` | 生产环境保持 `true`，禁止静默关闭证书校验 |

## 连接信息怎么填写

```json
{
  "id": "pump_api_01",
  "type": "vendor_model_peristaltic_pump_api",
  "config": {
    "base_url": "http://127.0.0.1:9080/api/v1",
    "connect_timeout": 3.0,
    "read_timeout": 10.0,
    "action_timeout": 120.0,
    "token_env": "PUMP_API_TOKEN"
  }
}
```

## 开发人员可复制的代码模板

以下示例使用蠕动泵标准接口。URL 和 JSON 字段是适配结构示意，必须按厂商 API 文档替换。

```python
from typing import Any, Dict

from unilabos.registry.decorators import action, device, topic_config


@device(
    id="vendor_model_peristaltic_pump_api",
    category=["合成制备仪器与设备", "实验泵", "蠕动泵"],
    description="某型号蠕动泵上位机 API 驱动",
    displayname="蠕动泵（上位机 API）",
)
class VendorModelPeristalticPumpAPI:
    def __init__(self, base_url: str, action_timeout: float = 120.0, **kwargs):
        self.base_url = base_url.rstrip("/")
        self.action_timeout = action_timeout
        self._client = self._build_client(**kwargs)

    @action(description="设置转动速度")
    def set_speed(self, speed: float = 0.0) -> Dict[str, Any]:
        if speed < 0:
            raise ValueError("speed 必须大于或等于 0")
        response = self._client.put("/pump/speed", json={"speed": speed})
        payload = self._require_json_success(response, action="set_speed")
        return {"success": True, "message": "转速设置成功", "speed": payload["speed"]}

    @action(description="正转")
    def rotate_forward(self) -> Dict[str, Any]:
        response = self._client.post("/pump/actions/rotate-forward")
        payload = self._require_json_success(response, action="rotate_forward")
        result = self._wait_for_job(payload["job_id"], timeout=self.action_timeout)
        if result["state"] != "completed":
            raise RuntimeError(f"正转失败：{result['state']}")
        return {"success": True, "message": "正转完成", "job_id": payload["job_id"]}

    @property
    @topic_config()
    def status(self) -> str:
        payload = self._get_status()
        return str(payload["status"])

    @property
    @topic_config()
    def fault(self) -> bool:
        payload = self._get_status()
        return bool(payload["fault"])
```

## 任务不会立即完成时怎样显示进度

建议将厂商状态归一为有限集合：`queued`、`running`、`completed`、`failed`、`cancelled`、
`unknown`。驱动必须为未知状态失败关闭，不得把未知或超时当作成功。

| 情况 | 标准处理 | 是否直接重试 |
| --- | --- | --- |
| 2xx 且返回任务号 | 记录任务号并等待终态 | 否 |
| 4xx 参数或权限错误 | 返回可行动错误，修正输入/权限 | 修正后再试 |
| 5xx 服务错误 | 标记服务不可用并保留请求 ID | 先确认服务状态 |
| 请求超时 | 标记为“结果待确认”，使用原请求编号或任务号查询 | 否 |
| 任务状态 `failed` | 映射厂商错误码和恢复建议 | 按错误类型决定 |

## 怎样确认接口可以交付

- 标准接口与 API 映射表完整，每个动作和属性都有来源；
- 已验证 2xx、4xx、5xx、无效 JSON、缺字段和错误类型；
- 异步任务只在终态完成后返回成功；
- 请求必须设置超时时间；自动重试前要确认不会让设备重复执行；
- 令牌、账号和证书未写入启动图（Graph JSON）或日志；
- 上位机重启、任务丢失和网络分区有明确恢复策略；
- 真机验收记录包含请求 ID、设备结果和标准动作返回值。

## 常见问题

| 现象 | 可能原因 | 建议处理 |
| --- | --- | --- |
| 返回 401/403 | Token 过期或权限不足 | 更新 Secret 并确认权限范围 |
| 返回 404 | API 路径或版本不一致 | 核对厂商文档和服务版本 |
| 返回 202 后一直运行 | 请求只是异步受理 | 用任务号轮询或订阅终态 |
| 超时后创建了重复任务 | 没有保存任务号或防重复请求编号 | 先查询厂商服务中的原任务，不要自动重新提交 |
| JSON 字段缺失 | 厂商版本变化或异常响应 | 校验响应 Schema 并记录版本 |
