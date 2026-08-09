# 接口 / 协议 / 设计: OS 本地 DAG 执行器

> **Author: HUMAN 定义 / CLAUDE 补充示例**
> Claude 实现时严格遵循，不自行发挥接口风格。
> 契约字段严格镜像 backend `uni-lab-backend` 现有模型，保证"接口和后端一致"。

## 设计类型

- [x] 调度/后端逻辑（OS 边侧本地 DAG 走图）
- [x] 通信桥接口（WebSocket 下行 `task_dag`，上行沿用 `job_status`）

---

## 一、通信桥接口

### 1.1 下行（backend → OS）新增消息 `task_dag`

WebSocket 消息统一 `{ "action": <type>, "data": <payload> }`。新增一种 `action = "task_dag"`。
**载荷 = 整张 DAG**。每个节点字段镜像 backend `engine.SendActionData`
（`pkg/core/schedule/engine/model.go:90`），边字段镜像 backend `model.WorkflowEdge`
（`pkg/repo/model/workflow.go:71`，`source_node_uuid`/`target_node_uuid`）。

```jsonc
{
  "action": "task_dag",
  "data": {
    "task_id": "<uuid>",           // 整张图的任务 id（= 现有 job_status.task_id）
    "notebook_id": "<uuid>",       // 记录本；与 SendActionData.notebook_id 同义
    "server_info": { /* ... */ },  // 与 SendActionData.server_info 同结构，整图共用
    "nodes": [
      {
        "node_id": "<uuid>",       // 节点 id；同时用作该节点的 job_id（幂等键 (task_id,node_id)）
        "device_id": "string",     // = SendActionData.device_id
        "action": "string",        // = SendActionData.action（动作名）
        "action_type": "string",   // = SendActionData.action_type
        "action_args": { /* ... */ }, // = SendActionData.action_args
        "sample_material": { /* uuid->uuid */ }, // = SendActionData.sample_material，可空
        "always_free": false       // 可选；缺省由 registry 决定，OS 不强依赖此字段
      }
    ],
    "edges": [
      { "source_node_uuid": "<uuid>", "target_node_uuid": "<uuid>" } // 依赖：source 先于 target
    ]
  }
}
```

**与现有 `job_start` 的关系**：一个 `task_dag` 节点在真正提交给 `DeviceActionManager` 时，
被 OS 内部**展开成等价于现有 `job_start` 的执行请求**（device_id/action/action_type/action_args/
notebook_id/job_id=node_id/task_id/node_id/server_info/sample_material）。因此**执行路径与现有
逐节点 job_start 完全一致**，只是"何时提交"由 OS 本地走图决定，而非云端逐条下发。

### 1.2 上行（OS → backend）**沿用现有 `job_status`，不改**

每个节点产生状态时，走现有 `WebSocketClient.publish_job_status(...)`，载荷逐字节不变
（`ws_client.py:1662`）：

```jsonc
{
  "action": "job_status",
  "data": {
    "job_id": "<node_id>", "task_id": "<uuid>", "device_id": "string",
    "notebook_id": "<uuid>", "action_name": "string",
    "status": "running|success|failed",
    "feedback_data": { }, "return_info": { }, "timestamp": 0.0
  }
}
```

backend `EdgeImpl.OnJobStatus`（`pkg/core/schedule/lab/edge/edge_msg.go:64`）与其
`JobData`（`engine/model.go:119`）**零改动**即可消费。

### 1.3 取消：沿用现有 `cancel_task`

`{ "action": "cancel_task", "data": { "task_id": "<uuid>" } }` → OS 复用
`DeviceActionManager.cancel_jobs_by_task_id`（`ws_client.py:340`）取消整张图在跑/排队节点，
并令 `DagExecutor` 停止调度后继。

### 1.4 端点

沿用现有边云 WebSocket 隧道（`ws_client.py`），无新端点。前端沿用 `/ws/workflow/{uuid}`。

---

## 二、调度/后端设计（OS 侧 `DagExecutor`）

### 2.1 数据结构

- `DagNode`：node_id / device_id / action / action_type / action_args / sample_material / always_free。
- `DagEdge`：source_node_uuid / target_node_uuid。
- `TaskDag`：task_id / notebook_id / server_info / nodes / edges；`from_message(data)` 解析并校验（拒环）。
- `NodeState` 枚举：`PENDING → READY → RUNNING → SUCCESS | FAILED | CANCELLED`。

### 2.2 资源模型 / 走图

- in-degree 表 `indeg[node_id]` 由 edges 构建；`ready = {n : indeg[n]==0}`。
- 每 tick 提交 ready 集全部节点给**注入的节点调度器 `submit(node) -> awaitable(status)`**：
  - 生产实现 = 复用 `_handle_job_start` 的 send_goal 路径（经 `DeviceActionManager`）。
  - 测试实现 = fake 调度器（可控时钟 + 可编程结果）。
- 节点终态回调（复用 `publish_job_status` 的 success/failed 拦截点）：
  - `success` → 对每条 out-edge `indeg[target]-=1`；归零者入 ready。
  - `failed` → fail-fast：取消同 task 未终态节点，任务 FAILED。
- **同设备互斥不在 DagExecutor 内做**——交给现有 `DeviceActionManager` 每设备锁天然保证（I3）。
  DagExecutor 只管依赖偏序（I1/I2），锁归锁、图归图，两层解耦。

### 2.3 不变量（对应 requirement I1–I6）

- I1 每节点恰好调度一次；I2 边 u→v ⇒ v 起跑晚于 u 终态；I3 同 device_action_key 非 always_free 不重叠；
- I4 resume 幂等（游标 + (task_id,node_id) 幂等缓存）；I5 含环解析即拒；I6 无环有限步终止。

### 2.4 时钟注入点

- DagExecutor 不直接 `time.sleep`；一切"等节点完成"通过注入的调度器 awaitable + 注入时钟推进。
- 超时（若节点级超时）用注入时钟，测试可控推进，不依赖墙钟。

### 2.5 本地持久化（断网/重启 resume）

- 游标 = `{ task_id, completed: [node_id], inflight: [node_id], failed: bool }`，落本地文件（原子写）。
- 恢复：读游标 → 已 completed 的节点视作已满足依赖，重建 in-degree/ready → 未完成从 ready 续跑。
- 与现有幂等 job 缓存叠加：即使游标漏记，(task_id,node_id) 幂等缓存兜底防重复执行（I4 双保险）。

---

## 三、对端改动清单（跨仓，本仓不实现，仅冻结契约）

### 3.1 backend（`uni-lab-backend`）
- 新增 `task_dag` 下行：把现有 `dag.go` 云端逐节点 `job_start` 走图，改为"编译后一次性发整张图"。
  可用开关与现有逐节点模式并存（灰度）。
- **不改** `OnJobStatus` / `JobData` / `cancel_task`。

### 3.2 前端（`Uni-Lab-Cloud`）— 复用两个既有 panel，零契约改动
- `WorkflowDAGPanel`（registry id `workflow-dag`，`web/src/panels/WorkflowDAGPanel.tsx`）：DAG 视图。
- `WorkflowStepsPanel`（registry id `workflow-steps`，`web/src/panels/WorkflowStepsPanel.tsx`）：步骤/线性视图。
- 二者经 `/ws/workflow/{uuid}` 订阅逐节点状态；因上行 `job_status` 契约不变，
  **逐节点 running/success/failed 渲染无需改动**。触发执行沿用 `WorkflowWSActionType.RunWorkflow`，
  停止沿用 `StopWorkflow`（`web/src/types/workflow.ts:178`）。

---

## 四、测试策略

- **fake/mock 点**：
  - 节点调度器 `submit(node)`：fake 版返回可编程 (status, 完成时刻)，不连真实设备/send_goal。
  - 时钟：注入可控 clock，`advance(dt)` 推进，断言并发/串行/超时，无 `time.sleep`。
  - 持久化：tmp_path 落游标文件，模拟"重启"= 丢弃内存态、从文件重建。
- **Hypothesis 覆盖的性质**：I1–I6。生成任意合法 DAG（随机节点数、随机无环边、随机 device 分配、
  随机 always_free 标记），断言：恰好一次执行、偏序遵从、同设备无重叠、resume 等价、含环即抛、终止。
