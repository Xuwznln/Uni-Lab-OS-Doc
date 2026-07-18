# 实现进度: OS 本地 DAG 执行器（整张工作流下沉边缘执行）

> **Author: CLAUDE** | 每完成一个子任务更新
> 行为规则见 docs/agent-workflow.md（退出协议 + 提交纪律）

## 当前状态

- 开始时间: 2026-07-18
- 最后更新: 2026-07-18
- 当前进度: 6/8 子任务完成
- 状态: 进行中（T06 完成，进行 T07）

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

## 遇到的问题

<!-- 问题与决策，尤其是硬件/时序/flaky 相关 -->

### T06 桥接层暴露 DagExecutor 对 CANCELLED 的误判（已修正 T02 文件）
- 现象：外部 cancel 时，未决节点 future 被解析为 CANCELLED 回流 DagExecutor.run，但原 run 循环把「任何非 SUCCESS」都走 `on_failed` → 节点落 FAILED 而非 CANCELLED（test_runner_cancel_resolves_pending 红）。
- 修正：dag_executor.run 区分三态——SUCCESS→on_success、CANCELLED→直接落 CANCELLED（不触发 fail-fast）、FAILED→on_failed + fail-fast；仅 `status == FAILED` 才取消在跑并中止。新增 `DagWalk.cancel_remaining()`：外部取消后把剩余非终态节点收敛为 CANCELLED，避免 run 返回含 PENDING/RUNNING 的非终态快照。T04/T05 全绿（fail-fast 语义未变，因失败仍走 on_failed）。

### T06 关键设计：回调式栈 ≠ awaitable，用 future 注册表桥接
- 生产执行栈（ws_client）是回调/队列驱动：_handle_job_start 入队 + send_goal 是**副作用**，终态经**另一线程**的 publish_job_status 回流；而 DagExecutor 要 `submit(node)->awaitable(NodeState)`。
- TaskDagRunner 桥接：per-node asyncio.Future（键 = node_id = job_id），_submit 先登记 future 再触发 on_start_node（避免瞬时跨线程回流早于登记的竞态），await future；notify_terminal 经 `loop.call_soon_threadsafe` 跨线程解析。同 device_action_key 排队节点的 future 悬挂至其各自终态——与 DeviceActionManager FIFO 串行（I3）天然组合，本层不复制互斥。
- 不阻塞消息循环：_handle_task_dag 用 `ensure_future(runner.run())` 起跑并立即返回，其间 cancel_task 仍可被 MessageProcessor 处理。

## 下一步建议

<!-- 供下一个 session 或人类参考 -->

- T07：`import unilabos` + 全量 `pytest tests/scheduler/` + `ruff check`（注意 ws_client.py 存在**既有无关** F541，非本需求引入）；AC-1~AC-6 逐条对照；contract-guardian 评审 task_dag 下行契约 + os-reviewer 评审桥接层。
- T08：interface-design.md 冻结对端清单——backend 新增 task_dag 下发（与逐节点 job_start 并存/灰度）、不改 OnJobStatus/JobData/cancel_task；前端两 panel（workflow-dag / workflow-steps）因上行契约不变零改动。
