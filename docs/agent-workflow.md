# Uni-Lab-OS — Agent 行为宪法（Agent Workflow）

> Claude 严格遵循此文件，不得修改。
> 这是 OS 端的行为宪法，对齐 backend 的 `CLAUDE.md`，但适配 Python / ROS2 的正确性重点。
>
> 代码规范见仓库 `AGENTS.md` / `docs/`。
> 团队级协作 SOP 见主仓 `product_designs/team_collaboration/`。
> 本文件定义 Agent **工作流程与行为约束**。

---

## 一、需求驱动原则

使用 `docs/features/FXXX-xxx/` 管理每个需求。**没有需求文件，不写代码。**

每个需求目录（从 `docs/templates/` 复制）包含：
- `requirement.md` — 人类编写的需求规格（只读）
- `feature-list.json` — 子任务清单（你只能改 status 和 notes）
- `interface-design.md` — 设备/协议/接口设计（只读）
- `progress.md` — 你来写的进度记录
- `checklist.md` — 你来写的验证清单

---

## 二、Session 启动协议

每个新 session 必须按序执行，不得跳过：

```
1. pwd                                             # 确认在 Uni-Lab-OS 根目录
2. git log --oneline -10                           # 了解最近进展
3. 找到当前需求目录                                # 人类告知，或从最近 commit 推断
4. cat docs/features/FXXX-xxx/progress.md          # 读进度
5. cat docs/features/FXXX-xxx/feature-list.json    # 读子任务
6. 选下一个 status="pending" 的子任务（按 id 顺序）
7. cat docs/features/FXXX-xxx/requirement.md       # 读需求
8. cat docs/features/FXXX-xxx/interface-design.md  # 读接口/协议设计
9. python -c "import unilabos"                      # 确认包能 import（当前 CI 的底线）
```

**不要凭"记忆"判断项目状态 — 你没有跨 session 记忆。**

---

## 三、单任务原则

**每次只实现 feature-list.json 中的一个子任务。** 完成并验证后 commit，再下一个。

禁止：同时改多个子任务、"顺手"重构无关代码、跳过验证。

---

## 四、Build-Verify Loop

每个子任务必须走完整循环，**仅 review 代码不算完成**：

```
Planning → Build → Verify → Fix（如需）→ Commit
```

### Verify（OS 端的绿色信号，按需求勾选）
- `python -c "import unilabos"` — 包可导入（底线，任何改动都要过）
- `pytest tests/<相关>` — 相关测试通过
- `ruff check <改动路径>` — 若已引入 ruff（见 §九）
- 对照 requirement.md 验收标准逐条确认

> OS 是"正确性重、逻辑复杂"的仓。协议编译 / 坐标数学 / 调度不变量这类逻辑，优先写 **property-based 测试（Hypothesis）** 而非只举几个例子——用性质约束覆盖输入空间。

---

## 五、正确性与 hermetic（OS 特有铁律）

loop 可信的前提是**测试不依赖真实硬件与墙钟**：

1. **隔离硬件**：设备驱动测试用 fake / mock transport，不连真实 OPC-UA / Modbus / RS485 / 串口。
2. **隔离墙钟**：涉及超时、调度、重试的逻辑，注入可控时钟，不 `time.sleep` 真实等待。
3. **确定性**：同一输入必得同一结果——随机/并发/DDS 时序不得泄漏进单测断言。
4. **协议编译 / 坐标变换 / 调度**：这些是数学，用 Hypothesis 写不变量（如"编译再反解 == 原输入"、"坐标往返变换 == 恒等"、"调度不产生资源冲突"）。

flaky 测试是毒药——人和 agent 都会学会无视红灯。发现 flaky：隔离状态或标记 quarantine，不要靠重跑掩盖。

---

## 六、提交纪律

每完成一个子任务**必须**：

1. `python -c "import unilabos"` 确认可导入
2. 相关 `pytest` 通过
3. `git add` 相关文件（**不要 `git add .`**）
4. `git commit -m "feat(领域): 描述"`，message 用中文
5. 更新 `feature-list.json` 中该任务 status 为 `"completed"`
6. 更新 `progress.md`

---

## 七、退出协议

Session 结束前**必须**：包可导入、提交所有已完成变更、更新 progress.md（完成了什么/遇到什么问题/下一步）、更新 feature-list.json 状态；未完成的保持 `"pending"`，不留 `"in_progress"`。

---

## 八、循环检测

同一文件编辑超 **5 次**仍未解决：停 → 重读 requirement.md 和 interface-design.md → 参考同领域已有驱动/协议实现 → 换思路 → 仍卡住则在 progress.md 记录、标 `"blocked"`、通知人类。**不要死循环。**

---

## 九、测试基建缺口（现状 → 目标）

现状：CI 只做 registry-check + import 验证，`pyproject.toml` 无 pytest/ruff/mypy/hypothesis 配置。目标（由 Harness DRI 逐步补齐，不阻塞当前功能）：

- 引入 `pytest` 为默认测试入口，`tests/` 下按领域分目录。
- 引入 `ruff`（lint+format）与 `mypy`（关键模块类型检查）。
- 引入 `hypothesis` 覆盖协议编译 / 坐标 / 调度不变量。
- CI 打开 pytest 门禁（先 `--co` 收集不报错，再逐步开断言门禁）。

在这些就位前，绿色信号至少是"import 通过 + 已写的 pytest 通过"。

---

## 约束即自由

结构化约束越明确，你在约束内的执行自由度越高，loop 转得越快。
