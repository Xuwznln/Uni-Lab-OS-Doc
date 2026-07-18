# 验证检查清单: OS 本地 DAG 执行器（整张工作流下沉边缘执行）

> **Author: CLAUDE** | 所有项 PASS 才能标记需求完成
> 规则见 docs/agent-workflow.md（Build-Verify Loop + 正确性铁律）

## 1. 可导入 / 编译

- [ ] `python -c "import unilabos"` 通过
- [ ] 无循环 import / 语法错误

## 2. Lint / 类型（如已引入）

- [ ] `ruff check unilabos/scheduler/` 无新增错误
- [ ] `mypy unilabos/scheduler/` 无新增错误（如适用）

## 3. 正确性 / hermetic

- [ ] 节点调度器用 fake 实现（可编程 status + 完成时刻），不连真实设备/send_goal
- [ ] 走图/超时逻辑注入可控时钟，无真实 time.sleep
- [ ] 无 flaky（同一输入必得同一结果）
- [ ] 调度不变量 I1–I6 有 Hypothesis 覆盖

## 4. 测试

- [ ] `pytest tests/scheduler/` 通过
- [ ] 已有测试无回归

## 5. 验收标准（对照 requirement.md 逐条）

- [ ] AC-1: 整张 DAG 本地并发走图（A 后 B/C 并发、B/C 后 D、各恰好一次）
- [ ] AC-2: 同设备自动串行（同 device_action_key 非 always_free 不重叠）
- [ ] AC-3: 断网中途不影响完成（重连后 job_status 补发）
- [ ] AC-4: 进程重启续跑且不重复
- [ ] AC-5: fail-fast 与环拒绝
- [ ] AC-6: 上行 job_status 契约逐字节一致（前端两 panel 零改动）

## 6. 提交

- [ ] git add 相关文件（不要 git add .）
- [ ] git commit 中文描述性 message
- [ ] feature-list.json 全部 completed
- [ ] progress.md 已更新

## 验证结果

| 项目 | 结果 |
|------|------|
| 可导入 | PASS / FAIL |
| Lint/类型 | PASS / FAIL / N/A |
| 正确性/hermetic | PASS / FAIL |
| 测试 | PASS / FAIL |
| 验收标准 | PASS / FAIL |

**总体结论**: PASS / FAIL
