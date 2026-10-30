# 串口／网络连接

:::{admonition} 阅读角色
- **业务负责人**：确认设备用途、动作名称、参数单位、成功标准和安全边界。
- **开发人员**：实现设备合同、连接方式、启动图节点、模拟能力和测试。
- **验收人员**：验证参数越界、断线、超时、联锁、未知结果和真机恢复。
:::

本页适用于通过串口线、TCP 网络或 Modbus 控制的设备。业务人员只需要确认页面上要提供哪些操作和状态；通信指令、报文校验和重连逻辑由开发人员封装在设备驱动中，不暴露给工作流。

串口、TCP、UDP、Modbus 等连接统一归为“直连驱动”：传输方式可以不同，但
工作流中看到的动作和状态必须与所选设备类别一致。本章以蠕动泵为例；代码中的通信报文只是填写位置，实际使用时必须替换为设备正式手册中的命令。

:::{admonition} 本页完成条件
交付标准设备类、直连驱动、启动图（Graph JSON）连接配置和协议测试；已验证分帧、校验、超时、断线、错误响应和重复发送边界。
:::

```{warning}
不存在能直接连接任意品牌设备的通用串口/TCP 命令。波特率、帧格式、校验、寄存器和响应含义必须来自设备手册或现场抓包验证。
```

## 需要填写哪些连接信息

| 配置 | 串口 | TCP/网络 | 要求 |
| --- | --- | --- | --- |
| 地址 | `port` | `host`、`port` | 属于实例配置，不写死在标准类 |
| 链路参数 | 波特率、数据位、校验、停止位 | 连接/读取超时、TLS | 明确默认值和允许范围 |
| 帧边界 | 终止符、固定长度或长度字段 | 长度字段或协议消息 | 禁止用一次 `read()` 猜完整响应 |
| 完整性 | 校验和/CRC | 协议校验、TLS | 校验失败不得报告成功 |
| 重试 | 有限次数 | 有限次数 | 只重试可安全重复的请求 |
| 身份确认 | 型号/序列号查询 | 握手或版本接口 | 连接后确认不是错误设备 |

接入前需准备设备型号、固件版本、正式协议、连接地址、命令与响应样例、单位、错误码、超时、
重复发送的后果及可用模拟器。密码、Token 和证书私钥通过 Secret 或环境变量注入，不得写入
启动图、源码或截图。

## 从设备手册到可用动作

1. 从当前 Workspace Catalog 选择设备大类和接口。
2. 为具体型号分配唯一 `@device` ID，分类路径保持与标准类一致。
3. 在 `connect()` 中建立连接、设置超时并核对设备身份。
4. 把每个标准动作映射为命令编码、发送、响应解析和错误转换。
5. 把状态属性映射为查询结果或带有效期的可靠缓存。
6. 在驱动边界校验范围、单位、枚举和设备返回值。
7. 在 `disconnect()` 中安全关闭连接并清理后台读取任务。

参数约束建议直接写入构造函数：

| 参数 | 类型 | 串口约束 | 网络约束 |
| --- | --- | --- | --- |
| `port` | `str` / `int` | 非空设备路径 | 1-65535 端口号 |
| `host` | `str` | 不适用 | 主机名/IP，不包含协议前缀 |
| `baudrate` | `int` | 必须属于设备支持集合 | 不适用 |
| `timeout` | `float` | 秒，必须大于 0 | 连接/读取分别设置更佳 |
| `address` | `int` | RS-485/Modbus 从站地址 | Modbus TCP 单元号按协议确认 |
| `auto_connect` | `bool` | 初次接入为 `false` | 初次接入为 `false` |

## 串口设备代码模板

```python
from typing import Any, Dict

from unilabos.registry.decorators import action, device, topic_config


@device(
    id="vendor_model_peristaltic_pump_serial",
    category=["合成制备仪器与设备", "实验泵", "蠕动泵"],
    description="某型号蠕动泵串口驱动",
    displayname="蠕动泵（串口）",
)
class VendorModelPeristalticPumpSerial:
    def __init__(self, port: str, baudrate: int = 9600,
                 timeout: float = 2.0, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._transport = None
        self._status = "offline"
        self._fault = False

    def connect(self) -> None:
        self._transport = self._open_serial()  # 使用项目选定的串口库
        identity = self._request("IDENTIFY")
        if not self._is_expected_device(identity):
            self.disconnect()
            raise ConnectionError("串口设备身份不匹配")
        self._status = "idle"

    @action(description="设置转动速度")
    def set_speed(self, speed: float = 0.0) -> Dict[str, Any]:
        if not 0.0 <= speed <= self._max_speed():
            raise ValueError("speed 超出设备允许范围")
        response = self._request(self._encode_set_speed(speed))
        self._require_ack(response, action="set_speed")
        return {"success": True, "message": "转速设置成功", "speed": speed}

    @action(description="正转")
    def rotate_forward(self) -> Dict[str, Any]:
        response = self._request(self._encode_rotate_forward())
        self._require_ack(response, action="rotate_forward")
        return {"success": True, "message": "正转已启动"}

    @property
    @topic_config()
    def status(self) -> str:
        return self._status

    @property
    @topic_config()
    def fault(self) -> bool:
        return self._fault
```

`_open_serial()`、命令编码和响应解析故意留为协议边界；必须依据设备协议实现并用录制报文/模拟器测试，不能从模板猜测。

## 把现场设备加入启动清单

```json
{
  "id": "pump_serial_01",
  "name": "串口蠕动泵 01",
  "children": [],
  "parent": null,
  "type": "device",
  "class": "community.my_device_package.my_lab_pump_serial",
  "position": {"x": 0.0, "y": 0.0, "z": 0.0},
  "config": {
    "port": "/dev/tty.usbserial-0001",
    "baudrate": 9600,
    "timeout": 2.0,
    "auto_connect": false
  },
  "data": {"status": "offline", "fault": false}
}
```

启动图 的 `config` 键必须与构造函数参数一致。TCP 设备将 `port` 改为数值端口，并增加 `host`；Modbus 设备再增加从站/单元地址、字节序和缩放配置。

## 使用 TCP 或 Modbus 时怎么调整

TCP 实现应复用相同的 `set_speed()`、`rotate_forward()`、`status` 和 `fault`，只替换传输层。
必须处理半包、粘包、连接中断和服务端关闭；如果使用 Modbus，还要记录功能码、寄存器类型、
字节序、缩放和读写权限。

```python
def _request(self, payload: bytes) -> bytes:
    self._socket.sendall(self._frame(payload))
    header = self._recv_exact(self.HEADER_SIZE)
    body_length = self._parse_length(header)
    body = self._recv_exact(body_length)
    return self._validate_frame(header + body)
```

## 怎样确认设备可以交付

- 动作与属性接口通过标准 CSV/代码一致性检查；
- 已验证错误端口、错误波特率、错误 IP、拒绝连接和中途断线；
- 已验证半包、粘包、CRC/校验失败、乱码和未知响应；
- 只有查询类操作或已经确认重复发送不会导致重复动作的命令才可以自动重试；
- 状态缓存有明确有效期，故障状态不会被默认值掩盖；
- 模拟器验证与真机验证分开记录。

## 常见问题

| 现象 | 可能原因 | 建议处理 |
| --- | --- | --- |
| 找不到串口 | 端口名变化、权限不足或线缆未接 | 在部署主机确认端口，再注入实例配置 |
| 能连接但没有响应 | 波特率、校验位、地址或结束符不匹配 | 对照设备手册和抓包逐项核对 |
| 返回乱码 | 编码、帧长度或边界错误 | 保留原始字节，先修复解码和分帧 |
| TCP 偶发解析错误 | 没有处理半包和粘包 | 按长度字段或协议边界使用 `_recv_exact` |
| 请求超时后重复动作 | 设备已执行但响应丢失 | 先查状态或任务号，不直接重放 |
