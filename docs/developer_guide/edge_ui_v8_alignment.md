# Edge UI v8 对齐记录

## 当前边界

UniLabOS 只保留一套微后端数据面和一套调度语义：

| 能力 | 唯一实现 | 说明 |
| --- | --- | --- |
| Workflow 定义、Graph、Task、Node Job | `workflow.db` + `WorkflowService`（`unilabos/server/services/workflow/`） | 本地写入口默认挂载；接入云端（Backend-controlled）后不挂载 |
| DAG、动作锁、物料锁、库存准入 | `unilabos/server/backend/scheduler/` | 同一轮调度使用一个完整资源申请，不存在第二套锁队列 |
| 执行与 Backend 命令协调 | `unilabos/server/backend/` | 接入云端后 Host 只执行 Backend 已准入的 Job |
| Material、Site、库存预留与账本 | `materials.db` + `MaterialsService` | 不再有独立 Inventory 数据库或 Provider |
| 设备状态 | `telemetry.db` + `TelemetryService` | latest 与 append-only event 使用既有表 |
| Job 生命周期与可靠收发 | `runtime.db` + `RuntimeService` | Backend 命令先持久化再执行 |
| 结果、反馈、错误与人工替换 | `history.db` + `HistoryService` | 不再维护平行的 Scheduler history |
| HostLink 网络 | `unilabos/backend/hostlink/` | 与调度实现解耦 |

完整 Host API 默认使用 `:8002`。独立 Scheduler Provider、独立 Inventory API、
进程内 Monitor SSE 和旧形状 Workflow 执行入口均已移除，不再提供兼容路由。

## UI 应使用的接口

默认 Host 数据面：

- `/api/v1/runtime`：endpoint、命令、execution job、可靠 outbox；
- `/api/v1/materials`：模板、Material、Site、lot、reservation 和 ledger；
- `/api/v1/telemetry`：设备最新状态和事件；
- `/api/v1/history`：payload 与统一历史事件；
- `/api/v1/health`、`/api/v1/hostlink/peers`：轻量诊断；
- `/api/v1/status-incidents`、`/api/v1/error-decisions`：人工决策；
- `/api/v1/scheduler/resources`：本机调度（默认）时可读；接入云端后以 503
  明确表示调度权威在远端 Backend。

默认（本机调度）Host 同时挂载 Workflow Authority，这是 edge UI 的写入口：

- `/api/v1/workflows` 管理定义；
- `/api/v1/workflows/{uuid}/graph` 管理整图；
- `/api/v1/workflow-tasks` 创建一次运行；
- `/api/v1/workflow-tasks/{uuid}/jobs` 查询节点 Job。

UI 不得再向 `/workflows` 提交“直接执行的 DAG”。正确写链路是：保存 Workflow
定义，保存 Graph，再创建 Workflow Task。接入云端（Backend-controlled）的 Host
不挂本地 Workflow 写 API，图由 Backend 持有，Edge 只接收已经调度好的 Job 命令。

## 实时与恢复

UI 的恢复基线来自四库 API，而不是进程内事件缓存：

- 当前执行状态读取 `runtime.execution_job`；
- 设备当前值读取 `telemetry.device_state_latest`；
- 执行历史读取 `history.history_event`；
- 物料余额和预留读取 Materials API；
- WebSocket 只传短通知，完整命令和状态经 HTTP 数据面获取。

因此断线重连不依赖 SSE replay，也不会因服务重启丢失恢复水位。

## 尚需前端配合

1. 移除所有旧执行形 Workflow 请求和旧 Provider 地址配置。
2. Timeline 改为组合 Runtime、History 和 Telemetry 的持久化投影。
3. retry 必须表现为 Backend 创建的新 attempt/job；Edge 不在原 Job 上本地重排。
4. 调度资源页面要识别 `/scheduler/resources` 的 503：这表示该 Host 已接入云端、
   调度权威在远端 Backend，不是服务故障。

## 回归重点

- 普通 Host 启动时只打开 `runtime.db`、`materials.db`、`telemetry.db`、
  `history.db` 四个 writer；
- 同一物料即使流向不同设备动作，也由同一个 Scheduler 串行化；
- 仓储 reservation 分配出的实体物料 UUID 会进入同一资源申请；
- 执行端遇到动作冲突会拒绝，不创建本地等待队列；
- Runtime 和 History 的状态转换仍使用既有 Service，不增加或修改表结构。
