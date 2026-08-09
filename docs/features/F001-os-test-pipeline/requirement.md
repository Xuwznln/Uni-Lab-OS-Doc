# 需求规格: OS 自动化测试与发布门禁流水线（Q3）

> **Author: HUMAN** | Claude 只读，按此实现，不修改。
> 工作流程见 docs/agent-workflow.md，代码规范见根目录 AGENTS.md

## 背景

Q3（今 2026-07-12 → 09 月底，约 11 周）是唯一窗口：10 月大规模联调、年底交付干湿闭环。
若 Q3 不把内核定位落到工程实践，10 月联调将是灾难。三条军令：

1. **内核可独立升级/验证/交付** —— 东方理工不改前端能否直接换内核，是解耦是否真实的判据。
2. **模拟驱动成为标准测试流程** —— 高京的虚拟设备（`unilabos/devices/virtual/`）已提供模拟能力，须从"能 import"升级为"能断言功能"。
3. **产品化运转** —— 版本号、变更日志、兼容性声明、发布门禁像正经软件产品一样维护。

### 现状实测（2026-07-12）

- 设备：165 个 device `.py` / ~40 类；`devices/virtual/` 有 17 个虚拟设备。
- 测试：18 个测试文件（registry 9 / resources 3 / workflow / integration）。
- CI 门禁 `ci-check.yml`：**仅 Windows**，跑 `--check_mode`（全量 import + AST 注册表校验 + 无未提交变更），**不跑 pytest**，且靠 4 次重试掩盖 Windows 原生崩溃（0xC0000005）。
- 模拟校验：`--check_mode` 只到 import，**没有"跑真实 protocol 打到虚拟设备并断言结果"的功能级校验**。
- `test_simulation_meta*.py` **已被删除**，仅剩 2026-06-25 的 stale `.pyc` —— 高京那套校验的测试代码是孤儿。
- 发布：成熟 conda 链（CI Check → Multi-Platform msgs → UniLabOS Conda → Conda-Pack → Anaconda.org），但**只 gate registry-check，不以测试为门禁**。
- 版本：`setup.py` version=`0.11.3`，手动 bump，无 CHANGELOG、无兼容性声明。
- 目标交付平台是 Linux（苏州 DockerCompose、宜宾 DataCore），**而测试只在 Windows 跑**。

### SZLab 已建成的可上收资产（styxhuang fork，苏州光刻胶线）

SZLab 直接 fork 主仓、把 OPC-UA/PLC 设备层做深（`unilabos/devices/workstation/szlab_poly_studio/`，79 文件），并**已建成一套虚拟 OPC-UA 三进程 action 回归 CI**——这正是本 spec T2 要的东西的实战实现：

- **OPC-UA 虚拟服务进程**：按 `*_nodes.csv` 暴露虚拟 PLC 变量，CI 专用通信入口（= 协议级仿真器）。
- **状态守护进程**：按 `*_flow.json` 监听变量变化、模拟 PLC 状态推进、推完成信号（= 设备行为模型 / 真正的"仿真机"）。
- **action 测试进程**：pytest 调真实设备 action，校验**写入/等待/完成信号/异常**（= 补上"变量握手容易漏测"这个洞）。
- 外加：容器化环境、flake8 + 全量 pytest、SZLab 专项测试、OPC-UA 集成 manifest（统一启动三进程）、虚拟服务+守护进程日志归档（失败定位）。
- 契约数据形态：每工位 `*_nodes.csv`（变量表）+ `*_flow.json`（状态规则）+ PLC 点表 `szlab_plc_*.csv`。
- fork 状态：对上游 dev 领先 239 / 落后 30，`szlab_poly_studio` + CI/SOP 待上收为核心标准。

**结论**：本 spec 不"新建"OPC-UA 测试，而是**上收 SZLab 三进程 CI 为内核标准的 OPC-UA/PLC 设备类 action 回归层**，并按"核心与资产解耦"拆成——仿真引擎进内核、`nodes.csv`+`flow.json` 契约数据留设备包——让别的 PLC 设备线复用同一引擎，不再各造一套 CI。这是 Q3"横向联动架构层面统一"的落点。

## 用户故事

```
As a 核心开发者,
I want to 让内核的每次变更都有可验证的、以模拟驱动为标准的测试覆盖，并把测试设为发布门禁,
So that 内核可以被独立升级、独立验证、独立交付，10 倍规模的实验室建设不需要人肉救火.
```

```
As a 领域开发者（东方理工/宜宾/苏州）,
I want to 拿到带兼容性声明的内核版本，知道"升级到这个版本我要不要改代码",并能不改前端直接换内核,
So that 我的场景定制不被内核升级打断.
```

## 详细描述

把 OS 测试从"仅 Windows 全量 import + 重试掩盖"升级为**分层流水线**，并把它接到发布门禁与版本纪律上：

- Tier 0（秒级，本地）：format/lint + 改动文件单测。
- Tier 1（PR 门禁，分钟级，**Linux**）：全单测 + 模拟驱动功能冒烟 + property 不变量；必须确定性，红即红，不靠重试。
- Tier 2（夜跑/发版）：protocol golden run + 契约测试 + 启动性能基准 + 硬件 `@hardware`。

核心是把**虚拟设备**升级成**功能级标准校验**，并新增**契约测试**证明内核↔应用解耦，最后把测试门禁化到 conda 发布链，配 semver + CHANGELOG + 兼容性声明。

## 验收标准（Given/When/Then，Claude 逐条验证）

### AC-1: 已有测试进门禁
```
Given 仓库现有 18 个测试文件,
When 打开一个 PR,
Then Linux 上 `pytest -m "not hardware and not slow"` 作为门禁运行且全绿，不依赖任何重试。
```

### AC-2: 模拟驱动功能级校验
```
Given 一个注册设备及其虚拟对应体,
When 运行标准动作冒烟测试,
Then 断言其 standard actions 的状态转移正确；模拟冒烟覆盖率（有虚拟体且通过冒烟的设备 / 注册设备）出具看板，目标 ≥ 80%。
```

### AC-3: 恢复模拟校验测试
```
Given test_simulation_meta*.py 曾被删除,
When 阶段 1 完成,
Then 该套测试重新纳入版本管理并在 CI 中运行。
```

### AC-4: property-based 不变量
```
Given protocol 编译 / 坐标变换 / 调度是纯函数逻辑,
When 运行 Hypothesis 测试,
Then 断言：编译往返一致、坐标往返恒等、调度不产生资源冲突。
```

### AC-5: driver hermetic 样板
```
Given 一个真实 driver（Modbus/串口类）,
When 切出可注入 transport 并写单测,
Then 测试不连真实硬件、无真实 sleep，成为 tests/devices/ 的复制模板，并被 os-reviewer 强制。
```

### AC-6: 契约测试（内核独立性）
```
Given 内核↔应用的公共面（registry schema / device action 接口 / WebSocket+FastAPI bridge）,
When 内核变更,
Then 契约测试断言公共契约未破坏；破坏时红灯。
```

### AC-7: 测试门禁化发布 + 版本纪律
```
Given conda 发布链,
When 发起一次发布,
Then Tier 1 不绿不发版；每个 release 带 semver、从 Conventional Commits 生成的 CHANGELOG、兼容性声明（领域开发者要不要改代码）。
```

### AC-8: 启动性能基准
```
Given 启动从 10-20s 优化到 3s 的目标,
When 运行启动 benchmark,
Then 超过阈值即红，防止优化悄悄劣化。
```

### AC-9: 换内核演练
```
Given 一个真实场景（东方理工或等价）,
When 9 月底做干跑验收,
Then 实跑一次"只换内核、不动前端"成功，作为 10 月联调前的验收。
```

### AC-10: OPC-UA action 握手回归（上收 SZLab 三进程）
```
Given 一个 OPC-UA/PLC 设备及其 nodes.csv + flow.json 契约,
When CI 启动"虚拟 OPC 服务 + 状态守护 + action 测试"三进程 manifest,
Then 在不连真实实验 OPC 的前提下，断言 action 的写入→等待→完成信号→异常处理正确（变量 begin/goal/end 达到预期），并归档虚拟服务与守护进程日志。
```

### AC-11: 仿真引擎与契约解耦、可复用
```
Given SZLab 的三进程 CI,
When 上收进内核,
Then 仿真引擎（虚拟 OPC 服务 + 状态守护 + manifest runner）进内核成为标准夹具；nodes.csv + flow.json 契约格式标准化并留在各设备包；另一个 OPC-UA 设备只写自己的 csv + flow 即可复用同一引擎，无需重写 CI。
```

## 涉及模块

- **CI**: `.github/workflows/`（新增 Linux pytest job；发布链加测试门禁）
- **测试**: `tests/`（新增 `tests/devices/`、`tests/simulation/`、`tests/contract/`、`tests/property/`、`tests/benchmark/`）
- **模拟**: `unilabos/devices/virtual/`、`--check_mode` / `--test_mode`（`unilabos/app/main.py`）、恢复 `tests/registry/test_simulation_meta*.py`
- **OPC-UA 仿真引擎（上收）**: 新增 `unilabos/simulation/opcua/`（虚拟 OPC 服务 + 状态守护 + manifest runner，源自 SZLab）；契约数据 `*_nodes.csv` + `*_flow.json` 留 `unilabos/devices/workstation/szlab_poly_studio/` 等设备包
- **通信适配**: `unilabos/device_comms/`（OPC-UA client / Modbus PLC —— action 握手回归的被测面）
- **注册表契约**: `unilabos/registry/registry.py`（schema version）
- **协议编译**: `unilabos/compile/`（golden run + property）
- **打包/版本**: `setup.py`、`.github/workflows/*conda*`、新增 `CHANGELOG.md`
- **lint 收敛**: SZLab 的 flake8 配置并入内核统一的 ruff（见 T1.1）

## 正确性关注点（OS 特有）

- protocol 编译往返一致、坐标往返恒等、调度无资源冲突 —— property-based（Hypothesis），不需仿真机。
- driver 测试须 fake transport + 可控时钟，禁连真机、禁真实 sleep（hermetic）。
- 交付平台 Linux 必须在测试矩阵内。

## 依赖关系

- 前置：`product_designs/square_devices_and_labs/05_virtual_standard_actions_plan.md`（虚拟标准动作）、`08_simulation_pair_registry_phase1b_plan.md`（模拟配对注册）。
- 外部依赖（须 fake）：真实 OPC-UA / Modbus / RS485 / 串口设备、ROS2 运行时。

## 验证方法

- [ ] Linux `pytest -m "not hardware and not slow"` 门禁全绿，无重试
- [ ] 模拟冒烟覆盖率看板 ≥ 80% 注册设备
- [ ] Hypothesis 守住三类不变量
- [ ] `tests/devices/` 有 fake-transport 样板并被 os-reviewer 强制
- [ ] 契约测试覆盖 registry schema / action 接口 / bridge
- [ ] conda 发布不过 Tier 1 不放行；release 带 CHANGELOG + 兼容性声明
- [ ] 启动 benchmark 守 3s 阈值
- [ ] 换内核干跑演练通过

## 不做什么（Out of Scope）

- 不追求 100% 行覆盖率（用 diff-coverage 只卡改动行）。
- 不在 Tier 1 引入真机 HIL（放 Tier 2 夜跑/发版）。
- 不重写现有 conda 发布链，只在其前面加测试门禁。
- 不做前端/backend 的测试体系（本 spec 仅 OS）。
