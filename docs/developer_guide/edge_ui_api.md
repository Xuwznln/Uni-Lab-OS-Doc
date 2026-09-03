# Edge HTTP 接入契约

## 当前边界

Uni-Lab-OS 的微后端数据面和调度职责如下：

| 能力 | 唯一实现 | 说明 |
| --- | --- | --- |
| Workflow 定义、Graph、Task、Node Job | `runtime.db`（workflow 表）+ `WorkflowService`（`unilabos/server/services/runtime/workflow/`） | 本地调度模式挂载写入口；Backend-controlled 模式由远端持有 |
| DAG、动作锁、物料锁、库存准入 | `unilabos/server/backend/scheduler/` | 同一轮调度使用一个完整资源申请 |
| 执行与 Backend 命令协调 | `unilabos/server/backend/` | 接入云端后 Host 只执行 Backend 已准入的 Job |
| Material、Site、库存预留与账本 | `materials.db` + `MaterialsService` | 物料聚合与库存事实共用事务边界 |
| 设备状态 | `telemetry.db` + `TelemetryService` | latest 与 append-only event 分表存储 |
| Job 生命周期与可靠收发 | `runtime.db` + `RuntimeService` | Backend 命令先持久化再执行 |
| 结果、反馈、错误与人工替换 | `history.db` + `HistoryService` | 使用统一的追加式历史流 |
| HostLink 网络 | `unilabos/backend/hostlink/` | 与调度实现解耦 |

完整 Host API 默认使用 `:8002`（`unilab --port` 可改）。前端只以独立静态站（GitHub
Pages 上的推荐站点）部署：在连接面板里填写进程地址即可，微后端已放开 CORS；本进程
不托管前端页面，根路径 `/` 只给出推荐前端与 API 工具的路标。

`unilab` 不带 `-g` 也能启动：Host 以空图起来（只有 `host_node`），设备随后从前端「驱动包」页
安装并作为受管子进程接入，或用 `-g <图>` 重启。Slave 仍必须用 `-g` 指定它要接入的设备。

## 契约导出（OpenAPI）

契约真相是 FastAPI 从路由声明生成的 OpenAPI。运行中的进程只挂当前角色的路由，所以
`GET /api/openapi.json` 是「本进程视角」；发布给前端对账的是离线导出的**全集**：

```bash
python -m unilabos.server.openapi_export --output openapi.json
```

它在临时目录里用四库 + 内存 WorkflowService 挂满 Host 与 `--role backend` 的全部路由，
不需要设备、不起 uvicorn，并给每个 operation 打 `x-openlab-role`（`host` / `backend` /
`any`，映射表在 `unilabos/server/openapi_export.py`，角色专属的新路由要在那里登记）。
OpenLab 用 `pnpm --filter @openlab/protocol openapi:sync` 调用它刷新快照并生成 TypeScript
类型；`protocol:check` 会把前端目录与这份导出对账，多一条、少一条、角色不一致都会失败。

因此新增路由**应当**声明 `response_model`（Pydantic）——否则 OpenAPI 只有请求体没有响应体，
前端类型只能手写、漂移只能人工发现（Workflow 域的行 DTO 目前就是这样，`workflow_node_job`
的 `attempt` → `attempt_no` 改名前端就是靠人工对照才追上的）。

## 请求规范

浏览器可调用的 HTTP API 遵循 OpenLab 仓库 `docs/protocol/conventions.md`（规范性文本，
MUST / SHOULD），`@openlab/protocol` 是它的类型化实现。给微后端新增或修改路由时必须满足：

- 路径 `/api/v1/<复数 kebab-case 集合>[/{snake_case_uuid}][/<子集合>][/<动词>]`；领域动作用
  `POST …/<动词>`（`/start`、`/apply`、`/launch`），不在路径里放 CRUD 动词；
- 方法语义：PUT 整体设定、PATCH 局部、DELETE 幂等；空体请求不要求 `Content-Type: application/json`；
- 状态码：201 同步创建、202 异步长操作（返回 operation 资源供轮询）、204 无正文删除、
  404 不存在或未挂载、409 状态冲突、422 一切可修正的请求错误（不用 400）、503 能力未装配；
  错误正文统一 `{"detail": …}`；
- 响应形态按域固定：直出 DTO；Backend 信封 `{code, data | error}`（workflow / registry / graphs，
  HTTP 恒 200，业务码只追加）；materials.v1 写信封 `InventoryMutation → MutationResult`；
- 字段 snake_case；权威身份 `*_uuid`；新增时间字段一律 `*_at_ms`（UTC epoch 毫秒整数）；
  枚举小写；新增枚举值算加法变更，客户端必须容忍；
- 列表：人看的用 `page / page_size → {items, total, page, page_size}`；子记录用 `limit / offset`；
  append-only 流用 `after_sequence` 游标；
- v1 内只做加法变更；删除 / 改名 / 改语义要走废弃流程并升大版本；
- 同一变更集内同步刷新 OpenLab 的 OpenAPI 快照（`openapi:sync`）、`catalog.ts`、域客户端、
  协议测试与域文档（`conventions.md §11` 清单）；浏览器不该调用的新路由登记到前端校验脚本的
  控制面清单，浏览器目录不得登记 Backend ↔ Edge 控制面写端点。

## UI 应使用的接口

默认 Host 数据面：

- `/api/v1/runtime`：endpoint、命令、execution job、可靠 outbox；
- `/api/v1/materials`：模板、Material、Site、lot、reservation 和 ledger。
  写请求信封 `InventoryMutation` 的 `actor_type` / `actor_uuid` 会原样落到 ledger
  （`GET /materials/changes`），前端物料变更列表以它渲染"来源" tag；浏览器发起的
  写请求应显式填 `actor_type: "human"`，不要依赖默认值 `edge`（`edge` 应展示为
  "Edge 上报"，取值表见 `examples/materials_operations_guide.md §2.2.1`）；
- `/api/v1/telemetry`：设备最新状态和事件；
- `/api/v1/history`：payload 与统一历史事件；
- `/api/v1/health`、`/api/v1/hostlink/peers`：轻量诊断；
- `/api/v1/status-incidents`、`/api/v1/error-decisions`：人工决策；
- `/api/v1/restart`：安静点重启（调试用）。POST 登记后暂停新派发，等
  active job 清空后按 scope 重启：`--role backend` 分离模式下通知 Edge
  进程整进程重启（调度权威常驻，重连后自动恢复派发）；同进程模式整进
  程重启并以相同参数自动拉起，等待中的任务由调度恢复链路继续，
  不会失败。GET 查询等待状态，DELETE 取消并恢复派发；body 可选
  `{"mode": "immediate"}` 跳过安静等待、`{"scope": "devices"|"process"}`
  显式指定作用域（默认 auto）。
- `/api/v1/scheduler/resources`：本机调度（默认）时可读；接入云端后以 503
  明确表示调度权威在远端 Backend。
- `/api/v1/driver-packages`（带执行面的 Host）：驱动包管理。`POST /install`
  在本进程解释器里 `pip install <pip 规格 | git URL | 本地目录>`（202，返回可轮询的
  operation；`upgrade=true` 加 `--upgrade`，`name` 是调用方已知的分发名，git/URL 规格靠它
  可靠登记），装完 AST 扫描 `@device` 记入 `<working_dir>/driver_packages.json` 台账；
  `PUT /{name}/enabled`、`DELETE /{name}` 改台账 / 卸载。启动时 `main.py` 把已启用的包目录
  并入 `--devices` 扫描，所以对 Host 本体这些操作都要 `POST /restart {"scope": "process"}`
  后生效（inventory 的 `restart_required` 提示前端）。可安装目录的官方来源是
  [awesome-lab-devices](https://github.com/Xuwznln/awesome-lab-devices) 的 `index.json`，由
  OpenLab 前端在浏览器里直接读取再把 `spec` 下发到这里；`GET /catalog` 只是 Edge 侧补充
  （`HTTPConfig.driver_package_index_url` 内网镜像 + 本地
  `<working_dir>/driver_package_catalog.json`），结构与 index.json 相同。
- `/api/v1/driver-packages/{name}/graphs`（带执行面的 Host）：驱动包随包设备图。示例设备包
  （`LabDevice*Demo`）把图作为 data-files 装到 `share/<包>/graph/*.json`（源码安装则在仓库
  `graph/`），`GET` 列出（`devices`、`device_only`），`GET /{graph}` 取 node-link 载荷，
  `POST /{graph}/launch` 直接把它作为受管设备进程拉起（同名进程 `<包>/<图>` 已存在则更新
  规格后重启），返回 `{created, process}`——前端「安装 → 启动」的启动一步就是它；纯设备图
  不需要 Host 重启，包里的 `@workflow` 模板仍要等 Host 重启后才上报。
- `/api/v1/device-processes`（带执行面的 Host）：受管设备进程。一条规格 = 设备节点列表
  （服务端展开为 slave 图，uuid 沿用物料权威已有设备身份）+ 要挂载的驱动包 + 重启策略；
  `start` 以 `python -m unilabos --is_slave --host_node_ip … -g <图>` 拉起本机子进程，经
  HostLink 接回本 Host；`never / on-failure / always` 三种看护策略，退避重启、超过
  `max_restarts` 停在 `crashed`；`auto_start` 的进程随 Host 启动拉起、Host 退出时终止；
  `GET /{id}/logs` 读尾部日志。驱动崩溃只影响该子进程，Host 与物料权威不受影响。
  这两组路由不在 `--role backend` 进程上挂载（404），前端按能力缺失降级。协议细节见
  OpenLab 仓库 `docs/protocol/driver-packages.md`。
- `/api/v1/registry/*`（持有调度权威的进程：默认 Host 或 `--role backend`）：条目级
  注册表版本。与 Workflow Authority 同归属、同一 `setup_server()` 挂载：默认 Host 启动
  时把自身扫描结果直接写进程内 Registry Authority，`--role backend` 则接收 Edge 经
  `POST /resource-templates` 上报的快照，两条路径共用同一模板投影。
  `entries` 列条目状态；`pending-impacts` 把挂起冲突映射到受影响的
  workflow 节点；`entries/{name}/apply` 确认待处理版本，`dismiss` 忽略挂起，
  `restore/{version}` 还原历史版本。
  接入云端（`--address`）的纯 Edge 没有该域，前端按 404/503 静默降级。

默认（本机调度）Host 同时挂载 Workflow Authority，这是 edge UI 的写入口：

- `/api/v1/workflows` 管理定义；
- `/api/v1/workflows/{uuid}/graph` 管理整图；
- `/api/v1/workflow-tasks` 创建一次运行；
- `/api/v1/workflow-tasks/{uuid}/node-runs` 查询节点运行：每节点一条，`status /
  return_info` 是当前（重试后的）attempt 的结果，`attempts` 是该节点的执行历史——画布
  节点状态与结果读取用它；
- `/api/v1/workflow-tasks/{uuid}/jobs`、`/api/v1/workflow-node-jobs/{job_uuid}` 查询
  attempt（物理执行），`job_uuid` 与 `/error-decisions` 报告、执行事件里的 `job_id` 一致。

UI 的工作流写链路是：保存 Workflow 定义，保存 Graph，再创建 Workflow Task。
Backend-controlled 模式不挂载本地 Workflow 写 API；图由 Backend 持有，Edge
只接收已经调度好的 Job 命令。

## 实时与恢复

UI 的恢复基线来自四库 API，而不是进程内事件缓存：

- 当前执行状态读取 `runtime.execution_job`；
- 设备当前值读取 `telemetry.device_state_latest`；
- 执行历史读取 `history.history_event`；
- 物料余额和预留读取 Materials API；
- WebSocket 只传短通知，完整命令和状态经 HTTP 数据面获取。

因此断线重连不依赖 SSE replay，也不会因服务重启丢失恢复水位。

## 前端集成要求

1. Workflow 执行使用“定义 → Graph → Task”写链路。
2. Timeline 组合 Runtime、History 和 Telemetry 的持久化投影。
3. retry 必须表现为调度权威创建的新 attempt/job（Backend-controlled 下是 Backend，
   本机调度下是 Workflow Authority 的同一事务）；Edge 不在原 Job 上本地重排。画布以
   节点运行为单位展示：当前状态来自节点运行投影，历史来自 `attempts`。
4. 调度资源页面要识别 `/scheduler/resources` 的 503：这表示该 Host 已接入云端、
   调度权威在远端 Backend，不是服务故障。

## 回归重点

- 普通 Host 启动时只打开 `runtime.db`、`materials.db`、`telemetry.db`、
  `history.db` 四个 writer；
- 同一物料即使流向不同设备动作，也由同一个 Scheduler 串行化；
- 仓储 reservation 分配出的实体物料 UUID 会进入同一资源申请；
- 执行端遇到动作冲突会拒绝，不创建本地等待队列；
- Runtime 和 History 的状态转换通过对应领域 Service 完成。
