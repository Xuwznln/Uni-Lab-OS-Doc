# 前端当前数据契约证据（355e2fc）

证据基线是 `uni-lab-fe` 的
`origin/integration/fe-os-migration@355e2fc498e4d58701b71289cdd031beedef5afa`。

本地当前仓库及组织远端没有找到旧 OpenLab 的 `STANDARD_TABLES`、`DATA_ENTITIES` 或
`docs/protocol/data-model.md`。因此不能把历史“17 张 Edge 表”当成当前 Backend 标准，
也不能声称它们仍由前端维护。当前可执行契约是 TypeScript port、adapter 和契约测试。

## Material 客户端投影

`packages/material/src/types.ts` 定义唯一 UI 投影 `MaterialAggregate`：

- `material`：`id`（Material UUID）、`sourceTemplateId`、`code`、`name`、`description?`、
  `config`、`createdAt`、`updatedAt`。
- `placement`：`unplaced/world/parent/site`；site placement 使用 `parentId + siteId`。
- `sites[]`：`id`（Site UUID）、`ownerMaterialId`、`key/name`、pose/size、capacity、
  allowed template IDs、occupied Material IDs 和渲染元数据。
- `revision`：前端并发 token，不应擅自塞进 Backend `material` 表。

`packages/services/src/materials.ts::mapBackendMaterialGraph` 是 Backend wire 到 UI 的唯一
适配 seam：

- `site.uuid → MaterialSite.id`
- `site.material_uuid → ownerMaterialId`
- `site.occupied_material_uuid → occupiedMaterialIds[0]`
- `current_site_uuid → MaterialPlacement.siteId`
- `relative_position → LabPose`

适配器拒绝重复 Site UUID、owner 不一致或悬空 current Site，不会从名称、数组下标或
`config.sites` 猜身份。

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

两者路径相同但语义不同。能力矩阵若把 `material.create` 对 Backend/Edge 宣告为可用，
请求会不兼容；在命令 DTO 正式统一前必须 fail closed，不能由 Edge 猜字段。

## Workflow 客户端契约

`packages/services/src/workflow.ts` 的 `WorkflowRuntimePort` 是 UI 唯一运行契约。主要路径：

- Workflow/Authoring：`/api/v1/workflows*`、`/workflows/{uuid}/graph`、
  `/workflows/{uuid}/authoring*`、`/api/v1/authoring/*`。
- 运行：`/api/v1/workflow-tasks*`、`/workflow-tasks/{uuid}/jobs`、
  `/workflow-tasks/{uuid}/commands`、`/workflow-node-jobs/{uuid}*`。
- SSE：`/api/v1/events`；短事件只负责使 REST 投影失效，不携带整份状态。

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

前端 Task interface 仍声明 `input/output`，而 Backend migrations 37/40 已删除这两列；
若 Backend 响应不提供它们，前端严格解码需要同步修订。`edge_uuid` 是 Job 的控制归属投影，
不是 Material identity。

## 前端不定义数据库 Schema

前端只拥有：

- 展示投影和 capability matrix；
- snake_case wire 到 UI camelCase 的可测试映射；
- 对缺失语义 fail closed。

前端不拥有 Backend/Edge 的表、FK、软删除、outbox 或 cursor，也不能要求 Edge 为纯展示字段
新增持久化列。
