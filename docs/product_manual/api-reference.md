# API 使用参考

:::{admonition} 阅读角色
- **业务负责人**：提供使用场景和预期结果，不直接操作调试接口或恢复命令。
- **开发或运维人员**：使用开发工具、接口和日志定位并处理问题。
- **验收人员**：确认权限、输入输出、异常处理和操作记录符合要求。
:::

API 是 Uni-Lab OS 网页、Workbench 和自动化工具共用的产品能力入口。日常实验优先使用 Uni-Lab OS 页面；批量导入、设备单动作、调度运维和系统集成再使用 API。

## 入口

当前演示环境可直接打开以下入口；部署到其他环境时，将示例地址替换为实际的 `<BACKEND_URL>`：

| 入口 | 地址 |
| --- | --- |
| Swagger | [打开 Swagger UI](http://115.190.137.109:30183/api/docs) · `<BACKEND_URL>/api/docs` |
| OpenAPI JSON | [查看 OpenAPI JSON](http://115.190.137.109:30183/api/openapi.json) · `<BACKEND_URL>/api/openapi.json` |

:::{warning}
当前演示 API 没有通用用户认证。不要把公网地址、真实 UUID 或示例请求直接用于生产自动化。生产集成必须经过受控网关、HTTPS、身份认证和最小权限授权。
:::

## 调用约定

### 同时检查 HTTP 与业务码

健康接口直接返回状态对象；多数业务接口返回 envelope。部分业务错误仍可能使用 HTTP 200，因此客户端必须同时检查：

- HTTP 状态；
- 响应 envelope 中的业务 `code`；
- `data` 或 `error.msg`；
- 对异步恢复操作，是否返回 HTTP 202。

### 写操作使用幂等身份

库存命令、任务命令和人工恢复可能要求 `Idempotency-Key`、命令 UUID 或 resolution UUID。同一次逻辑操作重试时必须复用原身份；生成新身份可能创建第二次业务操作。

### 更新使用期望修订

物料、试剂、工作流草稿和恢复操作会使用 `expected_revision`、claim/fencing 或其他比较并交换字段。发生冲突时重新读取最新对象再决定，不要改大修订号或移除约束。

### SSE 是变化通知

`/api/v1/events` 提供 REST 状态的失效通知。首次连接不带 `Last-Event-ID` 时从当前最新序号订阅；携带游标时从其之后续传，显式 `0` 回放当前保留历史。客户端在每次建连成功后重新读取当前页面的 REST 状态。

通知默认按 24 小时、10,000 条的目标定期分批清理，保留最新一条作为持久水位；可通过 `BasicConfig.frontend_event_*` 配置调整。游标过期或超前时，流发送带新水位的 `stream.reset`，客户端须重新读取 REST 后继续处理新通知。普通通知和 reset 都不能作为完整任务快照。`/api/v1/monitor/events` 是独立监控流，使用其自身的缓冲策略。

## 只读健康检查

```bash
curl -fsS "$BACKEND_URL/api/v1/health"
curl -fsS "$BACKEND_URL/api/v1/readiness"
curl -fsS "$BACKEND_URL/api/v1/edge/readiness"
curl -fsS "$BACKEND_URL/api/v1/online-devices"
```

这些接口适合监控连通性，不代表具体设备已经满足物理安全条件。

## 功能分组

下表列出 OpenAPI 中的主要产品域。具体方法、字段和响应以本次安装的 Swagger 为准。

| 产品域 | 代表路径 | 用途 |
| --- | --- | --- |
| 健康与 Runtime 能力 | `/api/v1/health`、`/readiness` | 服务就绪、工作流加载进度和 `capabilities` 权限投影。 |
| 设备连接与在线设备 | `/api/v1/edge/readiness`、`/edge/sessions`、`/online-devices` | Uni-Lab OS 内部设备连接、注册和在线节点。 |
| 设备与动作 | `/api/v1/devices`、`/actions`、`/device-action-runs` | 设备目录、动作 schema 和标准单动作 Task。 |
| 设备状态 | `/api/v1/device-state`、`/device-state/history` | 当前设备状态、上报与历史。 |
| 物料与模板 | `/api/v1/materials`、`/materials/graph`、`/resource-templates` | 物料实例、关系图、模板、站点和历史。 |
| 实验室布局 | `/api/v1/lab/layout`、`/lab/placements`、`/lab/zones` | 布局、放置、区域、装配与仓库视图。 |
| 样品与内容物 | `/api/v1/samples`、`/current-substances`、`/inventory/contents` | 样品、当前物质和容器内容物。 |
| 试剂身份 | `/api/v1/reagent-infos`、`/compounds/{cas}` | 试剂目录、批量/文件导入和 CAS 查询。 |
| 试剂库存 | `/api/v1/reagents`、`/inventory/commands`、`/reagent-history/{id}` | 库存 CRUD、原子分装、历史、预留和消耗。 |
| 工作流创作 | `/api/v1/workflows`、`/authoring/*`、`/workflow-nodes`、`/workflow-edges` | 定义、导入、编译、启动图（Graph JSON）、节点、连线和草稿 CAS。 |
| 发布与组合 | `/api/v1/workflows/{id}/publications`、`/composite-invocations` | 不可变发布合同和子工作流调用。 |
| 运行前检查 | `/api/v1/workflows/{id}/run-preflight` | 零写入检查输入、设备、资源和库存。 |
| 任务 | `/api/v1/workflow-tasks`、`/{id}/jobs`、`/{id}/commands` | 创建、查询、控制任务和查看冻结 Job。 |
| 人工确认与恢复 | `/manual-confirmation`、`/resolve-uncertain`、`/settle-material-transfer` | 确认、未知结果和物料转移结算。 |
| 执行锁 | `/workflow-tasks/{id}/execution-locks`、`/force-release` | 查看和受审计强制释放锁。 |
| 工作流干预 | `/api/v1/workflow-interventions`、`/{id}/decisions` | 查询开放干预并提交合法选项。 |
| 调度运维 | `/api/v1/reschedule`、`/scheduler/drain`、`/timeline` | 重算、排空/恢复和 Job 时间线。 |
| 监控与历史 | `/api/v1/monitor/snapshot`、`/monitor/events`、`/history/jobs` | 调度快照、SSE 和历史。 |
| Workspace | `/api/v1/workspace/package-mounts`、`/file-browser` | 包挂载和受控文件浏览。 |

## 退役接口

以下三个路径仍出现在 OpenAPI 中，但固定返回 HTTP 410：

```text
/api/v1/debug/workflow-tasks
/api/v1/debug/workflow-tasks/{task_uuid}
/api/v1/debug/workflow-tasks/{task_uuid}/commands
```

新集成应创建标准 `mode=step` 的 workflow Task，并使用标准 task commands。

## 接口集成检查表

在写入生产数据前确认：

- 已经过 TLS 和身份认证网关；
- 使用调用方自己的最小权限凭证；
- 记录幂等键、对象 UUID 和期望修订；
- 对业务 `code`、HTTP 202、409/冲突和 5xx 分别处理；
- 只对网络错误和允许重放的操作重试；
- SSE 断线后重新读取 REST 状态；
- 写日志时脱敏 Token、Authorization、密码和访问密钥；
- 恢复类接口需要现场确认和审计原因。
