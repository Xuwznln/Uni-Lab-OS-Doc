# Edge 节点错误与手动处置实现

设计来源：[节点错误处置方案](https://dptechnology.feishu.cn/wiki/P799w0gQZibfgvk1IhBc2grmnPg)。实现范围为 Python EdgeScheduler、本地 WorkflowStore、Edge 执行协议与现有前端。

**当前前端（2026-09-10）**：`Uni-Lab-OS/frontend` 已在 `src/App.tsx` 挂载全局错误处置。入口、设备单点动作、上下料登记、逻辑占用与恢复均直接调用本地 Python Edge API。本文后面的 kernel-web、Theia、workflow-editor 测试数量属于旧独立前端的历史记录，不能作为当前目录的验证证据。当前前端测试命令为 `cd frontend && npm test -- --maxWorkers=2`；生产构建为 `npm run build`，输出到 `unilabos/app/web/static/console`。

## 使用流程

1. 普通节点失败或执行结果未知，Edge 暂停工站的新普通动作；已实际开始的动作继续回传结果。
2. 全局处置弹窗提供取消当前任务、重试当前节点、进入手动处理。只有第三项创建处置会话。
3. 手动处理中选择设备及其动作，按动作 Schema 填参执行。手动动作不检查逻辑锁、占用或 UNKNOWN 条件。每次操作形成独立任务与执行命令，并关联来源 task/job/session、原 task trace 和新的执行 span。
4. 可登记人工上下料位置及释放选中的逻辑占用。库存修改保存原事实版本与操作者；逻辑释放不表示设备已停止。
5. “处理完成”撤销能够证明尚未开始的手动命令，回到同一会话的节点决定阶段，工站仍暂停。已开始和 UNKNOWN 动作保留真实执行及停止入口。
6. 最终选择重试当前节点或取消当前任务，可显式确认同时恢复工站。已知的其他错误、在途动作和 UNKNOWN 不作为人工恢复的限制条件；未决源任务自身仍等待决定。新错误再次暂停工站。

## 执行与恢复约束

- 实际开始屏障位于设备驱动调用之前；仅接收 ROS Goal 不标记已经开始。JSON ResourceSlot 解析完成后才取得开始许可，异步驱动在自身事件循环实际调用前取得许可。
- 重试新增 attempt 和独立命令 ID，沿用冻结节点合同、输入及已有前置结果；旧 job 与回执保留。停止操作精确指向一条执行命令。
- 同一错误/未知事实去重；旧 UNKNOWN 的迟到结果更新执行证据，不替代人的节点决定、不覆盖新 attempt，也不覆盖较新的人工库存位置。
- 工站版本、错误代次、决定版本、会话版本与幂等键共同约束并发提交。已经持久受理的命令如果应用失败，页面展示错误并允许按原 command UUID 再次应用。
- 人工等待期间的 task pause/resume 不能抹去 error_waiting。处置决定生效后按用户当前的 normal/step 模式恢复任务控制状态。
- **执行 Runtime 崩溃或更换进程后，全部活动 task 统一失败**，包括运行、取消中以及已进入调度但正在等待的 task。已开始/未知的 job 失败；未开始 job 取消；已完成节点的原结果保留。旧 task 不继续、不重试、不恢复未开始命令，不创建重启对账。
- 旧错误决定、手动会话和未应用控制意图终结。手动动作的执行 task 适用相同规则。跨库软件占用清理可幂等重试，完成后允许新 task 调度；这不是恢复旧 task，也不代表物料位置已经核实。
- 仅网络断连、页面刷新或执行进程仍存活时的调度器重建保留原事实与开始屏障；不能把它们当作 Runtime 崩溃。此时取消等待沿用原回执截止时间，不重复发送停止。真实新错误仍触发暂停。
- 同一个 Fence 的共享逻辑占用人工释放后，同步更新持久记录与所有参与 job 的内存占用；晚到结果不能重新建立该占用。设备托管释放绑定原托管 UUID，避免旧卸载结果释放后来重新取得的设备托管。

## 数据与接口

仅新增三张控制表：`workflow_station_control`、`workflow_station_control_command`、`workflow_error_session`。错误决定、任务、job、反馈、结果、资源占用、设备托管和库存事件复用现有表。既有表的字段/索引升级随本地 Store 初始化执行。

接口前缀：`/api/v1/stations/local`。

| 用途 | 接口 |
| --- | --- |
| 权威快照 | `GET /error-handling` |
| 错误三选项 | `POST /errors/{decision_id}/decision` |
| 会话读取/完成/返回 | `GET /sessions/{id}`、`POST /sessions/{id}/complete`、`POST /sessions/{id}/return` |
| 设备动作目录 | `GET /sessions/{id}/devices/{device_id}/actions` |
| 手动执行 | `POST /sessions/{id}/manual-actions` |
| 执行详情/停止 | `GET /manual-actions/{command_id}`、`POST /manual-actions/{command_id}/stop` |
| 控制命令查询/应用 | `GET /control-commands/{id}`、`POST /control-commands/{id}/apply` |
| 人工恢复 | `POST /resume` |
| 库存读取/更正 | `GET /sessions/{id}/inventory`、`POST /sessions/{id}/inventory-corrections` |
| 占用读取/释放 | `GET /resource-dispositions`、`POST /resource-dispositions` |

产生新意图的写操作携带 `Idempotency-Key`；状态相关操作携带预期版本。HTTP 受理与命令实际应用、设备实际执行分别展示。前端通过 `frontend/src/lib/stationErrorClient.ts` 访问接口，全局 SSE 只触发 REST 权威快照重读。

## 代码入口

- 控制状态与幂等：`unilabos/workflow/error_control.py`
- 单动作应用服务：`unilabos/workflow/error_handling.py`
- HTTP 适配：`unilabos/app/error_handling_api.py`
- DAG 与物理执行协调：`unilabos/workflow/task_scheduler_bridge.py`、`unilabos/app/scheduler/service.py`
- 实际开始屏障：`unilabos/app/edge_control/execution_start.py`、`unilabos/ros/nodes/base_device_node.py`
- 前端：`frontend/src/hooks/useStationErrorHandling.ts` 管理权威快照、事件刷新与幂等请求；`frontend/src/components/StationErrorHandling.tsx` 负责全局三选项和恢复；`ManualHandlingPanel.tsx`、`ManualActionParameters.tsx`、`HandlingInventoryPanel.tsx` 负责手动操作，由 `frontend/src/App.tsx` 挂载。

## 旧独立前端验证方式（历史记录）

Python 关键回归覆盖 scheduler_core、错误控制、人工库存、设备单动作 API、设备托管、Edge 协议与本地执行权威。最初的浏览器测试单独挂载 `StationErrorHandling`，连接真实 Python API/EdgeScheduler/WorkflowStore/InventoryStore，使用只记录命令的物理执行适配器；它没有加载完整的 kernel-web 或 Theia Workbench。后续增加真实 `JobExecutionBackend` 与受控 Host 的联调，详见上述流程核查。

在前端仓库运行：

```bash
UNILAB_OS_ROOT=/path/to/Uni-Lab-OS UNILAB_OS_PYTHON=/path/to/python \
  pnpm exec playwright test --config e2e/station-error.playwright.config.ts
```

该用例覆盖三选项、全局暂停、十动作目录填参、会话刷新恢复、来源 trace、处理完成后原地选择、原节点重试及继续多 task。物理 ROS2 环境需要另行运行 `tests/ros/nodes/test_base_device_json_resource_slots.py` 并完成设备联调；本地 Python 环境缺少 `rclpy`，不能据此声称实机链路已经验收。

## 第一版验证记录（2026-09-10）

以下为第一版的历史结果。后续复核发现的问题及本轮修复验证见下一节；第一版测试通过不代表真实启动、全部执行模式和设备联调均已验收。

| 检查 | 结果 |
| --- | --- |
| 本次改动涉及的 Python 测试（不含 ROS 环境测试） | 309 项通过；2 项已在干净基线复现的旧失败单独排除 |
| 50 task × 8 job 压力用例 | 400 节点完成，无重复派发；单独复测通过，耗时约 57 秒 |
| workflow-editor 完整单测 | 56 个文件、371 项通过 |
| 本功能真实 OS 浏览器流程 | 1 项通过，未使用接口路由 mock |
| Web / Desktop 构建 | 两项通过 |
| 新增 Python 控制、应用、API、开始屏障模块 | Ruff 检查通过；语法检查通过 |
| 类型检查 | services、workflow-editor、kernel-web 等通过；desktop 被原有类型错误阻断 |
| 双轴代码复核 | 需求与规范复核所发现的问题均已处理；取消回执竞态补有双线程回归 |

全量 Python 测试首次运行有 2957 项通过、96 项失败、43 个收集错误。将失败用例与干净 `6da5d66e` 基线逐项对照后，52 项失败和全部 43 个收集错误在基线仍可复现；另外 44 项已修复或按新产品规则更新，并包含在上述最终回归中。没有把未通过的全量检查记作通过。

仓库既有阻断包括：ROS2/消息包及部分可选依赖缺失；废弃运行模式相关旧测试；desktop 的 `preload/index.ts` 调用了 `ManagedRuntimeInstallationApi` 尚未声明的 `chooseEnvironment`；前端全仓质量检查存在其他文件的复杂度、体积及样式违规。本次新组件已通过相应检查，原有工作流浏览器测试还被其 OS fixture 导入缺失的 `RESOURCE_TEMPLATE_UUID` 阻断。

真实设备动作没有在本次实现过程中执行。上线前仍需在完整 ROS2 与设备环境验证实际开始屏障、设备停止支持和真实回执。上述测试覆盖的是软件控制合同与本地 API 集成。


## 再次复核与修复（2026-09-10）

本轮修复再次复核确认的五项问题，并处理修复复核中发现的持续事件等待问题：

1. **延迟启动**：工站错误处置路由在挂载时不再读取未发布运行时。请求级依赖在运行时就绪前返回 503，发布后同一路由立即读取权威工站状态；无需动态重挂路由。
2. **默认单进程执行**：`JobExecutionBackend` 绑定既有工站控制器，入队不再标记 `START_COMMITTED`。命令携带来源与实际开始 guard，设备调用前确认开始；暂停前已排队的普通动作等待，已开始动作继续结算。恢复只补缺失且证明未开始的原命令，不重复发送已交给 Host 的命令。手动动作经过身份验证后绕过同设备同动作队列，不引入资源锁检查。
3. **循环重试重启**：当前 attempt 的恢复沿 `retry_of_job_uuid` 查找已确认循环决定，保留完成轮次和其结果，不把旧 attempt 的终态赋给新 attempt。冻结循环上限仍不满足时重新报告该节点错误，其他任务仍可按人的恢复决定运行。
4. **事件重放**：快照请求串行合并；一批 100 条事件不再创建 100 个并发请求，也不丢弃所有较早成功响应。每次操作只等待覆盖自己请求代次的一轮读取，后续持续反馈继续后台刷新，不会让按钮一直 busy。端点切换后旧读取不能发布到新页面。
5. **目录重试**：设备、动作、库存分别保留加载错误并提供“重试加载”。只有对应读取成功才清除该项提示，不再由刷新工站状态掩盖目录错误；重试保留用户已经填写的动作参数。

队列模型与 `DeviceActionManager` 提取到 `unilabos/app/job_queue.py`，`ws_client.py` 保留兼容导出，两条执行链仍共用一个实现。这样本地执行器的合同测试可直接导入真实实现，而不必加载 ROS；实际 Host/设备调用仍在 ROS 环境执行。

本轮定向验证：Python 44 项通过（43 项定向回归 + 1 项库存结果重放，另排除需要 ROS 消息包的 Host 单例测试）；workflow-editor 原有 371 项通过，新增持续事件读取顺序测试 2 项通过；浏览器故障注入 4 项通过；真实 Python API/EdgeScheduler 的完整浏览器流程 1 项通过。单进程专项覆盖：暂停队列、Host 投递与驱动调用间发生新错误、已开始动作继续回报、开始前取消、同设备同动作在途时手动动作仍可开始。

需求与规范两轴复核均确认本轮问题闭合：Spec 剩余 0 项，Standards 剩余 0 项。

| 本轮检查 | 结果与边界 |
| --- | --- |
| Python 全量 | 3073 通过、51 失败、37 收集错误、12 跳过、1 xfailed；没有记作全量通过 |
| Python 失败比对 | 47 项与此前干净基线已确认失败一致；3 项因缺少 ROS 依赖；另 1 项库存重放旧用例补上真实 `/begin` 协议步骤后单独通过；37 个收集错误均已在基线出现 |
| 前端全仓单测 | services 207、workflow-editor 371、kernel-web 74 等通过；全仓命令最终被 Workbench 的原有启动脚本断言阻断，断言及其读取的脚本与修复前提交内容一致 |
| 最后一次前端新增回归 | 持续事件单测 2 项、HTTP/SSE 故障注入浏览器测试 4 项、真实 OS 浏览器流程 1 项均通过 |
| 类型检查 | 修改后的 workflow-editor 通过；全仓仍被 desktop 原有 `chooseEnvironment` 类型错误阻断 |
| 质量检查 | 相对本轮基线 `f3e01b66` 的前端生产代码检查通过；StationErrorHandling 537 行，超过建议 500 行但未超过登记/硬限制，保持现有界面组合结构；新增 Python 模块与回归通过 Ruff，修改模块语法检查通过 |
| 全仓质量/样式 | 仍存在其他既有模块的体积、复杂度及 robot-workstation `!important` 违规，本轮未扩改这些模块 |
| 最终版本 Web / Desktop 构建 | 两项均通过，包含持续事件等待修复 |

本轮没有重复执行全量 Python，只对更新的库存重放用例补跑，确认数量消费只结算一次的断言仍保留。真实 ROS2 设备动作未执行，软件回归不能替代实机验收。

## Runtime 崩溃规则调整（2026-09-10）

此版本取代此前的“Runtime 重启后恢复原任务并核对未知执行”。单进程入口在启动时终结运行库中的遗留任务；分离部署在执行进程 UUID 更换时，由 EdgeScheduler 统一终结全部活动任务，而非仅处理 HELLO 列出的 job。程序已崩溃时无法当场写库，失败事实在控制端确认进程重启或本地重新启动时提交。

软件执行 Claim、Lease、等待队列及库存 Permit 释放；库存位置、数量、设备托管等已确认事实保留，不合成停止证明或物料转运结果。旧不确定原因归档到 job 控制证据，终结后的旧回执不能覆盖结果或重新取得开始许可。无需用户完成对账才能终结这些 task。若还要执行相同实验，需要创建新 task。

失败终结与资源清理之间的中断，通过 pending 清理状态重试；清理失败期间关闭开始入口。分离执行端同时保留未完成的重启通知标记，重连不会漏掉失败的终结操作。命令传输过滤已终结 job 的旧命令，执行端启动时退役旧镜像，不生成 UNKNOWN 回执。

现有临时运行库/保留运行库选项不变：只有实际保留的运行库能展示跨进程任务历史。独立执行 Runtime 重启时，仍存活的工站调度进程继续保留任务记录。

本次验证：定向 Python 回归 379 项通过，另有 1 项既有地址配置断言失败后单独排除（`test_edge_control_settings_derives_split_scheduler_address`，对应 `EdgeControlSettings.from_config` 与 HEAD 一致）；最终复核修正迟到事件与旧命令退役边界后，相关 126 项复测通过。浏览器 14 项通过，其中新增“Runtime 崩溃关闭会话、全部旧活动 task 失败、未开始节点取消、刷新不恢复旧动作”的真实 Python API 联调。Ruff F 检查及两仓 `git diff --check` 通过。浏览器使用受控 Host 模拟执行进程退出事实，没有执行真实设备动作，也未把既有 Workbench 组件挂载缺口视为已修复。

飞书原文已同步更新第 11 节、已有能力复用说明、R4/R8 评审项、交付阶段和 A18 验收矩阵；回读版本为 160，旧 Runtime 对账恢复策略已替换。

## 当前 frontend 联调补充（2026-09-10）

`frontend/src/components/StationErrorHandling.test.tsx` 覆盖三选项、原请求幂等重发、断线禁写、过期恢复确认、手动动作参数、完成后保持暂停、多错误人工恢复、库存位置版本和逻辑占用版本。最终定向交互测试 11 项通过；前端全量测试曾以 2 个 worker 运行，293 项通过。生产构建与类型检查通过。

在本地 Kubernetes 的真实 Python EdgeScheduler 和 ROS2 Runtime 上，通过 `/console/` 页面验证了失败自动弹窗、进入人工处置、软件探针 `observe_boolean(value=true)` 执行成功、来源 Task Trace 一致、处理完成仍暂停、原节点 attempt 2 成功且前置未重跑。设备动作验证范围为软件探针；未执行真实硬件动作，也未修改现场库存。

取消分支联调发现同一 Job 多错误事实版本冲突：表约束是 `(workflow_node_job_uuid, revision)`，原先状态转换直接对单条记录 `revision + 1`。现统一从该 Job 的全部记录分配下一版本，取消时逐条关闭相关错误，手动进入/完成/返回、重试收尾、Runtime 重启清理共用同一规则。两个回归用例先复现相同 SQLite 唯一键错误，修复后通过；工站控制、Runtime 重启、延迟启动、库存与事件定向回归共 15 项通过。
