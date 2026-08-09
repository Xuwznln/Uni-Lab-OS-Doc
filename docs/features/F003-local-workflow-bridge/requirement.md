# 需求规格: 本地工作流桥（两套前端直连 OS 本地 DAG 执行，替代 Go 后端）

> **Author: HUMAN（据 /goal + 本轮"同时开启两个实现"指令定稿）** | Claude 只读，按此实现，不修改。
> 工作流程见 docs/agent-workflow.md，代码规范见根目录 AGENTS.md
> 前置功能：F002（OS 本地 DAG 执行器）已完成，本需求复用其 `task_dag`/`job_status` 契约。

## 背景

/goal 要求：把整张 DAG 下发给 OS 本地执行、接口和后端一致、前端复用 uni-lab-cloud 的两种
工作流 panel、做完整实现、遵循 OS 仓库 harness。F002 已把**本地执行**这一层做完，但要真正
"跑起来给人看"，还缺**前端 → OS 之间那一段**——生产里这一段由 Go 后端承担（编译工作流、
下发 `task_dag`、回流 `job_status`、翻译前端 WS 协议）。

**本环境 Go 后端不可用**（Redis/Nacos/MQTT/Docker 均缺），全栈 E2E 不可行。因此本需求用一个
**本地桥（local_bridge）** 充当后端的"翻译面"：它对 OS 是一个 WS 服务器（OS 的 `ws_client`
主动连进来），对前端是前端各自协议的服务端。桥不复制执行逻辑——它只翻译协议、把整张图交给
F002 真实执行，再把 F002 回流的真实 `job_status` 翻译回各前端。

本轮进一步要求**同时开启两个实现**：
- **实现 A**：uni-lab-cloud 的 `WorkflowDAGPanel` + `WorkflowStepsPanel`（panel 组件零改动）；
- **实现 B**：SZLab `unilabos_local_ui`（Vite + React 19 + React Flow 本地联调工作台）。

两者都驱动 F002 的 `task_dag` 本地执行，都**不启动 Go 后端**。

## 用户故事

```
As a 在本地做工作流联调的实验室开发者（无 Go 后端可用），
I want to 用两套前端之一（云端 panel 或 SZLab local_ui）画/选一张工作流并点运行，
So that 整张 DAG 被下发给本地 OS 进程就地并发执行（接口与后端一致），
        并在前端实时看到逐节点 running→success/failed 的真实状态。
```

## 详细描述

### Happy path（实现 A：云端 panel）
1. 前端 panel 经 `/ws/workflow/{uuid}` 发 `fetch_graph` → 桥回一张 demo 工作流图（nodes/edges）。
2. 用户点运行 → panel 发 `run_workflow` → 桥把 demo 图翻译成 F002 `TaskDag` → 交 OS 面执行。
3. OS 本地并发走图，每个节点终态经 `job_status` 回流到桥。
4. 桥把每条 `job_status` 翻译成 panel 的 `workflow_update` 报文（`task_status` 驱动节点态）→
   两个 panel 逐节点实时渲染 running→end。
5. 用户点停止 → panel 发 `stop_workflow` → 桥 `cancel_task` → OS 取消整张图。

### Happy path（实现 B：SZLab local_ui）
1. local_ui 经 HTTP `/api/preset`、`/api/stack-status` 拉取预设与栈状态。
2. 用户构图 → `POST /api/workflow/build-graph` → 桥回结构化图。
3. 用户点运行 → `POST /api/run` → 桥翻译成 `TaskDag` 交 OS → 返回 `run_id`。
4. local_ui 每 1s 轮询 `GET /api/run/{id}` → 桥返回逐节点态 + 结构化 log events（来自真实 `job_status`）。
5. `POST /api/run/{id}/cancel` → 桥 `cancel_task`。

### 两档执行模式
- **主：真实下发**（满足 /goal「下发给 OS 本地执行」）——桥等待真实
  `unilab --backend simple --test_mode --graph <demo.json>`（其 `HTTPConfig.schedule_addr`
  指向桥）连入，`task_dag` 走真实 F002 路径，`job_status` 真实回流。`--test_mode` 保证无硬件也能跑完。
- **备：离线自足**——若无法拉起真实 OS 进程，桥内用 F002 的 `TaskDagRunner` + 仿真节点调度器
  （照 `tests/scheduler/fake_scheduler.py`，每设备锁保 I3）在进程内跑同一 `TaskDag`，UI 仍完整动。
  用于 hermetic 测试与无 OS 演示。

### 异常与边界
- **含环工作流**：桥翻译成 `TaskDag` 时复用 F002 `TaskDag.from_message` 解析期拒环，向前端回明确错误。
- **OS 未连入**：`/api/stack-status` 与 panel 生命周期如实反映"未就绪"，不静默假装成功。
- **取消**：两套前端的 stop/cancel 都归一到桥的 `cancel_task(task_id)`。
- **上行契约不变**：桥对 OS 面严格用 F002 `task_dag`/`job_status`，一字段不新造。

## 验收标准（Given/When/Then，Claude 逐条验证）

### AC-1: 翻译核 UI 图 → F002 TaskDag（含环拒绝）
```
Given 一张合法 UI 工作流图（nodes/edges）与一张含环图，
When  桥的 workflow_to_dag 翻译，
Then  合法图产出字段合规的 TaskDag（node_id/device_id/action/action_type/action_args，
      edges 用 source_node_uuid/target_node_uuid），含环图在解析期即抛 DagValidationError。
```

### AC-2: OS 面 WS 服务器收发 task_dag / job_status
```
Given 桥的 /api/v1/ws/schedule 面与一个 fake OS 连接，
When  桥 submit_dag(task_dag) 下发、fake OS 回 job_status 终态，
Then  桥按 (task_id, node_id) 收敛每节点状态，run_handle 在全节点终态后完成；
      cancel_task 能停止后继并解析未决。
```

### AC-3: 实现 A UI 面报文形状匹配 panel 契约
```
Given 桥的 /ws/workflow/{uuid} 面收到 fetch_graph / run_workflow / stop_workflow，
When  桥翻译并下行，
Then  下行报文形状严格匹配 WorkflowDAGPanel.onMessageCallback 的 data.data.action 分发：
      fetch_graph → {data:{action:'fetch_graph',data:{nodes,edges}}}；
      每节点态 → {data:{action:'workflow_update',data:{...task_status...}}}（驱动 setNodeExecutedExecutor）。
```

### AC-4: 实现 B /api/* 端点契约匹配 local_ui
```
Given SZLab local_ui 的端点集（preset/stack-status/workflow/build-graph/run/run/{id}/run/{id}/cancel），
When  桥的 local_api 提供等价端点，
Then  端点路径/方法/响应结构与 local_ui/src/main.tsx 的 fetch 调用一致，
      GET /api/run/{id} 返回可供 1s 轮询的逐节点态 + log events。
```

### AC-5: 与 F002 契约逐字段一致（接口和后端一致）
```
Given 桥对 OS 面下发 task_dag、接收 job_status，
When  逐字段对照 F002 interface-design.md §一，
Then  task_dag（task_id/notebook_id/server_info/nodes[node_id,device_id,action,action_type,
      action_args,sample_material,always_free]/edges[source_node_uuid,target_node_uuid]）
      与 job_status（job_id/task_id/device_id/notebook_id/action_name/status/feedback_data/
      return_info/timestamp）无新造字段，node_id==job_id，幂等键 (task_id,node_id)。
```

### AC-6: 组合入口三面并起
```
Given 桥的 server.py 组合入口，
When  启动，
Then  schedule_ws（OS 面）/ workflow_ws（实现 A UI 面）/ local_api（实现 B UI 面）在单 event loop 并起，
      import unilabos 通过、无阻塞消息循环、无端口硬编码冲突。
```

## 涉及模块

- **桥（OS 侧新增）**: `unilabos/app/local_bridge/`
  - `schedule_ws.py`（OS 面 WS 服务器 `/api/v1/ws/schedule`）
  - `workflow_ws.py`（实现 A UI 面 WS 服务器 `/ws/workflow/{uuid}`）
  - `local_api.py`（实现 B UI 面 HTTP 服务器 `/api/*`）
  - `workflow_to_dag.py`（共享翻译核：UI 图 → F002 `TaskDag`）
  - `server.py`（组合入口）
- **复用（不重写）F002**: `unilabos/scheduler/dag_model.py`（`TaskDag.from_message` + 环校验）、
  `dag_executor.py` + `task_dag_runner.py`（仅离线备档需要）。
- **前端**:
  - 实现 A：`Uni-Lab-Cloud/web/src/panels/WorkflowDAGPanel.tsx` + `WorkflowStepsPanel.tsx`（零改动，仅配 `NEXT_PUBLIC_WS_URL` 指向桥）。
  - 实现 B：`Uni-Lab-OS/unilabos_local_ui/`（已从 styxhuang fork 取 UI 框架层，不取专有设备栈）。

## 依赖关系

- 前置功能: **F002 完成**（`task_dag`/`job_status` 契约冻结、`TaskDag`/`DagExecutor`/`TaskDagRunner` 可用）。
- 外部依赖（真实硬件/服务）: 无——hermetic 测试用 fake OS 连接 + fake 节点调度器，不连真实设备、无 time.sleep。
  集成验证用 `unilab --test_mode`（模拟硬件），不需要 Go 后端 / Redis / Nacos / MQTT。

## 验证方法

- [ ] `python -c "import unilabos"` 通过
- [ ] `pytest tests/app/ tests/scheduler/` 通过（含桥的 hermetic 测试）
- [ ] `ruff check unilabos/app/local_bridge/ tests/` 全净
- [ ] 实现 B 自带 `npm run test`（local_ui 的 workflowDraft/workflowExport）通过
- [ ] AC-1~AC-6 逐条对照
- [ ] 桥三面契约在 interface-design.md 冻结，与 F002 `task_dag`/`job_status` 逐字段对齐

## 不做什么（Out of Scope）

- 不启动 / 不实现 Go 后端；桥只在本环境替代其"翻译面"职责。
- 不改 F002 任何执行逻辑（`DagExecutor`/`TaskDagRunner`/`ws_client._handle_task_dag`）。
- 不取 SZLab `szlab_poly_studio` 专有设备栈（OPC-UA/PLC/S01–S11，DP 专有许可）；只取 local_ui 框架层。
- 不改 uni-lab-cloud 的两个 panel 组件（仅环境变量配置）。
- 不做工作流的持久化存储 / 多用户 / 鉴权（本地联调工具，桥内存态即可）。
