# 私有 `uni-lab-fe` 数据契约与本地微前端证据边界

本轮直接审计的证据基线是私有 `uni-lab-fe` 的
`origin/integration/fe-os-migration@355e2fc498e4d58701b71289cdd031beedef5afa`。

只在这个私有仓库中没有找到 `STANDARD_TABLES`、`DATA_ENTITIES` 或
`docs/protocol/data-model.md`；不能把它泛化成“用户当前微前端没有 catalog”。该仓库的
可执行契约是 TypeScript Port、Adapter 和契约测试。

## 本地微前端：已直接审计并更新

本轮直接读取并修改：

- 路径：`/home/wz/unilab-context/unilab-edge-ui`
- ref：`main@6c0db30e4fabfd67df5d99a1965a796e015e36e7`（工作树仍为本地 draft）
- 登记文件：`packages/protocol/src/catalog.ts::STANDARD_TABLES`、
  `packages/protocol/src/entities.ts::DATA_ENTITIES`、`packages/protocol/src/resource.ts`、
  `packages/protocol/src/entity-client.ts` 与 `src/views/DataEntitiesView.vue`

catalog 已从旧 17 项纠正为 26 个 v6 表/View，并按三层登记：

1. `backend-shared`：`resource_template`、`resource_handle_template`、`material`、
   `relative_position`、`site`、`material_state_history`。
2. `edge-compat`：`inventory_resource_template`、`material_instance`、`resource_relation`、
   `substance_content`；明确标成 View，不再把旧五列表误叫物理 `resource_template`。
3. `edge-private`：aggregate version sidecar、lot/reservation/ledger/outbox/cursor、布局、设备遥测
   与旧运行历史。

每项同时登记 `objectKind`、`schemaVersion`、`authority`、主键、逐列类型和 CRUD disposition。
Resource 客户端完整覆盖模板、Material、Graph、Site、state 路由；默认使用连接栏的 Host 微后端
base URL，换成正式 Backend 只替换 base URL。它同时检查 HTTP status 与 `{code,data,error}`
业务 envelope，HTTP 200 的非零 code 会抛 `BackendBusinessError`。

直接门禁结果：68 Edge ops、5 Cloud ops、17 inventory actions、26 typed entities；58 个
Vitest 测试、`vue-tsc` 和 Vite production build 全部通过。

`/home/wz/unilab-context/leaplab designs@24fc4ce` 仍只是 target-design；它描述未来持久化
所有权，不能写成微前端、Go Backend 或 OS 已实现 migration/API。

## Material 客户端投影

私有 `uni-lab-fe` 的 `packages/material/src/types.ts` 定义 UI 投影 `MaterialAggregate`：

- `material`：`id`（Material UUID）、`sourceTemplateId`、`code`、`name`、`description?`、
  `config`、`createdAt`、`updatedAt`。
- `placement`：`unplaced/world/parent/site`；site placement 使用 `parentId + siteId`。
- `sites[]`：`id`（Site UUID）、`ownerMaterialId`、`key/name`、pose/size、capacity、
  allowed template IDs、occupied Material IDs 和渲染元数据。
- `revision`：前端并发 token，不应擅自塞进 Backend `material` 表。

`packages/services/src/materials.ts::mapBackendMaterialGraph` 是该私有前端从 Backend wire 到
UI 的 Adapter Seam：

- `site.uuid → MaterialSite.id`
- `site.material_uuid → ownerMaterialId`
- `site.occupied_material_uuid → occupiedMaterialIds[0]`
- `current_site_uuid → MaterialPlacement.siteId`
- `relative_position → LabPose`

适配器拒绝重复 Site UUID、owner 不一致或悬空 current Site，不会从名称、数组下标或
`config.sites` 猜身份。

其中 Backend canonical 名称必须保持为：物理表 `site`（单数），
`site.material_uuid` 是 owner，`site.occupied_material_uuid` 是 occupant，组成关系使用
`material.parent_uuid`，状态事实表使用 `material_state_history`。私有前端的 camelCase/
聚合字段保留原样，但只能通过上述 Adapter 显式转换，不能反向改 Backend 字段名。

### 私有 `uni-lab-fe` 的 Material API 冲突

读取已采用 Backend-shaped `GET /api/v1/resource-templates*`、
`GET /api/v1/materials/graph`。但前端 `material.create` 当前发送的是聚合命令：

```text
POST /api/v1/materials
{template_id,name,placement,initial_contents,config?,expected_revision,idempotency_key}
```

Backend d552078 的同路径仍接收行/聚合创建 DTO：

```text
{resource_template_uuid,parent_uuid?,barcode,name,description?,meta_data?,config?,
 relative_position?,site_placement?}
```

Backend 候选 d123ce0 在该 DTO 上继续增加可选 `data`，并在响应 Material 中增加由服务端模板
展开逻辑派生的 `type`。私有前端 355e2fc 的 `MaterialAggregate` 没有对应实例类型字段，且当前
create 命令仍与 Backend DTO 不同；Adapter 必须选择是否把 `type` 投影为展示分类，并禁止把 UI
输入当成 `material.type` 的写入权威。

上述冲突仍适用于私有 `uni-lab-fe@355e2fc`。本地 `unilab-edge-ui` 已改为精确 Backend DTO：
create 支持 `data` 且不发送 `type/class`，update 不发送 `data/type/class/template`，根列表默认不含
组件、内部同步显式 `with_children=true`。同一 `createResourceClient(baseUrl)` 可直连 Host 微后端
或正式 Backend，不再为两端维护两套调用形状。

## Workflow 客户端契约

私有 `uni-lab-fe` 的 `packages/services/src/workflow.ts` 以 `WorkflowRuntimePort` 表达运行接口。
主要路径：

- Workflow/Authoring：`/api/v1/workflows*`、`/workflows/{uuid}/graph`、
  `/workflows/{uuid}/authoring*`、`/api/v1/authoring/*`。
- 运行：`/api/v1/workflow-tasks*`、`/workflow-tasks/{uuid}/jobs`、
  `/workflow-tasks/{uuid}/commands`、`/workflow-node-jobs/{uuid}*`。
- SSE：`/api/v1/events`；短事件只负责使 REST 投影失效，不携带整份状态。

Backend 候选 d123ce0 另新增发布契约 Interface：

- `POST /api/v1/workflows/{uuid}/publications`
- `GET /api/v1/published-workflow-contracts`
- `POST /api/v1/workflows/{uuid}/composite-invocations`

私有前端 355e2fc 未登记这组 Port；`unilab-edge-ui` 也刻意没有把它们登记为 Edge capability。
这些是 Backend 管理/发布能力，客户端只有连接正式 Backend 且提供者声明完整 capability 时才能
启用；不能因为 Backend 新增物理表，就让浏览器或 Edge 直读/复制该表。

当前 Task 状态：

```text
pending / admission_blocked / running / canceling /
succeeded / failed / canceled / timeout
```

当前 Job 状态：

```text
pending / dispatched / running / intervention_required / cancel_requested /
execution_unknown / succeeded / failed / skipped / canceled / timeout
```

`admission_blocked` 是前端迁移分支新增的投影状态，但 Backend d552078 的
`workflow_task.status` CHECK 尚不接受它；这是前端登记超前，不得写入 Edge SQLite。

Backend Workflow 公共成功终态是 `succeeded`。Edge Local REST v1 仍使用 `success` 时，
它属于遗留兼容 Adapter：对 Local v1 响应执行 `succeeded → success`；接收旧 `success` 后，
在进入 Backend-shaped 共享模型前规范化为 `succeeded`。不能把 Local v1 的 `success` 反向定义
成 Backend canonical 状态。Edge 共享 HTTP/WS 出入口已经集中执行 `success → succeeded`；
Local v1 的反向兼容仍只存在于本地响应边界。

前端 Task interface 仍声明 `input/output`，而 Backend migrations 37/40 已删除这两列；
若 Backend 响应不提供它们，前端严格解码需要同步修订。`edge_uuid` 是 Job 的控制归属投影，
不是 Material identity。

## 私有前端投影不定义数据库 Schema

本轮直接审计的私有 `uni-lab-fe` 只拥有：

- 展示投影和 capability matrix；
- snake_case wire 到 UI camelCase 的可测试映射；
- 对缺失语义 fail closed。

私有客户端投影不拥有 Backend/Edge 的表、FK、软删除、outbox 或 cursor，也不能要求 Edge
为纯展示字段新增持久化列。`unilab-edge-ui` 的 catalog 则是协议与物理对象登记，但同样不拥有
Backend 领域语义；它必须按 Backend canonical、Edge 兼容对象和 Edge 私有对象三层记录。

## 前端看到的 target-design 持久化所有权

浏览器在所有模式都只调用 Backend-shaped Interface，不直读 `os-local.sqlite`、
`workspace.sqlite`、`lab-dev.sqlite` 或 PostgreSQL，也不根据 URL/部署名称猜哪个进程在调度。

| 模式 | 前端 Interface 提供者 | 前端不可见的内部所有权 | Site identity |
|---|---|---|---|
| `os-quick-debug` | OS 本地微后端 | Active Host OS 独占本地运行存储并推进 DAG | Host Edge 首次生成并保存 |
| `go-standalone` | Go 前端网关；运行投影来自 Active Host 事件 | Go 独占 `workspace.sqlite`，Active Host OS 独占 `os-local.sqlite` | Go 新建；Quick Debug import 接受 Edge UUID 或 mapping |
| `go-lab-dev` | Lab Go 前端网关 | Lab Go 独占 `lab-dev.sqlite`，Active Host OS 负责实时调度 | Lab Go 生成，OS 原样 hydration |
| `go-lab` | Lab Go 前端网关 | Lab Go 独占 PG，Active Host OS 负责实时调度 | Lab Go 生成，OS 原样 hydration |

Go 不计算 `ready`、不持有活锁、不调用 Scheduler 或逐节点派发；每个 scope 只有一个 Active Host
OS。前端状态必须来自 Go 持久投影或 OS Backend-shaped read model，不能由浏览器推进。

Workspace 与 Lab-dev 通过不可变 Version Artifact 发布，不复制数据库行。Quick Debug →
Standalone export/import 携带 `schema_version/content_hash`：同 UUID 同 hash 幂等，同 UUID 异 hash
冲突。微前端只展示并提交该冲突，不得自行重写 UUID 或按名称合并。

微前端 catalog 现以 `authority=backend-shared/edge-compat/edge-private`、`objectKind` 和
`schemaVersion` 标注已实现对象；`os-local.sqlite`/Go target 表仍不进入 implemented catalog。
