# 需求规格: OS 本地 DAG 执行器（整张工作流下沉边缘执行）

> **Author: HUMAN（据 /goal 指令定稿）** | Claude 只读，按此实现，不修改。
> 工作流程见 docs/agent-workflow.md，代码规范见根目录 AGENTS.md
> 上游架构依据：主仓 `product_designs/scheduler_and_preservation/13_control-plane-vs-data-plane.md`、
> `product_designs/ai_native_org/内核与应用模块归属地图.md`。

## 背景

当前 backend（Go `pkg/core/schedule/engine/dag`）用 `errgroup + gctx` 在**云端**逐节点走 DAG：
每个节点单独下发一条 `job_start`，阻塞等 `job_status` 回调，再调度后继节点。
**跨步编排（走图这件事本身）活在云上**——一旦边云网络在任务中途断开，DAG 走不动，
即使所有设备都在边侧、都能跑，整个任务也会**卡在半路**。

OS 侧（`unilabos/app/ws_client.py::DeviceActionManager`）已经拥有执行一个节点所需的全部零件：
每设备 FIFO 锁 + busy/free、`always_free` 放行、幂等 job 缓存（(task_id, job_id) 键）、
`send_goal` 执行、`publish_job_status` 回调。**唯独缺"任务级走图"这一层**——
`task_id` 现在只是一个分组标签，不是可执行的图。

styxhuang fork（`scripts/run_workflow_local.py`）已验证"本地执行工作流"可行，但它是
**拓扑排序压平成线性 + 串行 for 循环**，且直接 `getattr(device, method)()` 绕过了
`DeviceActionManager` 的锁与幂等。线性化会**丢掉 DAG 的宽度（并发）**：6 台磁搅并联被压成
一台接一台，墙钟从"关键路径"退化为"各段之和"。

**本需求 = 把"走图"从云搬到 OS 边侧，且不牺牲并发**：backend 一次性把**整张 DAG**
（nodes + edges）下发给 OS，OS 本地维护 in-degree / ready-set 就地并发走图，
每个节点仍走现有 `DeviceActionManager` 锁 + `send_goal` + `publish_job_status`。
边全在 OS、断网中途无影响、宽 DAG 保持并发、线性只是它的退化特例。

## 用户故事

```
As a 实验室边侧运行时（OS host_node），
I want to 一次性接收整张工作流 DAG 并在本地就地并发走图执行，
So that 即使边云网络在任务中途断开，任务仍能按依赖关系继续跑完，
        且相互独立的分支并发执行、同设备节点自动串行、断电重启可续跑。
```

## 详细描述

### Happy path
1. backend 编译工作流后，通过 WebSocket 下发一条 `task_dag` 消息，载荷含
   `task_id` / `notebook_id` / `server_info` / `nodes[]`（每个节点 = 一个可执行动作）/
   `edges[]`（`source_node_uuid → target_node_uuid` 依赖）。
2. OS `DagExecutor` 解析：从 edges 建 in-degree 表；in-degree 为 0 的节点入 ready-set。
3. 每一轮：把 ready-set 里**所有**前置已满足的节点一起提交给 `DeviceActionManager`：
   - 不同设备的节点 → 并发起跑；
   - 同一设备（同 `device_action_key`）的节点 → 现有每设备锁天然串行排队；
   - `always_free` 节点 → 现有逻辑放行并发。
4. 每个节点完成（`publish_job_status` 终态 `success`）→ 后继 in-degree - 1；
   新归零的进入 ready-set。直到全部节点终态。
5. 每个节点上行的 `job_status` 契约**与现状逐字节一致**——backend `OnJobStatus`、
   前端两个工作流 panel 无需改动即可实时渲染逐节点状态。

### 异常与边界
- **中途断网**：整张图与执行游标都在本地，OS 继续走图；重连后 `job_status` 补发（复用现有缓存回放）。
- **进程重启**：从本地持久化游标 + 幂等 job 缓存恢复 ready-set，已完成节点不重复执行。
- **节点失败**（终态 `failed`）：fail-fast——取消同组在跑/排队节点（对齐 backend gctx 兄弟组取消语义），
  停止调度新节点，任务整体判失败。
- **环**：解析即拒绝（in-degree 永不归零的节点集非空 → 明确报错，不静默挂起）。
- **取消**：现有 `cancel_task`（按 task_id）取消整张图；复用 `cancel_jobs_by_task_id`。
- **重复下发**（同 task_id 再次 `task_dag`）：幂等——已有终态的节点不重跑。

### 与现有模块的交互
- 复用 `DeviceActionManager`（锁/队列/幂等）、`_handle_job_start` 的 send_goal 路径、`publish_job_status`。
- 在 `ws_client.py` 消息分发（第 636 行 if/elif 链）新增 `elif message_type == "task_dag"`。
- **不改** `job_status` 上行格式、不改 `DeviceActionManager` 语义、不改设备驱动。

## 验收标准（Given/When/Then，Claude 逐条验证）

### AC-1: 整张 DAG 本地并发走图
```
Given 一张含并联分支的 DAG（A→B、A→C、B→D、C→D，B/C 分属不同设备），
When  OS 收到 task_dag 并执行，
Then  A 完成后 B 与 C 并发起跑（非串行），B 和 C 都完成后 D 才起跑，
      且全部 4 个节点各恰好执行一次。
```

### AC-2: 同设备自动串行
```
Given DAG 中两个 ready 节点落在同一 device_action_key 且均非 always_free，
When  两者同轮进入 ready-set，
Then  经 DeviceActionManager 每设备锁，二者串行执行（不重叠），顺序稳定。
```

### AC-3: 断网中途不影响完成
```
Given 一张多节点 DAG 正在执行，
When  执行到中途边云 WebSocket 断开，
Then  OS 继续按依赖走完剩余节点；重连后各节点 job_status 被补发，
      backend 观察到的最终每节点状态与不断网时一致。
```

### AC-4: 进程重启续跑且不重复
```
Given 一张 DAG 执行到一半，部分节点已终态 success，
When  OS 进程重启并从本地游标 + 幂等缓存恢复，
Then  已完成节点不重复执行，未完成节点从正确的 ready-set 继续，任务最终跑完。
```

### AC-5: fail-fast 与环拒绝
```
Given (a) 某节点终态 failed；或 (b) DAG 含环，
When  执行 / 解析，
Then  (a) 同组在跑/排队节点被取消、不再调度新节点、任务整体 failed；
      (b) 解析阶段即报错，不进入执行、不静默挂起。
```

### AC-6: 上行契约与后端一致（前端 panel 零改动复用）
```
Given OS 逐节点执行整张 DAG，
When  每个节点产生状态,
Then  上行 job_status 载荷字段与现状逐字节一致（job_id/task_id/device_id/
      notebook_id/action_name/status/feedback_data/return_info/timestamp），
      backend OnJobStatus 与前端 WorkflowDAGPanel / WorkflowStepsPanel 无需改动即可消费。
```

## 涉及模块

- **调度/后端（OS 侧新增）**: `unilabos/scheduler/dag_executor.py`（本地 DAG 走图核心，节点调度器与时钟可注入）
- **数据结构**: `TaskDag` / `DagNode` / `DagEdge`（解析 task_dag 载荷；字段镜像 backend `SendActionData` + `WorkflowEdge`）
- **本地持久化**: 执行游标（completed / in-flight 节点集），断网/重启 resume
- **通信桥**: `unilabos/app/ws_client.py` 新增 `_handle_task_dag`，接 `DeviceActionManager` + `send_goal` + `publish_job_status`
- **通信协议**: WebSocket（下行 `task_dag`，上行沿用 `job_status`）
- **对端（本仓外，接口对齐清单见 interface-design.md）**:
  - backend `uni-lab-backend`：新增 `task_dag` 下发（可与现有逐节点 job_start 并存/切换）
  - 前端 `Uni-Lab-Cloud`：复用现有 `WorkflowDAGPanel`（id `workflow-dag`）与 `WorkflowStepsPanel`（id `workflow-steps`），因上行契约一致，逐节点状态渲染零改动

## 正确性关注点（OS 特有 — 必须 property-based 覆盖）

调度是数学，用 Hypothesis 对**任意合法 DAG**覆盖不变量：
- **I1 恰好一次**：每个非 disabled 节点在一次成功执行中恰好被调度一次。
- **I2 偏序遵从**：若存在边 u→v，则 v 的起跑晚于 u 的终态（拓扑正确）。
- **I3 同设备互斥**：任一时刻，同一 device_action_key 上非 always_free 节点不并发重叠。
- **I4 resume 幂等**：任意"执行到中途快照 → 重启恢复"路径，最终结果与不中断执行等价，且无重复执行。
- **I5 环即拒绝**：含环输入必在解析期抛错，绝不进入执行。
- **I6 终止性**：无环输入必在有限步内到达全终态。

## 依赖关系

- 前置功能: `DeviceActionManager`（现有）、幂等 job 缓存（现有）、`publish_job_status`（现有）。
- 外部依赖（真实硬件/服务）: 无——测试用**可注入的 fake 节点调度器**替代真实 send_goal / 设备，
  用**可控时钟**替代墙钟；不连真实 OPC-UA / Modbus / 串口，不 time.sleep。

## 验证方法

- [ ] `python -c "import unilabos"` 通过
- [ ] `pytest tests/scheduler/` 通过
- [ ] DagExecutor 有 hermetic 测试（fake 节点调度器 + 可控时钟，覆盖 AC-1~AC-5）
- [ ] I1–I6 不变量有 Hypothesis 覆盖
- [ ] `task_dag` / `job_status` 契约变更经 contract-guardian 评审为 PASS

## 不做什么（Out of Scope）

- 不改 `job_status` 上行格式、不改 `DeviceActionManager` 内部语义、不改任何设备驱动。
- 不实现 Tier-O 全局优化调度（那是云侧 advisory，见 doc13）；本需求只做边侧可行性走图。
- 不做条件分支 / 循环 / 动态生成节点（DAG 静态；控制流留后续需求）。
- 不在本仓实现 backend 的 `task_dag` 发送端与前端改动（跨仓，仅在 interface-design.md 冻结契约）。
