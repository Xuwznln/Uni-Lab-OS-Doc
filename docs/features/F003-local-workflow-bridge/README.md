# F003 本地工作流桥（两套前端直连 OS 本地 DAG 执行，替代 Go 后端）

> 基于 Harness Engineering 方法论，为 OS（Python/ROS2）定制。
> Agent 行为宪法: `docs/agent-workflow.md`
> 团队协作 SOP: 主仓 `product_designs/team_collaboration/`

## 一句话

在**不启动 Go 后端**（本环境 Redis/Nacos/MQTT/Docker 均不可用）的前提下，用一个
**本地桥（local_bridge）** 充当后端的"翻译面"，让两套前端都能把**整张工作流 DAG**
下发给 OS 本地执行（复用 F002 的 `task_dag` 真实路径），并接收 OS 回流的真实 `job_status`：

- **实现 A**：uni-lab-cloud 的两个工作流 panel（`WorkflowDAGPanel` + `WorkflowStepsPanel`）
  经桥的 `/ws/workflow/{uuid}` 面驱动，**panel 组件零改动**。
- **实现 B**：SZLab `unilabos_local_ui`（Vite + React 19 + React Flow）经桥的 `/api/*` 面驱动。

两套 UI 只是不同协议的翻译面，最终都走同一条 F002 `task_dag` 执行路径 —— 单一事实源，
不复制执行逻辑。

## 文件职责分工

| 文件 | 谁写 | 说明 |
|------|------|------|
| `requirement.md` | **HUMAN** | 需求规格：用户故事、验收标准 |
| `feature-list.json` | **HUMAN** 定义 / **CLAUDE** 改状态 | 子任务拆分与进度 |
| `interface-design.md` | **HUMAN** 定义 / **CLAUDE** 补充 | 桥三面接口 + 与 F002 契约对齐 |
| `progress.md` | **CLAUDE** | 实现进度记录 |
| `checklist.md` | **CLAUDE** | 验证检查清单 |

## 与 F002 的关系

F002 已把整张 DAG 的**本地执行**做完（`task_dag` 下行 → `DagExecutor`/`TaskDagRunner`
本地并发走图 → `job_status` 上行）。F003 只补**前端到 OS 之间的桥**这一段：把两套前端
各自的协议翻译成 F002 冻结的 `task_dag`/`job_status` 契约，不改 F002 任何执行逻辑。
