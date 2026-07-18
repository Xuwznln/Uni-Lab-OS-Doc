# 验证检查清单: 本地工作流桥（两套前端直连 OS 本地 DAG 执行，替代 Go 后端）

> **Author: CLAUDE** | 所有项 PASS 才能标记需求完成
> 规则见 docs/agent-workflow.md（Build-Verify Loop + 正确性铁律）

## 1. 可导入 / 编译

- [ ] `python -c "import unilabos"` 通过
- [ ] 无循环 import / 语法错误（local_bridge 不反向污染 scheduler 执行核）

## 2. Lint / 类型

- [ ] `ruff check unilabos/app/local_bridge/ tests/` 全净
- [ ] 无新增 mypy 错误（N/A：仓库未统一启用 mypy）

## 3. 正确性 / hermetic

- [ ] OS 连接用内存 fake（收 task_dag、按脚本回 job_status），不起真实进程/连真实设备
- [ ] 离线执行核用仿真节点调度器（每设备锁保 I3），无真实 time.sleep
- [ ] 无 flaky（完成时刻手动驱动，确定性）

## 4. 测试

- [ ] `pytest tests/app/ tests/scheduler/` 通过（含桥的 hermetic 测试）
- [ ] 已有测试无回归（F002 tests/scheduler 全绿）
- [ ] 实现 B 自带 `npm run test`（local_ui workflowDraft/workflowExport）通过

## 5. 验收标准（对照 requirement.md 逐条）

- [ ] AC-1: 翻译核 UI 图 → F002 TaskDag，含环拒绝（复用 dag_model.from_message 校验）
- [ ] AC-2: schedule_ws 收发 task_dag/job_status，按 (task_id,node_id) 收敛，cancel 停后继
- [ ] AC-3: workflow_ws 下行 data.data.action 形状匹配 WorkflowDAGPanel（fetch_graph / workflow_update）
- [ ] AC-4: local_api /api/* 端点契约匹配 local_ui（含 1s 轮询 GET /api/run/{id}）
- [ ] AC-5: OS 面 task_dag/job_status 逐字段对齐 F002 §一（node_id==job_id，幂等键，无新造字段）
- [ ] AC-6: server.py 三面并起单 event loop，import 通过、不阻塞消息循环

## 6. 提交

- [ ] git add 相关文件（未 git add .，逐文件添加）
- [ ] git commit 中文描述性 message（每子任务一提交）
- [ ] feature-list.json 全部 completed（T01–T07 completed）
- [ ] progress.md 已更新

## 验证结果

| 项目 | 结果 |
|------|------|
| 可导入 | — |
| Lint/类型 | — |
| 正确性/hermetic | — |
| 测试 | — |
| 验收标准 | — |
| 代码评审 | — |

**总体结论**: 待实现
