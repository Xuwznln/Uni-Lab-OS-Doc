# 验证检查清单: OS 本地 DAG 执行器（整张工作流下沉边缘执行）

> **Author: CLAUDE** | 所有项 PASS 才能标记需求完成
> 规则见 docs/agent-workflow.md（Build-Verify Loop + 正确性铁律）

## 1. 可导入 / 编译

- [x] `python -c "import unilabos"` 通过
- [x] 无循环 import / 语法错误（scheduler 不反向依赖 ws_client）

## 2. Lint / 类型（如已引入）

- [x] `ruff check unilabos/scheduler/ tests/scheduler/` 全净；`ws_client.py` 仅 1 处既有无关 F541（HEAD 已存在，非本需求引入）
- [ ] `mypy unilabos/scheduler/` 无新增错误（N/A：仓库未统一启用 mypy）

## 3. 正确性 / hermetic

- [x] 节点调度器用 fake 实现（fake_scheduler.py / FakeStack），不连真实设备/send_goal
- [x] 走图/超时逻辑注入可控时钟，无真实 time.sleep（settle() 用 asyncio.sleep(0) 零墙钟推进）
- [x] 无 flaky（手动 complete()/finish() 驱动终态，确定性）
- [x] 调度不变量 I1–I6 有 Hypothesis 覆盖（test_dag_invariants.py，max_examples=200）

## 4. 测试

- [x] `pytest tests/scheduler/ tests/app/` 通过（19 passed：15 scheduler + 4 T07 死路径回归）
- [x] 已有测试无回归（T04/T05 全绿，dag_executor CANCELLED 修正未改 fail-fast 语义）
- [x] T07 评审 HIGH 悬挂缺陷已整改并加回归（job_start 死路径必回终态，见 progress.md）

## 5. 验收标准（对照 requirement.md 逐条）

- [x] AC-1: 整张 DAG 本地并发走图 — test_runner_diamond_concurrent_on_callback_stack + test_dag_executor AC-1（A 后 B/C 并发、B/C 后 D、各恰好一次）
- [x] AC-2: 同设备自动串行 — test_runner_same_device_serialized + fake max_concurrent_by_key（同 device_action_key 非 always_free 不重叠）
- [x] AC-3: 断网中途不影响完成 — dag_executor._notify_terminal try/except（上行失败不打断走图）+ ws publish_job_status 断线缓存/重连补发（既有，未改）
- [x] AC-4: 进程重启续跑且不重复 — dag_persistence 游标 + (task_id,node_id) 幂等缓存 + test_dag_invariants I4
- [x] AC-5: fail-fast 与环拒绝 — test_runner_fail_fast_triggers_cancel_remaining + test_dag_executor AC-5a/AC-5b（解析期 DagValidationError）
- [x] AC-6: 上行 job_status 契约逐字节一致 — _start_dag_node 复用 _handle_job_start → publish_job_status 载荷未改（仅新增 no-op notify 钩子），对照 interface-design §1.2 逐字段核验；前端两 panel 零改动

## 6. 提交

- [x] git add 相关文件（未 git add .，逐文件添加）
- [x] git commit 中文描述性 message（每子任务一提交）
- [x] feature-list.json 全部 completed（T01–T08 completed）
- [x] progress.md 已更新

## 验证结果

| 项目 | 结果 |
|------|------|
| 可导入 | PASS |
| Lint/类型 | PASS（既有无关 F541 除外；mypy N/A） |
| 正确性/hermetic | PASS |
| 测试 | PASS（19 passed：15 scheduler + 4 死路径回归） |
| 验收标准 | PASS（AC-1~AC-6 逐条对照） |
| 代码评审 | PASS（python-reviewer；HIGH 悬挂缺陷已整改 + 回归覆盖，见 progress.md） |

**总体结论**: PASS（契约以 interface-design.md 冻结，实现逐字段对齐后端；评审 HIGH 项已闭环；跨仓 T08 为对端清单，已在 interface-design.md §三 冻结）

