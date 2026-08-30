# 实验室协议 0.1

本文描述 UniLabOS 当前微后端、统一调度器、库存与 Edge 执行链路。实现入口以
`unilabos/server/backend/` 为准；数据库边界和表目录见
`unilabos/server/database/DESIGN.md`。

## 1. 核心约束

系统必须始终满足以下约束：

1. 一个 Job 只能由一个调度权威准入。
2. DAG 就绪、动作互斥、物料互斥和仓储分配属于同一调度决策。
3. 执行端不保存等待队列；资源冲突必须拒绝并回到调度权威重算。
4. 库存预留必须先于动作下发，库存消费必须紧邻真实驱动调用且幂等。
5. Runtime、Materials、Telemetry、History 只通过既有 Repository 和 Service
   写入，不新增 Scheduler 私有数据库、私有历史表或平行物料模型。
6. WebSocket 只承担短通知；完整命令、状态和恢复数据走持久化 HTTP 数据面。

## 2. 两种部署 profile

调度语义只有一套，但权威可以位于不同进程。判定依据唯一：是否显式配置了云端
Backend 地址（`HTTPConfig.remote_addr`，CLI 统一入口为 `--address`）。

| Profile | 图和 DAG 权威 | Edge 内部行为 |
| --- | --- | --- |
| 默认本机调度（未配置云端地址） | 本进程 `BackendScheduler` | 本地 Workflow Task 转 DAG；同一调度器管理动作、物料和库存；配合 edge UI 直连使用 |
| Backend-controlled（显式配置云端地址） | 远端 Backend | 本机不调度，只接收单个已调度 Job，先写 Runtime/History，再交给执行器 |

同一进程内永远只有一个调度权威。`GET /api/v1/health` 返回 `scheduler=local`
是默认且正确的状态；接入云端后返回 `remote`。

## 3. 由谁启动

CLI 运行时的启动顺序如下：

```text
app/main.py
  -> server/startup.py::setup_host_server_stack
     -> resolve_database_paths
     -> server/composition.py::configure_server_services
     -> setup_materials_service / Materials client
     -> setup_execution_backend
        -> JobExecutionBackend
        -> WorkflowBusinessCoordinator(RuntimeService, HistoryService)
     -> [未配置云端地址] setup_local_scheduler
        -> WorkflowService
        -> BackendScheduler
```

`setup_host_server_stack()` 是 Host 的唯一组合入口。它先绑定四库路径和 Materials
Authority，再创建执行层，因此不存在执行器先于库存权威工作的启动窗口。

关闭时顺序相反：先停止本地 Scheduler 和执行 worker，再关闭四库 writer。

## 4. 图如何进入系统

### 4.1 默认本机调度

默认 Host 通过本机 Workflow API（edge UI 的写入口）接收定义和 Graph：

1. `PUT /api/v1/workflows/{uuid}/graph` 保存节点和边。
2. `POST /api/v1/workflow-tasks` 固化 `workflow_snapshot` 和 `execution_plan`。
3. `WorkflowService` 调用已绑定的 `BackendScheduler.submit(task_uuid)`。
4. Scheduler 从持久化 Task/Node Job 构建 `TaskDag`，恢复已完成节点后开始走图。

`/workflows` 只表示 Workflow 定义，不再兼容“提交一张图立即执行”的旧语义。

### 4.2 Backend-controlled（接入云端）

显式配置云端地址后，Edge 不接收也不保存整张工作流图，本机 Workflow 写 API 不
挂载。远端 Backend 持有图、完成 DAG 和资源调度，然后下发一条 `execute_job` 命令：

1. Backend 经 control WebSocket 发送带 UUID、类型、sequence 和内容哈希的短通知。
2. `WorkflowBusinessCoordinator` 经 HTTP 拉取完整命令和 payload。
3. Coordinator 校验通知身份、payload 哈希、endpoint 和 transport。
4. 命令先写 `runtime.command_inbox`，payload 写 `history.payload_object`。
5. 创建或恢复 `runtime.execution_job`，推进到 `dispatch_pending`。
6. 将规范化执行 payload 交给 `JobExecutionBackend`。

Edge 不根据单个 Job 反推整图，也不在本地创建 retry。retry 是 Backend 新建的
attempt 和新 `job_uuid`。

## 5. 一轮统一调度如何进行

`BackendScheduler` 对每个 DAG-ready 节点执行以下步骤：

1. 解析上游 Job 输出到当前 action 参数。
2. 从设备 action 注册信息读取 `materials_need_lock` 参数名。
3. 从 action 参数提取权威 `material_uuid`；非权威或缺失 UUID 直接失败。
4. 合并 Task 仓储 reservation 已分配的实体物料 UUID。
5. 构造一个 `SchedulerResourceRequest`，其中同时包含：
   - `(device_id, action_name)` 动作 claim；
   - 所有 action 参数物料 claim；
   - 所有仓储分配实体物料 claim。
6. `SchedulerResourceManager.acquire()` 以 all-or-nothing 方式申请完整集合。
7. 状态为 `held` 才标记 Node Job running 并下发；状态为 `waiting` 时只留在
   Scheduler 的等待集合中，执行端完全不可见。

资源管理器使用稳定登记顺序避免后来者越过有冲突的早期等待者，也不会让一个 Job
只持有部分锁。

## 6. 什么时候重算

统一 Scheduler 没有独立定时轮询。重算由事实变化触发：

- 新 DAG-ready 节点登记完整资源申请时；
- Job 成功、失败、跳过、取消并释放资源时；
- Task fail-fast 清理剩余 Job 时；
- 资源 handoff 完成或取消时；
- 进程恢复后重新提交可恢复 Task 时。

每次 `acquire/release/cancel` 都会让 `SchedulerResourceManager` 重新提升可满足的等待
请求；释放后 `BackendScheduler._reconcile_resources()` 只下发本轮已经变为 `held`
的 Job。DAG runner 收到节点终态后，再计算新的 ready 节点。

终态顺序固定为：

```text
持久化 Node Job 终态
  -> 释放动作和物料 claims
  -> 重算并提升等待 Job
  -> 通知 DAG runner
  -> 计算新 ready 节点
```

这保证下一个 Job 起跑前，上一个 Job 的结果已经可供参数解析和审计查询。

## 7. 物料和仓储扣减语义

“每轮调度都带着物料和仓储”不等于“每轮重复扣库存”。正确事务边界是：

1. **Task 准入时一次性预留**：Scheduler 将该 Task 所有声明的
   `inventory_requirements` 交给 `MaterialsService.reserve_task_inventory()`，使用
   一个事务 all-or-nothing 创建 Job reservation。
2. **每轮资源重算都带入物料**：每个 Node 的完整资源申请包含 action 参数物料和
   reservation 分配出的实体物料，所以不同设备也不能同时操作同一物料。
3. **驱动调用前只消费一次**：`ExecutionInventoryCoordinator` 校验 reservation
   的 `job_uuid`、Task、revision 和 requirements，然后在 `send_goal` 前 consume。
4. **终态收口**：未 consume 的 active reservation 在 Task 终态释放；已经 consume
   的数量不返还，失败或取消的实体物料进入 quarantine，成功按账本完成。

Scheduler 不直接修改 `material_substance` 快照，数量权威是 `inventory_lot`，所有
reserve/consume/release/quarantine 事实进入 `inventory_ledger`。命令以
`(command_uuid, effect_key)` 幂等，因此恢复和重复投递不会重复扣减。

## 8. Edge 内部通信机制

```text
Backend WebSocket notice
  -> Coordinator HTTP fetch + hash validation
  -> RuntimeService / HistoryService durable write
  -> JobExecutionBackend.dispatch
  -> active-only DeviceActionManager
  -> worker event queue
  -> HostLink adapter or ROS2 adapter
  -> device action
  -> feedback/result callback
  -> Coordinator
  -> Runtime transition + History append + Backend event outbox
```

需要区分两种“队列”：

- `JobExecutionBackend` 的 worker queue 只做线程间事件串行化，不参与排序，也不保存
  等待资源的 Job；
- Scheduler 的 waiting request 才是调度等待状态，且只存在于唯一调度权威。

执行层仍保留最后一道防线：同一动作已有 active Job、状态 incident 持有设备、库存
reservation 不合法或物料 UUID 契约错误时，立即拒绝。它不会在 Edge 内部悄悄排队。

HostLink 主从通信位于 `unilabos/backend/hostlink/`。Slave 同步设备 action 注册信息到 Host，
Host 因而能读取远端设备的 `materials_need_lock` 和 status policy；动作执行仍由
HostLink adapter 路由到实际 Slave。

## 9. 四库与 Service

表结构保持不变，四库组合根是 `unilabos/server/composition.py::ServerServices`。

| 数据库 | 调度/执行使用方式 |
| --- | --- |
| `runtime.db` | 命令 inbox、execution job 状态机、endpoint、可靠 adapter/backend outbox |
| `materials.db` | Material/Site、lot、Task/Job reservation、库存 ledger |
| `telemetry.db` | endpoint/device latest 状态和追加事件，供状态联锁投影读取 |
| `history.db` | payload、feedback、result、error、decision 和 replacement chain |

Repository 只负责 SQL 与事务，Service 负责幂等和状态机。Scheduler 和执行层不得
直接创建 SQLite connection，也不得声明同名表。跨库只使用规范 UUID，不使用
`ATTACH DATABASE` 或跨库外键。

本机调度的 Workflow 定义、Graph 和 Task 事实由 `WorkflowService` 的现有
Workflow Store 保存；它不是 Runtime/History 的替代物。Backend-controlled
执行的完整 Job 生命周期始终进入 Runtime 和 History。

## 10. HTTP 面

默认 Host 挂载：

| 前缀 | 语义 |
| --- | --- |
| `/api/v1/runtime` | RuntimeService 数据面 |
| `/api/v1/materials` | MaterialsService 数据面；外置 Materials 时不重复挂本地写 API |
| `/api/v1/telemetry` | TelemetryService 数据面 |
| `/api/v1/history` | HistoryService 数据面 |
| `/api/v1/health` | Scheduler 位置和执行器 readiness |
| `/api/v1/hostlink/peers` | Host/Slave 连接诊断 |
| `/api/v1/status-incidents` | 状态联锁事件与人工决策 |
| `/api/v1/error-decisions` | 动作失败终态决策 |
| `/api/v1/scheduler/resources` | 本机调度器的动作/物料资源快照 |
| `/api/v1/workflows`、`/api/v1/workflow-tasks` | 本机 Workflow Authority 写 API（默认挂载；接入云端后不挂载） |

诊断 Router 不复制 Runtime、History 或 Telemetry 的业务查询。接入云端后请求
本地 scheduler resource snapshot 会返回 503，以明确表示 authority 不在该进程。

## 11. 状态联锁与错误闸门

Telemetry latest 投影用于执行前状态策略判断。违反 hold/reject 条件时创建 status
incident，并拒绝新动作；恢复条件满足或人工决策后解除 hold。

设备动作失败时先保存原始失败 payload 和 History event，再打开 Runtime error gate。
Backend 必须先更新调度事实，然后下发 `release_failed` 或 replacement result 决策。
人工替换通过 `supersedes_event_uuid` 关联原结果，不能覆盖原始历史。

## 12. 恢复与幂等

- Backend 命令由 `command_uuid` 和 backend sequence 去重；
- Runtime job transition 使用 version 校验；
- Materials mutation 使用 command/effect 幂等；
- Coordinator 启动时恢复未完成 dispatch、error gate 和可靠 outbox；
- 本地 Scheduler 启动时查询可恢复 Workflow Task，并从持久化 Job 终态重建 DAG walk；
- 执行端只登记 active Job，不承担跨重启调度恢复。

## 13. 实现入口

| 职责 | 文件 |
| --- | --- |
| Host 组合与启动 | `unilabos/server/startup.py` |
| 四库组合根 | `unilabos/server/composition.py` |
| 运行时组件装配 | `unilabos/server/backend/composition.py` |
| Backend 命令协调 | `unilabos/server/backend/coordinator.py` |
| 执行适配 | `unilabos/server/backend/execution.py` |
| 库存执行边界 | `unilabos/server/backend/inventory.py` |
| 统一调度服务 | `unilabos/server/backend/scheduler/service.py` |
| 动作/物料资源管理 | `unilabos/server/backend/scheduler/resource_manager.py` |
| DAG runner | `unilabos/server/backend/scheduler/dag/` |
| Backend 诊断 API | `unilabos/server/api/backend.py` |
| HostLink 网络与执行 adapter | `unilabos/backend/hostlink/` |

## 14. 已移除的兼容面

以下能力不再存在，也不应恢复为平行实现：

- 独立 Scheduler Provider 进程和第二套 Workflow 执行入口；
- Scheduler 私有 Inventory 数据库、Resource Provider 和仓储模型；
- 执行端动作等待队列与独立物料锁队列；
- Scheduler 私有 history/device-state store；
- 进程内 Monitor SSE replay；
- 本地 retry、旧 DAG cursor 文件和旧 timeline/snapshot API。

新增能力必须落到上述唯一 Service、协议和表设计中；需要变更 schema 时新增正式
migration，不得在 Scheduler 目录临时建表。
