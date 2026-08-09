# 验证检查清单: OS 自动化测试与发布门禁流水线（Q3）

> **Author: CLAUDE** | 所有项 PASS 才能标记需求完成
> 规则见 docs/agent-workflow.md（Build-Verify Loop + 正确性铁律）

## 1. 可导入 / 基础

- [ ] `python -c "import unilabos"` 通过
- [ ] `pyproject.toml` 含 pytest + ruff 配置与 markers

## 2. 阶段 1 — 止血（AC-1 / AC-3）

- [ ] Linux CI job 跑 `pytest -m "not hardware and not slow"` 且现有测试全绿
- [ ] 主门禁不含"原生崩溃重试"式掩盖；Windows import 崩溃已隔离到 quarantine
- [ ] `test_simulation_meta / _ast / _decorator` 已恢复并在 CI 运行

## 3. 阶段 2 — 模拟锚点（AC-2 / AC-4 / AC-5）

- [ ] 每个注册设备的标准动作冒烟测试存在并断言状态转移（非仅 import）
- [ ] 模拟冒烟覆盖率看板产出，≥ 80% 注册设备；缺虚拟体设备被显式列出
- [ ] 26+ protocol 代表的 golden run 存在并进 Tier 2
- [ ] Hypothesis 守住：编译往返一致 / 坐标往返恒等 / 调度无资源冲突
- [ ] `tests/devices/` 有 fake-transport 样板；driver 的 transport/clock 可注入
- [ ] os-reviewer 的 hermetic 红线引用该样板
- [ ] SZLab 虚拟 OPC 三进程引擎已上收到 `unilabos/simulation/opcua/`，OPC-UA action 断言写入/等待/完成/异常（begin/goal/end），日志归档
- [ ] `nodes.csv` + `flow.json` 仿真契约格式标准化并留设备包；新 OPC-UA 设备只写 csv+flow 即复用引擎
- [ ] SZLab flake8 已并入内核统一 ruff（无两套 lint）

## 4. 阶段 3 — 交付门禁（AC-6 / AC-7 / AC-8 / AC-9）

- [ ] 契约测试覆盖 registry schema / device action 接口 / bridge，破坏即红
- [ ] conda 发布链前置 Tier 1 gate，不绿不发版
- [ ] semver 规则确立；`CHANGELOG.md` 从 Conventional Commits 生成
- [ ] 每个 release 带兼容性声明（领域开发者要不要改代码 + registry schema version）
- [ ] 启动 benchmark 守 3s 阈值，劣化即红
- [ ] "只换内核、不动前端"干跑演练通过（东方理工或等价场景）

## 5. 正确性 / hermetic

- [ ] 无测试连真实硬件（Tier 1）
- [ ] 无真实 sleep；超时/调度注入可控时钟
- [ ] 无 flaky（同一输入必得同一结果）；quarantine 通道单列

## 6. 交付平台

- [ ] Linux 在测试矩阵内（对齐苏州/宜宾部署目标）

## 7. 提交

- [ ] git add 相关文件（不要 git add .）
- [ ] git commit 中文描述性 message，链接 F001
- [ ] feature-list.json 全部 completed
- [ ] progress.md 已更新

## 验证结果

| 阶段 | 结果 |
|------|------|
| 阶段 1 止血 | PASS / FAIL |
| 阶段 2 模拟锚点 | PASS / FAIL |
| 阶段 3 交付门禁 | PASS / FAIL |
| 正确性/hermetic | PASS / FAIL |
| 交付平台 | PASS / FAIL |

**总体结论**: PASS / FAIL
