# 前端与 Edge 错误处置流程核查

> 历史验证记录：下文结论对应当时的旧独立前端。当前内置控制台已接入全局错误处置，入口与验证方式见 [当前实现说明](edge-error-handling-implementation.md)。

日期：2026-09-10。业务代码基线：Uni-Lab-OS `d0aa7ff2`，uni-lab-fe `00af79bf`。本轮补充联调用例，未修改业务实现。仅核查 Python Edge，不涉及已废弃的 Go 调度实现。

## 结论与阻断

**当前默认 Theia Workbench 尚不能完成该功能的用户操作流程。** 错误处置组件自身具备对应能力，但产品入口遗漏了 Workbench。

证据：

- `uni-lab-fe/package.json:9`：`pnpm dev` 执行 `pnpm run workbench`。
- `apps/kernel-web/src/App.tsx:26`：挂载了 `StationErrorHandling`。
- `packages/workbench-theia/src/browser/unilab-workbench-widget.tsx:1259`：Workbench 自己组合设备、物料、机器人和工作流页面，没有使用 kernel-web 的 App。
- 同文件 `:1350` 的领域页面组合及整个 Workbench 源码均未挂载 `StationErrorHandling`，也没有另一套调用工站处置接口的页面。
- `packages/workbench-theia/src/browser/workbench-connection-profile.ts` 的本地连接使用 `local-python`；该配置已具备 `workflow.errorHandling` 能力。因此缺的是页面接入，并非 Edge 缺少这组接口。

影响：从默认工作台运行任务，Edge 可以因节点失败暂停普通调度，工作流页面也可以展示等待错误处置，但用户无法通过本功能的三选项弹窗进入手动处理、决定当前节点并恢复工站。kernel-web 的挂载和独立组件联调不能证明 Workbench 已接入。

修复应在 Workbench 全局组合位置挂载处置入口，复用它已经选择的 Services 和本地权威连接，保证切换设备/物料/工作流页面后仍可继续处置，并在切换权威时隔离会话与未完成请求。随后应使用完整 Workbench 页面重新验收；不能通过独立测试页替代这一项。

## 本轮联调范围

新增 `uni-lab-fe/e2e/station-error-complete-flow.spec.ts`，通过浏览器点击真实处置组件和 Services，连接真实 FastAPI、EdgeScheduler、TaskSchedulerBridge、WorkflowStore、InventoryStore、JobExecutionBackend 和 DeviceActionManager。仅物理 Host 被受控替身替代：它在调用真实开始屏障后记录设备调用，通过执行器原有回报入口提交结果，取消受理与取消终态分别回报。

普通单动作 task 使用产品 HTTP 创建接口。三节点依赖链通过 Store 的任务工厂准备冻结任务，再提交真实调度桥；这一用例验证运行期 DAG 处置，不验证工作流编辑和创建页面。

追踪使用真实 OpenTelemetry SDK、内存导出器和产品上下文传播实现，不连接外部 Collector。断言要求 source/manual trace 非空且相同、span 非空且不同。原有浏览器测试仅比较 `trace_id` 相等，可能让两个空值通过；因此该旧断言单独不足以证明追踪链路。

| 场景 | 关键验证 |
| --- | --- |
| 初始三选项直接重试 | 不创建手动会话；创建新 attempt；保留原失败 job；等待 task 恢复并完成 |
| 初始三选项取消 | task 进入 canceled；原失败记录保留；其他 task 继续 |
| 暂停与在途动作 | 新普通任务不开始；已开始的另一设备动作继续回报成功 |
| 手动执行被占用设备 | 同设备同动作已有在途执行时仍能开始手动动作；参数实际送达 Host；来源 task 正确 |
| 完成后取消与恢复 | 手动动作仍在途时允许用户确认恢复；会话最终选择阶段刷新后仍可恢复 |
| 人工上下料与释放占用 | 上料/卸料结果回读正确；仅释放选中占用；释放不会伪造在途设备已停止 |
| 停止手动动作 | 停止请求只针对所选命令；收到真实取消终态前保持 cancel_requested；收到后为 canceled |
| 返回手动处理 | 保持同一个会话，不重复创建 |
| 单独恢复与新错误 | 重试可继续保持全站暂停；用户独立确认恢复；后来出现的新错误再次暂停 |
| 三节点链中间失败后重试 | 前置成功节点不重跑；失败 job 不被覆盖；仅当前节点新增 attempt；后继在重试成功后执行；其他 task 完成 |
| 三节点链中间失败后取消 | 已成功/失败记录保留；后继取消且没有发送到设备；其他 task 完成 |

浏览器回归另保留原有记录器主流程，以及 4 项 HTTP/SSE 故障注入测试。故障注入测试用于验证页面恢复能力，不能替代真实 Edge 联调。

## 执行结果

Python 定向回归：17 项通过，覆盖 `test_local_execution_pause.py`、`test_error_recovery.py`、`test_workflow_deferred_error_handling.py`、`test_error_control.py` 和 `test_error_inventory.py`。日志：`/tmp/edge-flow-python.log`。

浏览器最终回归：**13 项全部通过，48.1 秒**。其中 8 项连接真实本地执行器，1 项为原有记录器主流程，4 项为 HTTP/SSE 故障注入。最终日志：`/tmp/edge-flow-final-verified.log`。该次运行包含真实且非空的 trace/span 校验、三节点链的重试和取消两条分支，浏览器未报告 `pageerror` 或非预期 `console.error`。

测试辅助 Python 通过 Ruff 的 F 规则检查；变更通过 `git diff --check`。**以上结果证明处置组件与 Edge 的软件链路，未消除 Workbench 缺少挂载的阻断。**

复现命令（前端仓库根目录）：

```bash
UNILAB_OS_ROOT=/path/to/Uni-Lab-OS \
UNILAB_OS_PYTHON=/path/to/python \
pnpm exec playwright test --config e2e/station-error.playwright.config.ts
```

## 尚未验收

- 完整 Theia Workbench 的自动弹窗、设备/工作流页面切换及整条用户操作链路：受上述挂载缺口阻断。
- 真实 ROS2 Host、设备驱动、现场设备执行和停止回执：本轮没有执行现场设备动作。
- 本轮没有重新运行全仓构建/所有测试；业务实现未变，验证集中于真实调用链和新增运行期联调。
