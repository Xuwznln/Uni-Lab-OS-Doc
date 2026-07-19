# 验证检查清单: [FEATURE_NAME]

> **Author: CLAUDE** | 所有项 PASS 才能标记需求完成
> 规则见 docs/agent-workflow.md（Build-Verify Loop + 正确性铁律）

## 1. 可导入 / 编译

- [ ] `python -c "import unilabos"` 通过
- [ ] 无循环 import / 语法错误

## 2. Lint / 类型（如已引入）

- [ ] `ruff check <改动路径>` 无新增错误
- [ ] `mypy <关键模块>` 无新增错误（如适用）

## 3. 正确性 / hermetic

- [ ] 设备驱动测试用 fake transport，不连真实硬件
- [ ] 超时/调度逻辑注入可控时钟，无真实 sleep
- [ ] 无 flaky（同一输入必得同一结果）
- [ ] 协议编译/坐标/调度不变量有 Hypothesis 覆盖（如适用）

## 4. 测试

- [ ] 相关 `pytest tests/[领域]` 通过
- [ ] 已有测试无回归

## 5. 验收标准（对照 requirement.md 逐条）

- [ ] AC-1: ... 已验证
- [ ] AC-2: ... 已验证

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
