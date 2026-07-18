# 接口 / 协议 / 设计: 本地工作流桥（local_bridge）

> **Author: HUMAN 定义 / CLAUDE 补充示例**
> Claude 实现时严格遵循，不自行发挥接口风格。
> OS 面契约严格镜像 F002 `interface-design.md`（`task_dag`/`job_status`），保证"接口和后端一致"。

## 设计类型

- [x] 通信桥接口（三面：OS 面 WS / 实现 A UI 面 WS / 实现 B UI 面 HTTP）
- [x] 调度/后端逻辑（翻译核：UI 图 → F002 `TaskDag`；离线备档执行）

## 架构总览

```
[Next.js panels :32234] --WS /ws/workflow/{uuid}----\
                                                      >--[local_bridge]--WS /api/v1/ws/schedule--[unilab --test_mode 真实 OS 进程]
[SZLab local_ui :5174] --HTTP /api/* (:8014)--------/    （task_dag 下发 / job_status 回流 = F002 真实路径）
```

- 桥对 **OS 面**是一个 **WS 服务器**（OS 的 `ws_client` 主动 `websockets.connect` 连进来）。
- 桥对 **实现 A** 是 `/ws/workflow/{uuid}` 的 WS 服务端（说 `WorkflowWSActionType`）。
- 桥对 **实现 B** 是 `/api/*` 的 HTTP 服务端（SZLab local_ui 轮询）。
- 三面共享一个翻译核 `workflow_to_dag`，最终都产出 F002 `TaskDag` 交同一执行路径。

端口（默认，可注入避免冲突）：OS 面 `:8890` · 实现 A UI 面 `:8891` · 实现 B UI 面 `:8014`。

---

## 一、OS 面：WS 服务器 `/api/v1/ws/schedule`（严格复用 F002 契约）

OS 的 `ws_client._build_websocket_url()` 从 `HTTPConfig.schedule_addr` 拼出
`ws(s)://<host>/api/v1/ws/schedule` 并主动连入。桥在此路径 accept OS 连接。

### 1.1 下行（桥 → OS）`task_dag` —— 逐字段对齐 F002 §1.1

```jsonc
{
  "action": "task_dag",
  "data": {
    "task_id": "<uuid>",
    "notebook_id": "<uuid>",
    "server_info": { /* 整图共用 */ },
    "nodes": [
      { "node_id": "<uuid>", "device_id": "string", "action": "string",
        "action_type": "string", "action_args": { }, "sample_material": { }, "always_free": false }
    ],
    "edges": [ { "source_node_uuid": "<uuid>", "target_node_uuid": "<uuid>" } ]
  }
}
```
`node_id == job_id`，幂等键 `(task_id, node_id)`。**不新造任何字段。**

### 1.2 上行（OS → 桥）`job_status` —— 逐字段对齐 F002 §1.2

```jsonc
{ "action": "job_status",
  "data": { "job_id": "<node_id>", "task_id": "<uuid>", "device_id": "string",
            "notebook_id": "<uuid>", "action_name": "string",
            "status": "running|success|failed",
            "feedback_data": { }, "return_info": { }, "timestamp": 0.0 } }
```

### 1.3 取消 `cancel_task` —— 对齐 F002 §1.3

`{ "action": "cancel_task", "data": { "task_id": "<uuid>" } }`。

### 1.4 桥对内暴露的 API（schedule_ws.py）

- `submit_dag(task_dag: TaskDag) -> run_handle`：向已连入的 OS 下发并登记 `(task_id,node_id)` 状态表。
- `on_job_status(cb)`：注册回调，OS 每回一条 `job_status` 即触发（供两 UI 面翻译）。
- `cancel_task(task_id)`：下发 `cancel_task` 并停止后继收敛。
- `run_handle`：可 await，全节点终态后完成；暴露逐节点 `NodeState` 快照。

---

## 二、实现 A UI 面：WS 服务器 `/ws/workflow/{uuid}`（匹配 panel 契约）

前端 `useWorkflowWebSocket`（`web/src/services/workflowService.ts`）连
`WSS_URL_V2 + /ws/workflow/{uuid}?access_token_v2=...`。桥在此 accept，收发形状必须匹配
`WorkflowWSActionType`（上行）与 `WorkflowDAGPanel.onMessageCallback` 的 `data.data.action`（下行）。

### 2.1 上行（panel → 桥）动作枚举（`web/src/types/workflow.ts`）

| action 字符串 | 语义 | 桥处理 |
|---|---|---|
| `fetch_graph` | 拉图 | 回 demo 图（见 2.2） |
| `run_workflow` | 运行 | demo 图 → `TaskDag` → `schedule_ws.submit_dag` |
| `stop_workflow` | 停止（data=taskId） | `schedule_ws.cancel_task` |

（其余 create_node/batch_* 等编辑动作本地联调工具可暂回 no-op ack，不阻塞。）

### 2.2 下行（桥 → panel）报文形状 —— **双层嵌套 `data.data`**

- 拉图：`{ "data": { "action": "fetch_graph", "data": { "nodes": [...], "edges": [...] } } }`
- 逐节点态（**核心**）：把 OS 回流的每条 `job_status` 翻译成
  ```jsonc
  { "data": { "action": "workflow_update",
              "data": { "task_status": "running|end", /* + node 定位/executor 字段 */ } } }
  ```
  panel 据 `data.data.action==='workflow_update'` 调 `setNodeExecutedExecutor(data.data)`；
  `task_status==='end'`（`WorkflowStatusEnum.Finished`）判该节点完成。
- 生命周期：`run_workflow`/`stop_workflow` 回同名 action 标注开始/停止。

> **契约锁点**：`job_status.status` → `task_status` 映射：`running→running`，`success/failed→end`
> （panel 只认 running/end；成败细节走 executor 字段）。测试须断言此形状。

---

## 三、实现 B UI 面：HTTP 服务器 `/api/*`（匹配 SZLab local_ui）

端点集对照 `unilabos_local_ui/src/main.tsx` 的 fetch 调用：

| 方法 · 路径 | 语义 | 桥处理 |
|---|---|---|
| `GET /api/preset` | 预设工作流/设备 | 回 demo 预设 |
| `GET /api/stack-status` | 栈就绪状态 | 反映 OS 是否连入（不假装就绪） |
| `POST /api/workflow/build-graph` | 构图 | UI 草稿 → 结构化 nodes/edges |
| `POST /api/run` | 运行 | 图 → `TaskDag` → `submit_dag`，返回 `{ run_id }` |
| `GET /api/run/{id}` | 轮询（1s） | 回逐节点态 + 结构化 log events（来自真实 `job_status`） |
| `POST /api/run/{id}/cancel` | 取消 | `cancel_task` |

`vite dev :5174` 的 `/api` → `127.0.0.1:8014` 代理不变。响应结构以 local_ui 消费处为准，
测试对照端点集与字段。

---

## 四、共享翻译核 `workflow_to_dag.py`

UI 工作流图（两套 UI 各自的 nodes/edges 形状）→ F002 `TaskDag`：
- 复用 `unilabos/scheduler/dag_model.py:TaskDag.from_message` 的字段约定与**环校验**（解析期拒环 = F002 I5）。
- node → `{node_id, device_id, action, action_type, action_args, sample_material, always_free}`；
  edge → `{source_node_uuid, target_node_uuid}`。
- 这是实现 A / 实现 B **唯一共享**的业务逻辑，避免两面各写一套翻译。

---

## 五、执行模式（两档）

- **主：真实下发**——`schedule_ws` 等真实 `unilab --backend simple --test_mode --graph <demo>`
  连入（`schedule_addr` 指向桥）。`task_dag` 走真实 F002 路径，`job_status` 真实回流。
  demo 图选 `unilabos/test/experiments/` 内含虚拟设备者。
- **备：离线自足**——无真实 OS 时，桥内用 F002 `TaskDagRunner` + 仿真节点调度器
  （照 `tests/scheduler/fake_scheduler.py`，每设备 `asyncio.Lock` 保 I3）跑同一 `TaskDag`，
  UI 仍完整动。用于 hermetic 测试与无 OS 演示。

---

## 六、测试策略

- **fake/mock 点**：
  - OS 连接：hermetic 测用内存 fake OS（收 `task_dag`、按脚本回 `job_status`），不起真实进程。
  - 节点调度器（离线档）：照 F002 `fake_scheduler.py`，每设备锁建模 I3。
  - 无 `time.sleep`：完成时刻手动驱动，零墙钟推进。
- **契约断言**：
  - OS 面：`task_dag`/`job_status` 逐字段对照 F002 §一（含 `node_id==job_id`、幂等键）。
  - 实现 A：下行 `data.data.action` 形状匹配 `WorkflowDAGPanel.onMessageCallback`。
  - 实现 B：端点路径/方法/响应结构匹配 `local_ui/src/main.tsx`。
- **翻译核**：合法图往返、含环图解析期抛 `DagValidationError`（复用 F002 校验）。

---

## 七、对端 / 前端改动清单（不改组件，仅配置）

### 7.1 uni-lab-cloud（实现 A）
- **不改** `WorkflowDAGPanel` / `WorkflowStepsPanel` 组件。
- 仅设 `NEXT_PUBLIC_WS_URL`（`web/src/config.ts:144` 的 `WSS_URL_V2`）指向 `ws://127.0.0.1:8891`，
  在 tmux 重启 dev server。

### 7.2 SZLab local_ui（实现 B）
- 取 `unilabos_local_ui/` 框架层（已完成）；**不取** `szlab_poly_studio` 专有设备栈。
- `vite.config.ts` 的 `/api` → `127.0.0.1:8014` 代理不变；后端换成桥的 `local_api`。
