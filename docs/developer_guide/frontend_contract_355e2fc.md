# 私有 `uni-lab-fe` 数据契约与本地微前端证据边界

本轮直接审计的证据基线是私有 `uni-lab-fe` 的
`origin/integration/fe-os-migration@355e2fc498e4d58701b71289cdd031beedef5afa`。

只在这个私有仓库中没有找到 `STANDARD_TABLES`、`DATA_ENTITIES` 或
`docs/protocol/data-model.md`；不能把它泛化成“用户当前微前端没有 catalog”。该仓库的
可执行契约是 TypeScript Port、Adapter 和契约测试。

## 本地微前端：导师提供、待实机复核

导师提供的另一个证据源是：

- 路径：`/home/wz/unilab-context/unilab-edge-ui`
- ref：`main@6c0db30e4fabfd67df5d99a1965a796e015e36e7`
- 状态：dirty
- 登记文件：`packages/protocol/src/catalog.ts::STANDARD_TABLES`、
  `packages/protocol/src/entities.ts::DATA_ENTITIES`、`docs/protocol/data-model.md`、
  `docs/protocol/cloud-mapping.md`

本机不存在 `/home/wz`，以上是导师提供的本地工作区证据，尚未在该工作区直接执行
`git status`、读取文件或运行契约测试。根据提供的语义并与 Edge 195434 的 v4 Schema
交叉核对，catalog 登记的是以下 17 张 Edge live v4 物理表：

```text
resource_template, inventory_lot, material_instance, resource_relation,
substance_content, inventory_reservation, inventory_ledger, sync_outbox,
processed_command, sync_cursor, lab_meta, lab_zone, lab_placement,
device_property_latest, device_property_history, workflow_runs, job_runs
```

具体 catalog 顺序、字段元数据和 dirty diff 必须在 `/home/wz` 实机复核后才能升级为直接证据。
这 17 张表不能直接替换成 Backend 表，协议登记应分三层：

1. Backend canonical/shared Schema：`resource_template`、`resource_handle_template`、
   `material`、`relative_position`、`site`、`material_state_history` 和 Backend Workflow 表。
2. Edge 当前物理表/兼容 View：注明 v4 table 与 v5 canonical table/View 的迁移映射、对象类型、
   读写性和最低 Schema 版本。
3. Edge 私有同步表：`sync_outbox`、`processed_command`、`sync_cursor` 等，不进入 Backend DTO。

导师后续提供但本环境仍未直接审计的微前端更新还包括：新增
`docs/protocol/persistence-ownership.md`，并更新 README、`cloud-mapping.md`、`data-model.md`；
导师报告 `protocol:check` 通过，计数为 53 Edge ops、5 Cloud ops、17 actions、17 typed entities。
这些是 `local-draft` 验证记录，不是本机执行结果，也不会把 17 张 live 表自动改成 target 表。

另一个独立证据源是导师提供、待实机复核的
`/home/wz/unilab-context/leaplab designs@24fc4ce` target-design。它描述未来持久化所有权；不能把
该设计分支写成微前端、Go Backend 或 OS 已实现 migration/API。

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

### 当前 Material API 冲突

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

两者路径相同但语义不同。能力矩阵若把 `material.create` 对 Backend/Edge 宣告为可用，
请求会不兼容；在命令 DTO 正式统一前必须 fail closed，不能由 Edge 猜字段。

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

私有前端 355e2fc 未登记这组 Port，导师提供的 `unilab-edge-ui` 工作区也未在本机直接复核。
客户端只有在提供者声明完整 capability 且 DTO/错误语义通过契约测试后才能启用；不能因为
Backend 新增了物理表，就让浏览器或 Edge 直读该表。

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

Backend Workflow 公共成功终态是 `succeeded`。Edge Local REST v1 当前仍使用 `success` 时，
它属于遗留兼容 Adapter：对 Local v1 响应执行 `succeeded → success`；接收旧 `success` 后，
在进入 Backend-shaped 共享模型前规范化为 `succeeded`。不能把 Local v1 的 `success` 反向定义
成 Backend canonical 状态。当前 Edge `workflow_status` WebSocket 上行仍会原样发送内部状态，
因此 `success → succeeded` 是待实现的共享边界 Adapter，不是已经闭合的能力。

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

微前端 catalog 需要额外标注五类成熟度：`backend-implemented-candidate`、`edge-live`、
`legacy-implemented`、`local-draft`、`target-design`。`os-local.sqlite`/Go target 表只能登记在最后一类，
当前 17 张 Edge live 表仍保持 implemented。
