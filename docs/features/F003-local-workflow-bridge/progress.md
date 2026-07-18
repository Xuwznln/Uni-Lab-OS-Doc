# 实现进度: 本地工作流桥（两套前端直连 OS 本地 DAG 执行，替代 Go 后端）

> **Author: CLAUDE** | 每完成一个子任务更新
> 行为规则见 docs/agent-workflow.md（退出协议 + 提交纪律）

## 当前状态

- 开始时间: 2026-07-18
- 最后更新: 2026-07-18
- 当前进度: 2/7 子任务完成（T01 翻译核 + T02 schedule_ws 已完成）
- 状态: T01–T02 完成；T03–T07 待续（T01–T07 依已批准计划拟定）

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
- 说明: 协议逻辑集中在 ScheduleSession（与真实 WS 传输解耦——send 协程注入、handle_incoming 喂入 OS 回来报文，便于 hermetic 测）。submit_dag 下发 {action:task_dag, data:serialize_task_dag(dag)}——serialize_task_dag 是 T01 build_task_dag_payload 的逆（纯 dataclass→dict，字段名严格 F002，无别名）；cancel_task 下发 {action:cancel_task, data:{task_id[,job_id]}}（对齐 ws_client._handle_cancel_action）；on_job_status 注册回流回调供 UI 面翻译；RunHandle 按 (task_id,node_id) 维护逐节点 NodeState（node_id==job_id），job_status.status→NodeState（running/success/failed/cancelled 值同名直映），全终态置 done.Event；任务级幂等（同 task_id 复用句柄、不重复下发，与 OS 侧 _handle_task_dag 一致）。ScheduleWSServer 薄壳：websockets.serve 绑 /api/v1/ws/schedule，延迟 import websockets（未装不拖累其余桥面），逐条 json.loads 喂 handle_incoming、send 经 json.dumps(ensure_ascii=False) 外发、EdgeSession 头取 session_id。tests/app/test_schedule_ws.py 10 用例覆盖 AC-2：F002 task_dag 报文逐字段、逐节点收敛、failed 终态、回调按序、cancel 报文+标 cancelled、幂等、host_ready 置位、未知 status 忽略、serialize 往返。沿用 F002 scheduler 的 asyncio.run 约定（不引 pytest-asyncio 配置）。pytest tests/app tests/scheduler 36 passed、ruff 净、import unilabos 通过。

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
