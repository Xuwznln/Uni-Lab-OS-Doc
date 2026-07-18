# 实现进度: 本地工作流桥（两套前端直连 OS 本地 DAG 执行，替代 Go 后端）

> **Author: CLAUDE** | 每完成一个子任务更新
> 行为规则见 docs/agent-workflow.md（退出协议 + 提交纪律）

## 当前状态

- 开始时间: 2026-07-18
- 最后更新: 2026-07-18
- 当前进度: 4/7 子任务完成（T01 翻译核 + T02 schedule_ws + T03 workflow_ws + T04 local_api 已完成）
- 状态: T01–T04 完成；T05–T07 待续（T01–T07 依已批准计划拟定）

## 落位

- 特性目录: docs/features/F003-local-workflow-bridge/
- 桥代码: unilabos/app/local_bridge/（待建）
- 实现 B 前端: unilabos_local_ui/（已从 styxhuang fork 取框架层）
- 复用 F002: unilabos/scheduler/{dag_model,dag_executor,task_dag_runner}.py（不重写）

## 实现记录

<!-- 每完成一个子任务在此追加 -->

### T01: workflow_to_dag 翻译核
- 状态: completed
- 文件: unilabos/app/local_bridge/__init__.py, unilabos/app/local_bridge/workflow_to_dag.py, tests/app/test_workflow_to_dag.py
- 说明: 两套 UI 唯一共享的业务逻辑。_node_to_f002 做别名归一——扁平（云端）节点 node_id/device_id/action/action_type/action_args 直取，嵌套（SZLab）{id, data:{method, deviceId, params}} 从 data 段取；_edge_to_f002 支持 source/target 与 source_node_uuid/target_node_uuid 两种边名。build_task_dag_payload 只做归一（产出严格 F002 字段名，无 UI 别名泄漏），workflow_to_task_dag 交 TaskDag.from_message 统一判定合法性——缺字段/重复 node_id/悬空边/含环的判定与报错信息与 F002 逐字一致（不重复实现校验）。tests/app/test_workflow_to_dag.py 8 用例覆盖 AC-1：扁平/嵌套归一、载荷字段名严格 F002、含环解析期拒（match "含环"）、悬空边拒、缺 device_id/task_id 拒、空 nodes 拒。8 passed in 0.9s、ruff 净、import unilabos 通过。

### T02: schedule_ws OS 面 WS 服务器
- 状态: completed
- 文件: unilabos/app/local_bridge/schedule_ws.py, tests/app/test_schedule_ws.py
- 说明: 协议逻辑集中在 ScheduleSession（与真实 WS 传输解耦——send 协程注入、handle_incoming 喂入 OS 回来报文，便于 hermetic 测）。submit_dag 下发 {action:task_dag, data:serialize_task_dag(dag)}——serialize_task_dag 是 T01 build_task_dag_payload 的逆（纯 dataclass→dict，字段名严格 F002，无别名）；cancel_task 下发 {action:cancel_task, data:{task_id[,job_id]}}（对齐 ws_client._handle_cancel_action）；on_job_status 注册回流回调供 UI 面翻译；RunHandle 按 (task_id,node_id) 维护逐节点 NodeState（node_id==job_id），job_status.status→NodeState（running/success/failed/cancelled 值同名直映），全终态置 done.Event；任务级幂等（同 task_id 复用句柄、不重复下发，与 OS 侧 _handle_task_dag 一致）。ScheduleWSServer 薄壳：websockets.serve 绑 /api/v1/ws/schedule，延迟 import websockets（未装不拖累其余桥面），逐条 json.loads 喂 handle_incoming、send 经 json.dumps(ensure_ascii=False) 外发、EdgeSession 头取 session_id。tests/app/test_schedule_ws.py 覆盖 AC-2：F002 task_dag 报文逐字段、逐节点收敛、failed 终态、回调按序、cancel 报文+标 cancelled、幂等、host_ready 置位、未知 status 忽略、serialize 往返。沿用 F002 scheduler 的 asyncio.run 约定（不引 pytest-asyncio 配置）。

### T03: workflow_ws 实现 A UI 面 WS 服务器
- 状态: completed
- 文件: unilabos/app/local_bridge/workflow_ws.py, tests/app/test_workflow_ws.py, unilabos/app/local_bridge/schedule_ws.py（回调改 async）
- 说明: 云端 panel（WorkflowDAGPanel/WorkflowStepsPanel）UI 面 WS 服务器。协议翻译集中在 WorkflowSession（传输无关——注入 send 协程 + 注入已就绪 ScheduleSession，喂 handle_incoming）。fetch_graph→回 build_demo_graph()：每节点带 uuid/id/node_id（三者相等，保证 job_id==node_id==panel node.id）+ device_id/action/action_type/action_args（供 workflow_to_dag 翻译）+ pose.position（供 handleNodesToWorkflowReactFlow 渲染），边用 source_node_uuid/target_node_uuid（同服务翻译与渲染）。run_workflow→demo 图经 workflow_to_task_dag 构 TaskDag 交 schedule.submit_dag（OS 面收严格 F002 task_dag），task_id 取 panel uuid（一图一任务，回流可按 task_id 命中），回 {code:0,data:{action:run_workflow,data:<task_id>}} ack。stop_workflow→schedule.cancel_task，回 {code:0,data:{action:stop_workflow}}。回流：WorkflowSession 于 schedule 注册唯一 async 回调 _on_os_job_status（按 self._task_id 动态过滤，避免多次运行累积回调），每条 OS job_status 经纯函数 translate_job_status_to_update 译成 {code:0,data:{action:workflow_update,code:0,data:{node_uuid(==job_id),job_status,task_status,header,msg}}}——task_status 仅当整张 DAG 全终态（RunHandle.finished，回调在 apply_status 后触发故末条已 True）时为 end 否则 running，对齐 panel setNodeExecutedExecutor 逐节点更新 + task_status=end 清 taskId 的语义。为使下行推送时序可判定（而非 asyncio.ensure_future 的非确定序），把 schedule_ws._on_job_status 改为 async 并 await inspect.isawaitable 回调结果（向后兼容既有 sync lambda——T02 用例仍全绿）。WorkflowWSServer 薄壳：延迟 import websockets 绑 /ws/workflow/，get_schedule_session 解析已就绪 ScheduleSession、路径取 uuid。tests/app/test_workflow_ws.py 10 用例覆盖 AC-3：fetch_graph 渲染就绪（uuid/pose.position/边名）、run 下发逐字段 F002 task_dag + ack、job_status→workflow_update 逐字段（node_uuid==job_id）、task_status 仅全终态为 end、failed 达 end、非本会话 task_id 忽略、stop→cancel_task+确认、纯函数形状（running/end 切换）、demo 图翻译合法 TaskDag、_extract_uuid 路径解析。pytest tests/app tests/scheduler 46 passed、ruff 净、import unilabos 通过。

### 修复：feature-list.json 结构损坏
- T02 完成时的编辑误删了 T02 对象闭合与 T03 的 "id": "T03" 行，致 JSON 非法（两对象并成一个）。本轮修复：还原 T02 `},` 闭合 + T03 `{ "id":"T03" ...`（id/name/layer/description 逐字还原人类原文，未改定义），并顺带将 T03 status→completed。python json.load 校验通过，7 个任务 id 齐全。

### T04: local_api 实现 B UI 面 HTTP 服务器
- 状态: completed
- 文件: unilabos/app/local_bridge/local_api.py, tests/app/test_local_api.py
- 说明: 云端桥的实现 B（SZLab unilabos_local_ui）UI 面 FastAPI HTTP 服务器。协议翻译集中在 LocalApiState（传输无关——注入已就绪 ScheduleSession，在其上注册唯一 job_status 回调，把 OS 回流按 task_id(==run_id) 路由到 RunRecord 并累积结构化 log_events；RunHandle 逐节点态由 schedule_ws._on_job_status 在回调前更新，此处只追加日志）。node_statuses_of/overall_status_of 为纯映射函数：NodeState→NodeRunStatus（PENDING→idle/READY→preparing/RUNNING→running/SUCCESS→success/FAILED→failed/CANCELLED→cancelled，对齐 main.tsx NodeRunStatus 字面量）；run 整体态 pending/running/completed/failed/cancelled（未全终态有 running 则 running 否则 pending；全终态任一 failed→failed，否则任一 cancelled→cancelled，否则 completed，对齐 pollRun 终态集）。build_graph 交 workflow_to_task_dag 走 F002 解析校验（含环抛 DagValidationError→路由转 400 detail），通过则回显归一 {name,nodes,edges}；start_run 构 TaskDag(task_id=uuid4().hex) 交 submit_dag（OS 面收严格 F002 task_dag），建 RunRecord 返回 RunStatus；get_run/cancel_run（下发 cancel_task）。node_statuses 以 node.id 为键——F002 node_id==local_ui node.id，故 applyNodeStatuses 命中。build_demo_preset 与 workflow_ws.build_demo_graph 的设备/动作对齐（pump_1/pump_liquid、stirrer_1/stir），default_config 镜像 local_ui DEFAULT_CONFIG；build_stack_status 返回 success 空堆栈。create_app 延迟 import fastapi（未装不拖累其余桥面），6 路由只做请求解码+调 LocalApiState+错误转码——OS 未连入 503、未知 run 404、含环 400 detail；LocalApiServer uvicorn 薄壳（延迟 import）。tests/app/test_local_api.py 12 用例（FastAPI TestClient 同步驱动 + 内存 ScheduleSession→FakeTransport 顶替真实 WS 传输，请求之间以 asyncio.run(schedule.handle_incoming(...)) 喂回 job_status——纯内存态更新，无 time.sleep/真实 OS/网络）覆盖 AC-4：preset/stack-status 形状、build-graph 往返、含环 400 detail、run 下发 F002 task_dag + 全 idle 态、轮询随 job_status 推进至 completed、failed 达 failed、cancel 发 cancel_task+标 cancelled、未知 run 404、OS 未连入 503（preset/stack-status 仍可用）、纯函数映射、demo preset 设备对齐。pytest tests/app tests/scheduler 58 passed、ruff 净、import unilabos 通过。

## 遇到的问题

<!-- 问题与决策，尤其是硬件/时序/flaky 相关 -->

### 为什么需要桥（架构决策）
- 本环境 Go 后端不可用（Redis/Nacos/MQTT/Docker 均缺），全栈 E2E 不可行。
- OS 的 ws_client 主动外拨 ws(s)://<host>/api/v1/ws/schedule 连后端；桥只需当此路径的 WS 服务器让 OS 连入。
- 两套前端协议（云端 /ws/workflow 与 SZLab /api/*）都不被上游 OS + F002 原生服务，故都需桥翻译。
- 桥不复制执行逻辑：翻译协议 → 交 F002 真实 task_dag 路径 → 翻译回流 job_status。单一事实源。

## 下一步建议

<!-- 供下一个 session 或人类参考 -->

- 待人类确认 feature-list.json 的 T01–T07（Claude 依批准计划拟定，按 harness 规则新增任务条目需人类确认）。
- 确认后按 id 序开工：T01 workflow_to_dag 翻译核（复用 F002 环校验）→ T02 schedule_ws → T03 workflow_ws → T04 local_api → T05 server.py → T06 接线 local_ui → T07 集成验证。
