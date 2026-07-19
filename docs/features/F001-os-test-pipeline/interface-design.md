# 接口 / 协议 / 设备设计: OS 自动化测试与发布门禁流水线

> **Author: HUMAN 定义 / CLAUDE 可补充示例**
> Claude 实现时严格遵循，不自行发挥。

## 设计类型

- [x] 调度/后端逻辑（测试与发布基建）
- [x] 协议编译（golden run + property）
- [x] 新设备驱动（fake-transport 样板）

---

## 1. 测试分层（三 Tier）

| Tier | 触发 | 内容 | 平台 | 门禁 |
|------|------|------|------|------|
| **0** | 本地 pre-commit | ruff format+lint、改动文件单测 | 本地 | 本地 |
| **1** | 每次 PR | `pytest -m "not hardware and not slow"`：全单测 + 模拟标准动作冒烟 + property 不变量 | **Linux**（+ Win registry-check 并行） | **必须绿、确定性、不重试** |
| **2** | 夜跑 / 发版前 | protocol golden run + 契约测试 + 启动 benchmark + `@hardware` HIL | Linux + Win（+ osx） | 发版门禁 |

### pytest markers（T1.1 约定）

```
markers =
    hardware: 需要真实硬件，默认排除（仅 Tier 2 / 手动）
    slow: 慢测试（golden run / benchmark），默认排除
    property: Hypothesis 不变量测试
    simulation: 模拟驱动功能级测试
    contract: 内核↔应用契约测试
```

运行约定：
- Tier 1：`pytest -m "not hardware and not slow"`
- Tier 2：`pytest -m "slow or property or contract" --benchmark-only`（分步）

### tests/ 目标结构

```
tests/
├── devices/        # T2.4 hermetic driver 单测（fake transport）
├── simulation/     # T2.1/T2.2 标准动作冒烟 + protocol golden run
├── property/       # T2.3 Hypothesis 不变量
├── contract/       # T3.1 registry schema / action / bridge 契约
├── benchmark/      # T3.4 启动性能
├── registry/       # 现有 + 恢复 test_simulation_meta*
└── (现有 resources/workflow/integration 保留)
```

---

## 2. 模拟驱动功能级校验（T2.1，锚点）

标准动作冒烟的断言模型（伪代码，Claude 按现有 virtual 设备接口落地）：

```python
@pytest.mark.simulation
def test_device_standard_actions_smoke(registered_device):
    sim = build_virtual_counterpart(registered_device)   # 从注册表取虚拟对应体
    for action in registered_device.standard_actions:
        before = sim.state_snapshot()
        sim.execute(action, **sample_args(action))        # 无真机、无真实 sleep
        after = sim.state_snapshot()
        assert state_transition_valid(action, before, after)   # 断言功能，而不仅是 import
```

覆盖率看板：`通过冒烟的设备数 / 注册设备总数`，输出为 CI artifact（json + 简单 md 表），目标 ≥ 80%。缺虚拟体的设备**显式列出**，不静默跳过。

---

## 3. Protocol 黄金运行（T2.2）

```python
@pytest.mark.simulation
@pytest.mark.slow
@pytest.mark.parametrize("protocol", representative_protocols())   # 26+ 类型各取代表
def test_protocol_golden_run(protocol):
    compiled = compile_protocol(protocol.yaml)
    result = run_against_virtual_devices(compiled)     # 打到虚拟设备
    assert result == load_golden(protocol.name)        # 与冻结的黄金产出比对
```

golden 文件纳入版本管理；变更需人工确认（防止把 bug 固化成 golden）。

---

## 4. property-based 不变量（T2.3）

```python
from hypothesis import given, strategies as st

@given(p=protocol_strategy())
def test_compile_roundtrip(p):
    assert decompile(compile_protocol(p)) semantically_equals p

@given(pose=pose_strategy())
def test_coordinate_roundtrip(pose):
    assert to_local(to_absolute(pose)) ≈ pose           # 往返恒等（容差）

@given(sched=schedule_strategy())
def test_no_resource_conflict(sched):
    plan = schedule(sched)
    assert no_two_tasks_share_resource_at_same_time(plan)
```

---

## 5. driver fake-transport 样板（T2.4）

关键 seam —— driver 必须依赖**可注入 transport / clock 接口**，而非直接 `serial.Serial(...)` / 硬编码 client：

```python
class SomeDriver:
    def __init__(self, transport: Transport, clock: Clock = SystemClock()):
        self._t, self._clock = transport, clock          # 注入点

# 测试
def test_driver_command_encoding():
    fake = FakeTransport(scripted_responses=[...])
    drv = SomeDriver(fake, clock=ManualClock())
    drv.move(x=10)
    assert fake.sent == [expected_frame]                 # 断编解码，不连真机
```

产出：一个可复制模板 + os-reviewer 的 hermetic 红线引用它。

---

## 6. 契约测试（T3.1，内核独立性验证器）

冻结公共面并断言未破坏：

```python
@pytest.mark.contract
def test_registry_schema_is_backward_compatible():
    assert registry_schema_version() >= FROZEN_MIN
    assert public_fields(current_schema) ⊇ public_fields(frozen_schema)   # 只增不减

@pytest.mark.contract
def test_device_action_interface_stable():
    assert public_action_signatures() ⊇ frozen_action_signatures()

@pytest.mark.contract
def test_bridge_message_contract():
    # WebSocket + FastAPI bridge 的消息 schema 不破坏
    ...
```

契约破坏走 expand-contract：先加新面（兼容）→ 迁移消费方 → 下一版本才移除旧面。这是"东方理工不改前端换内核"的机器化保证。

---

## 7. 发布门禁与版本（T3.2 / T3.3）

- conda 发布链（现有）前置一步：**Tier 1 绿灯检查**，不绿不进 Multi-Platform Build。
- 版本：semver（`MAJOR.MINOR.PATCH`），`setup.py` 单一来源 + tag `v*` 对齐。
- `CHANGELOG.md`：从 Conventional Commits 生成（feat/fix/breaking）。
- 兼容性声明：每版一段"领域开发者影响"（要不要改代码 / registry schema version 是否变）。

---

## 测试策略小结

- fake/mock 点：transport、clock、真实设备、ROS2 运行时。
- Hypothesis 覆盖：protocol 编译、坐标、调度。
- 不进 Tier 1：HIL、golden run、benchmark（都在 Tier 2）。

---

## 8. OPC-UA/PLC 设备类：虚拟三进程 action 回归（上收自 SZLab）

SZLab 已实战验证的三进程链路，上收为内核标准夹具。**按核心/资产解耦拆两半**：

```
内核（unilabos/simulation/opcua/，可复用引擎）        设备包（随设备发布，资产）
├── virtual_server.py   按 csv 起虚拟 OPC-UA 服务      szlab_poly_studio/
├── state_daemon.py     按 flow 推进 PLC 状态           ├── decap_s08_nodes.csv   # 变量表
├── manifest_runner.py  统一启动三进程 + 日志归档        ├── decap_s08_flow.json   # 状态规则
└── conftest fixtures   pytest 夹具                     └── szlab_plc_*.csv       # PLC 点表
```

### 三进程链路

```
代码提交
   │
   ▼ 容器环境安装依赖
┌────────────────────┐   写入/等待    ┌─────────────────────┐
│ ① OPC-UA 虚拟服务   │◀──────────────│ ③ action 测试进程    │
│   按 *_nodes.csv    │──────────────▶│   pytest 调真实 action│
│   暴露 PLC 变量      │   变量当前值   │   校验 写/等/完/异常  │
└─────────┬──────────┘                └──────────▲──────────┘
          │ 变量变化                              │ 完成信号
          ▼                                       │
┌────────────────────┐                            │
│ ② 状态守护进程       │────────────────────────────┘
│   按 *_flow.json     │   模拟 PLC 状态推进、推完成信号
└────────────────────┘
   在不触碰真实实验 OPC 的前提下，完成通信握手、状态等待、结果判断回归
```

### action 断言模型（变量握手 = 最易漏测处）

```python
@pytest.mark.simulation
@pytest.mark.opcua
def test_opcua_action_handshake(opcua_manifest):   # fixture 起①②③
    dev = opcua_manifest.device("decap_s08")
    var = opcua_manifest.variables

    begin = var.snapshot()
    dev.execute("open_cap")                 # ③ 调真实 action，写 PLC 变量
    # ② 守护进程按 flow 把 goal 变量推进到 done
    opcua_manifest.wait_until(var.done_signal("open_cap"), timeout=ManualClock)

    end = var.snapshot()
    assert transition_ok(begin, end, expected=load_flow("decap_s08"))   # begin/goal/end
    assert dev.last_result.ok and not dev.last_result.exception
    # 失败时：manifest 已归档 ①② 日志 + 变量变化表，供定位
```

### manifest 格式（统一启动，编排即数据）

```yaml
# 每个 OPC-UA 设备/工位一个 manifest；引擎通用，内容是资产
device: decap_s08
nodes_csv: decap_s08_nodes.csv        # ① 暴露哪些变量
flow_json: decap_s08_flow.json        # ② 状态推进规则
actions_under_test: [open_cap, close_cap]
log_archive: [virtual_server, state_daemon]   # 失败定位
```

### 复用契约（AC-11）

新 OPC-UA 设备接入测试 = **只写 `*_nodes.csv` + `*_flow.json` + manifest**，复用内核 `unilabos/simulation/opcua/` 引擎，不重写 CI。这条是"横向联动架构统一"的机器化保证：别的 PLC 线（东方理工/其它光刻胶线）直接套同一引擎。

> 关联但**不在本 spec 内**：SZLab 的 `unilabos_local_ui` 本地联调工作台用的是同一套 begin/goal/end 变量数据模型——CI 断言与本地调试应共用 `nodes.csv`/`flow.json` 契约，避免各造一半（边界划分见 `product_designs/ai_native_org/苏州光刻胶设备包现状与协作.md`）。

