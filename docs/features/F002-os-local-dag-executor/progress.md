# 实现进度: OS 本地 DAG 执行器（整张工作流下沉边缘执行）

> **Author: CLAUDE** | 每完成一个子任务更新
> 行为规则见 docs/agent-workflow.md（退出协议 + 提交纪律）

## 当前状态

- 开始时间: 2026-07-18
- 最后更新: 2026-07-18
- 当前进度: 8/8 子任务完成
- 状态: 完成（T07 评审 HIGH 项已整改闭环；T08 对端契约已在 interface-design.md §三 冻结）

## 实现记录

<!-- 每完成一个子任务在此追加 -->

### T01: DAG 数据结构与 task_dag 解析
- 状态: completed
- 文件: unilabos/scheduler/dag_model.py, unilabos/scheduler/__init__.py
- 说明: DagNode/DagEdge/TaskDag/NodeState + TERMINAL_STATES；from_message 解析并校验（缺字段/重复 node_id/悬空边/含环均拒，Kahn 拓扑消解检测环 = I5）。device_action_key 与 ws_client._handle_job_start 一致。import 通过、ruff 通过。

### T02: DagExecutor 本地并发走图核心
- 状态: completed
- 文件: unilabos/scheduler/dag_executor.py
- 说明: 分两层解耦——DagWalk 纯同步状态机（ready/mark_running/on_success/on_failed/is_done + resume via completed），是 I1/I2/I5/I6 的靶子；DagExecutor 异步驱动，每轮提交全部 ready 节点并发起跑（asyncio.ensure_future + FIRST_COMPLETED），success 递减后继入度、failed 即 fail-fast 取消在跑并停止调度。同设备互斥不在此层（交注入的调度器）。on_node_terminal 回调预留给 T03 游标。自检 AC-1 通过、ruff 通过。

### T03: 本地持久化游标与 resume
- 状态: completed
- 文件: unilabos/scheduler/dag_persistence.py
- 说明: DagCursor(task_id/completed/inflight/failed) + DagCursorStore（目录可注入，mkstemp+flush+fsync+os.replace 原子写，防半写）。record_terminal(task_id,node_id,status) 可直接作 DagExecutor.on_node_terminal 回调。resume：DagWalk(dag, completed=cursor.completed) 重建 ready-set。自检 AC-4：崩溃后 completed=[A,B]、resume ready=[C]、仅跑 C,D、A/B 不重复。ruff 通过。

### T04: hermetic 单元测试（AC-1~AC-5）
- 状态: completed
- 文件: tests/scheduler/__init__.py, tests/scheduler/fake_scheduler.py, tests/scheduler/test_dag_executor.py
- 说明: fake_scheduler.py 建模生产 DeviceActionManager 关键契约——同 device_action_key 非 always_free 节点经每设备 asyncio.Lock 串行、绝不重叠（I3）；完成时刻由测试手动 complete() 驱动、settle() 用 asyncio.sleep(0) 零墙钟推进，确定性无 flaky。8 用例：AC-1 菱形 A→B/C→D 并发走图 + I1 恰好一次；AC-2 同 key 峰值并发=1（串行）+ always_free 峰值=2（放行）；AC-3 on_node_terminal 抛 ConnectionError（模拟断网上行失败）不打断走图、全节点仍 SUCCESS；AC-4 resume completed=[A,B] 不重跑 + 游标 roundtrip；AC-5a 某节点 FAILED 触发 fail-fast、D 绝不起跑；AC-5b 含环解析期即抛 DagValidationError。8 passed in 0.05s、ruff 通过、无 time.sleep。

### T05: property-based 不变量测试（I1~I6）
- 状态: completed
- 文件: tests/scheduler/test_dag_invariants.py
- 说明: Hypothesis legal_dags 生成器随机造 1~7 节点、边只从低号指向高号（构造性无环）、随机 device/always_free；@given + st.data() 抽随机合法调度驱动纯 DagWalk。3 用例：I1/I2/I6——每节点恰好运行一次 + ready 节点全部前驱必 SUCCESS + run_order 恒为合法拓扑序 + ≤节点数轮终止；I4——取整跑拓扑序任意前缀作崩溃游标 resume，只跑未完成、已完成不重跑、合计全 SUCCESS；I5——任意 i<j 加 j→i 回边构 2-环，解析期即 DagValidationError。I3（同设备无重叠）属注入调度器层，已由 T04 fake max_concurrent_by_key 覆盖，不重复。max_examples=200，3 passed、ruff 通过。依赖 hypothesis（已 uv pip install）。

### T06: ws_client 接入 _handle_task_dag（桥接层）
- 状态: completed
- 文件: unilabos/scheduler/task_dag_runner.py, tests/scheduler/test_task_dag_runner.py, unilabos/app/ws_client.py, unilabos/scheduler/dag_executor.py（CANCELLED 修正）
- 说明: TaskDagRunner 桥接 DagExecutor(submit->awaitable) 与生产回调/队列式执行栈——per-node asyncio.Future（node_id=job_id），_submit 先登记后触发 on_start_node 免竞态、await future；notify_terminal 经 loop.call_soon_threadsafe 跨线程解析；cancel() 停调度 + 未决 CANCELLED；run() 完成后任一非 SUCCESS 触发注入的 on_cancel_remaining 清理设备残余。ws_client 接线：分发新增 `elif message_type == "task_dag"`；_handle_task_dag 解析 TaskDag → 建 runner（loop=self._loop）→ 注册 → ensure_future 起跑（不阻塞消息循环）；_start_dag_node 把节点展开为等价 job_start payload 复用 _handle_job_start 全路径（DeviceActionManager 锁/幂等/send_goal）；_cancel_all_jobs_for_task 从原 cancel task_id 分支抽出，既服务外部 cancel_task 又作 on_cancel_remaining；publish_job_status 终态钩 notify_task_dag_terminal（非 DAG 任务 no-op，job_start 零影响）；cancel_task 命中 runner 即 runner.cancel()（设备侧由 on_cancel_remaining 统一清理，避免重复）。test_task_dag_runner.py：FakeStack 建模 ws 回调式队列栈，4 用例（菱形不同设备并发/同 device_action_key 串行/fail-fast 触发 cancel_remaining/外部 cancel 解析未决为 CANCELLED）。import unilabos 通过、pytest tests/scheduler 15 passed、ruff 净（唯一 F541 属既有无关代码）。

### T07: 验证与评审
- 状态: completed
- 文件: unilabos/app/ws_client.py（评审整改）, tests/app/__init__.py, tests/app/test_ws_job_start_deadpaths.py
- 说明: 验证信号全 PASS——`import unilabos` 通过；`pytest tests/scheduler/ tests/app/` 19 passed（15 scheduler + 4 死路径回归）；ruff 于本需求作者面（scheduler/ + tests/）全净，仅 ws_client.py:1291 既有无关 F541（HEAD 已存在）。AC-1~AC-6 逐条对照 requirement.md：AC-1 test_runner_diamond_concurrent + dag_executor AC-1；AC-2 test_runner_same_device_serialized + fake max_concurrent_by_key；AC-3 _notify_terminal try/except；AC-4 dag_persistence + I4；AC-5 fail-fast + 解析期拒环；AC-6 _start_dag_node 复用 _handle_job_start → publish_job_status 载荷逐字段未改（前端两 panel 零改动）。代码评审经 python-reviewer（contract-guardian/os-reviewer 在本环境不可用，契约评审直接对照 interface-design.md 完成），发现并整改 1 项 HIGH 悬挂缺陷（见下）。

## 遇到的问题

<!-- 问题与决策，尤其是硬件/时序/flaky 相关 -->

### T06 桥接层暴露 DagExecutor 对 CANCELLED 的误判（已修正 T02 文件）
- 现象：外部 cancel 时，未决节点 future 被解析为 CANCELLED 回流 DagExecutor.run，但原 run 循环把「任何非 SUCCESS」都走 `on_failed` → 节点落 FAILED 而非 CANCELLED（test_runner_cancel_resolves_pending 红）。
- 修正：dag_executor.run 区分三态——SUCCESS→on_success、CANCELLED→直接落 CANCELLED（不触发 fail-fast）、FAILED→on_failed + fail-fast；仅 `status == FAILED` 才取消在跑并中止。新增 `DagWalk.cancel_remaining()`：外部取消后把剩余非终态节点收敛为 CANCELLED，避免 run 返回含 PENDING/RUNNING 的非终态快照。T04/T05 全绿（fail-fast 语义未变，因失败仍走 on_failed）。

### T06 关键设计：回调式栈 ≠ awaitable，用 future 注册表桥接
- 生产执行栈（ws_client）是回调/队列驱动：_handle_job_start 入队 + send_goal 是**副作用**，终态经**另一线程**的 publish_job_status 回流；而 DagExecutor 要 `submit(node)->awaitable(NodeState)`。
- TaskDagRunner 桥接：per-node asyncio.Future（键 = node_id = job_id），_submit 先登记 future 再触发 on_start_node（避免瞬时跨线程回流早于登记的竞态），await future；notify_terminal 经 `loop.call_soon_threadsafe` 跨线程解析。同 device_action_key 排队节点的 future 悬挂至其各自终态——与 DeviceActionManager FIFO 串行（I3）天然组合，本层不复制互斥。
- 不阻塞消息循环：_handle_task_dag 用 `ensure_future(runner.run())` 起跑并立即返回，其间 cancel_task 仍可被 MessageProcessor 处理。

### T07 评审发现并整改：job_start 死路径不发终态 → DAG 节点永久悬挂（HIGH）
- 现象（python-reviewer/os-reviewer 视角）：`_handle_job_start` 有两条死路径提前退出且**不发 job_status**——(a) `HostNode.get_instance(0)` 为 None 时 `logger.error` 后直接 `return`；(b) 在构造 `queue_item` 之前抛异常（如 `JobAddReq` 解析或 `enqueue_job` 抛错），原 except 块 `if "req" and "queue_item" in locals()` 守卫为假 → 只 `logger.warning` 不上报。二者都使 backend/DAG 侧对应 job 永不收到终态；对 DAG 而言节点 future 永不解析 → `DagExecutor.run` 在 `asyncio.wait` 无限阻塞、`_task_dag_runners[task_id]` 泄漏。注意 line 820 的「排队」返回**不是**缺陷——该 job 稍后出队执行并正常发终态（同设备串行 I3 的 by-design pending）。
- 修正（根因层，同时惠及非 DAG 普通 job）：(a) HostNode 不可用分支补发 `publish_job_status(queue_item,"failed")` 再返回；(b) except 块在 `queue_item` 未构造时用 `req`（或 `data`）兜底构造 `QueueItem`，保证任一异常路径都发 "failed"。
- 修正（桥接兜底层）：`_start_dag_node` 改为 `ensure_future(_start_dag_node_guarded(...))`，包裹 `_handle_job_start`——仅当其抛出内部 try 未兜住的**逃逸异常**时才经 `notify_task_dag_terminal(...,"failed")` 判该节点失败；正常返回**不**解析（保排队节点 pending 至各自终态 = I3）。
- 回归：tests/app/test_ws_job_start_deadpaths.py 4 用例锁死——HostNode 缺失发 failed / enqueue 抛错经 fallback 发 failed / 桥接逃逸异常判 failed / 正常返回不误判。19 passed、ruff 净。

## 下一步建议

<!-- 供下一个 session 或人类参考 -->

- T07 已完成：验证信号全 PASS（import + 19 passed + ruff 作者面净）；AC-1~AC-6 逐条对照；python-reviewer 评审发现的 HIGH 悬挂缺陷已整改并加回归。契约评审直接对照 interface-design.md（contract-guardian/os-reviewer agent 本环境不可用）。
- T08 已完成（doc-only，跨仓不在本仓实现）：interface-design.md §三 已冻结对端清单——backend 新增 task_dag 下发（与逐节点 job_start 并存/灰度）、不改 OnJobStatus/JobData/cancel_task；前端复用 WorkflowDAGPanel(workflow-dag) + WorkflowStepsPanel(workflow-steps)，因上行契约不变零改动。
- 对端实施提醒：backend 侧新增 task_dag 序列化下发时，nodes 镜像 SendActionData、edges 用 source_node_uuid/target_node_uuid；node_id 即 job_id、幂等键 (task_id, node_id)。
