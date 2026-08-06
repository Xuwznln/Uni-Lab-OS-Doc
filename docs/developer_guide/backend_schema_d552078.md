# Backend 当前领域 Schema 证据（d552078）

本文记录 `uni-lab-backend` 的已实现基线
`origin/main@d5520789975d6aa14792b8c1bde6565050b5fcf8`。证据来自按顺序执行
`migrations/sqlite/000001..000049` 后的 `sqlite_master`、`PRAGMA table_info`、
`foreign_key_list`、`index_list`，并与 Go 领域模型及 Router 交叉核对。

2026-08-07 直接 fetch 还核验到未进默认分支的
`origin/feat/workflow@d123ce0a4e3b3ff834c26f4f02e3f9f53bea3b3e`。它在 d552078 后新增
000050..000054；本文以“d123ce0 候选增量”单独登记，不能反写成默认 main 已发布。

这不是要求 Edge 把 PostgreSQL/SQLite DDL 逐字复制。它定义的是 Backend 领域语义、
公共 JSON 字段、约束和写入权威。

本文主体成熟度是 `backend-implemented-candidate`：所列对象都有实际 migration/model/Router
证据；具体对象是否已进默认分支由各节 Ref 明示，不能从成熟度名称推断。文末另列导师提供、待实机复核的
`leaplab/designs@24fc4ce` `target-design`；目标表不能反向写成 d552078 已实现。

## 记号

- `Base`：`uuid UUID PK`、`create_time`、`update_time`、可空 `deleted_at`、可空
  `description`、`meta_data JSON object DEFAULT {}`。
- `JSON`/`JSON[]`：SQLite 物理类型为 `TEXT` 并由 `json_valid/json_type` 约束；
  PostgreSQL 使用对应 JSON 类型。
- `active unique`：仅对 `deleted_at IS NULL` 的行生效。
- 时间在公共 API 中是 RFC 3339；数据库使用 `DATETIME/timestamptz`，不是毫秒整数。

## 资源、物料、库位（Site）

| 表 | 字段（除 Base） | FK、唯一约束和索引 | Backend d552078 当前实现 / 公共 API |
|---|---|---|---|
| `resource_template` | `name, display_name, resource_type, header?, footer?, icon?, model JSON, module?, language?, tags JSON[], data_schema JSON, config_schema JSON, pose JSON, config_info JSON[], cover?, scene JSON[], device_params JSON, manufacturer_uuid?, ui_overlay JSON` | active unique `name`（区分大小写）；active `resource_type`、分页游标索引 | Backend 模板服务；`/api/v1/resource-templates*` |
| `resource_handle_template` | `resource_template_uuid, name, display_name, type, io_type, source?, key?, side?`；`io_type=source/target/bidirectional` | FK template RESTRICT；active unique `(resource_template_uuid,io_type,name)` | Backend 模板服务；随模板详情公开 |
| `material` | `resource_template_uuid, barcode, name, config JSON, data JSON, parent_uuid?, class` | FK template/self-parent RESTRICT；禁止自父；active unique `LOWER(barcode)`（非空）、根 Material 的 `LOWER(name)`；parent/template 索引 | Backend 资源服务；`/api/v1/materials*` |
| `relative_position` | `material_uuid, position_x/y/z, depth,length,width, scale_x/y/z, rotation_x/y/z` | FK Material RESTRICT；每个 active Material 至多一行 | Material 聚合内写；Material 详情/Graph 公开 |
| `site` | `material_uuid, name, sort_order, allowed_resource_template_uuids JSON[], occupied_material_uuid?, position_x/y/z, depth,length,width` | 两个 Material FK RESTRICT；禁止 owner=occupant；active unique `(material_uuid,LOWER(name))`；active occupant 全局唯一；owner+order 索引 | 当前 Backend create 路径在事务内分配 UUID；`/materials/{uuid}/sites`、`/sites/{uuid}`、Material Graph |
| `material_state_history` | `material_uuid,status?,state_data JSON,source?,observed_at` | FK Material RESTRICT；`(material_uuid,observed_at DESC,uuid DESC)` | append-only 状态事实；`/materials/{uuid}/states*` |
| `material_ledger_entry` | `uuid PK, material_uuid,event_type,operator_type,from_site_uuid?,to_site_uuid?,changes JSON,extension JSON,trace_id?,recorded_at`；没有 Base | `event_type=created/updated/deleted`；`operator_type=frontend/edge/system`；Site 变更前后不能相同；Trace ID 为 32 位小写 hex；FK Material/Site RESTRICT；时间线索引 | 已存在的 Backend canonical 不可变操作台账；`GET /materials/{uuid}/ledger`；Edge 接入延后 |
| `material_warehouse` | `resource_template_uuid,name,alias?,sku?,spec?,unit,batch_no,order_no?,supplier,quantity,remaining,safety_stock,unit_price,storage_location?,operator?,inbound_at?,attachments JSON[],status` | FK template；active unique `(template,LOWER(batch_no))`；FIFO 索引 | Backend 仓储域；Edge 当前无共享实现 |

`Site.uuid` 是库位（Site）本身的稳定身份；`Site.material_uuid` 是拥有该库位的
Material；`Site.occupied_material_uuid` 是当前占用该库位的另一个 Material。三者不得互换。
Material 的 `parent_uuid` 表示组成关系，库位占用不改变组成关系。

上表描述 Backend d552078 在 `backend_controlled` 下的当前实现，不把“Backend 分配 UUID”
提升成所有部署模式的唯一规则。在 `local_scheduler` / 默认开源 Host 模式，Host Edge 是本地
Material/Site Authority，可在首次物化时分配并持久化稳定 Site UUID。Edge-origin 聚合上送时，
Backend 仍缺接收外部 `material.uuid/site.uuid` 的 import/upsert 或显式 identity mapping；这是
Backend Interface TODO，不能通过两端各自生成 UUID 或按 label/index 猜测合并来绕过。

`material_ledger_entry` 已由 migration 000049 落入 Backend canonical Schema。Edge 的
`inventory_ledger` 是不同语义的私有库存事件账，不能改名冒充；在可信
`operator_type/user` 注入完成前，Edge 镜像/写入可以延后，但 Backend 已存在状态必须保留。

### d123ce0 候选资源增量

| 变更 | 实际证据 | Edge / Interface 处置 |
|---|---|---|
| `material.type` | 000050 添加 `VARCHAR(32)/TEXT NOT NULL`；旧数据依次从模板 `config_info` 匹配组件类型、模板 `resource_type`、`resource` 回填；Go `Material.Type` 直接输出 JSON `type` | Edge v5 `material` 尚无该列。`local_scheduler` 下属于 A 类持久事实，下一 Schema 版本应 additive 增列并采用同义回填；不能用 `class` 临时代替 |
| `idx_material_type_active` | 000051 对 `LOWER(TRIM(type)) WHERE deleted_at IS NULL` 建索引；000054 兼容曾发生的 migration 编号分叉 | Edge 新列落地时同步建 active index；SQLite/PostgreSQL 表达可不同 |
| Material create `data` | Handler create DTO 增加 `data`，Service 写入 `material.data`；不是新列 | Edge 表已有 `data`，只需 DTO/Service parity，不需要再改表 |

这组增量没有修改 `site`、`relative_position`、Site UUID 分配、`material.parent_uuid`、软删除、
时间格式、outbox 或状态枚举。`material.type` 由服务端根据冻结模板组件派生，不是客户端可自由
写入的新 Authority。

## 化学品与物质状态

| 表 | 字段（除 Base） | 关键约束 / API |
|---|---|---|
| `reagent_info` | `cas?,name,aliases JSON[],molecular_formula?,smiles?,inchi_key?,molecular_weight?,physical_state` | active CAS/InChI Key 唯一，名称/物态索引；`/reagent-info*` |
| `reagent` | `material_uuid,reagent_info_uuid,concentration_value/unit?,quantity,quantity_unit` | 每个 active Material 至多一个 Reagent；两个 FK RESTRICT；`/reagents*` |
| `sample` | `material_uuid,code,name,quantity,quantity_unit` | active code、Material 唯一；`/samples*` |
| `current_substance` | `material_uuid,name?,composition JSON[],quantity,quantity_unit,physical_state,revision,observed_at` | 每个 active Material 一行；revision 是物质内容版本；`/current-substances*` |
| `substance_history` | `current_substance_uuid,material_uuid,change_type,name?,composition JSON[],quantity,quantity_unit,physical_state,revision,observed_at` | `(current_substance_uuid,revision)` 唯一；Material 时间/版本索引；`/substance-history*` |
| `reagent_warehouse` | `reagent_info_uuid,name,brand?,grade?,sku?,spec?,batch_no,order_no?,supplier,quantity,remaining,safety_stock,package_quantity/unit,concentration_value/unit?,unit_price,storage_location?,operator?,inbound_at?,expires_at?,attachments JSON[],status` | active `(reagent_info_uuid,LOWER(batch_no))` 唯一；FIFO/有效期索引 |

## 工作流定义

| 表 | 字段（除 Base） | FK、唯一约束和索引 | 公共语义 |
|---|---|---|---|
| `workflow` | `name,tags JSON[],revision>0` | active 创建时间索引 | 可复用工作流定义；`/api/v1/workflows*` |
| `workflow_node_template` | `resource_template_uuid,name,display_name,class?,goal JSON,goal_default JSON,feedback JSON,result JSON,schema?,type,icon?,header?,footer?,node_type` | FK template；active unique `(template,name)` | 节点模板目录 |
| `workflow_handle_template` | `workflow_node_template_uuid,handle_key,io_type,display_name,type,data_source?,data_key?,required bool` | `io_type=source/target`；active unique `(node_template,handle_key,io_type)` | 节点 Handle 定义 |
| `workflow_node` | `workflow_uuid,workflow_node_template_uuid?,parent_uuid?,material_uuid?,name,type,icon?,pose JSON,param JSON,footer?,action_name?,action_type?,disabled,minimized,script?,execution_policy JSON` | FK Workflow/template/self-parent/Material；禁止自父；无持久化 `status` | 图节点；`action_name/action_type` 是冻结执行身份 |
| `workflow_edge` | `source_node_uuid,target_node_uuid,source_handle_uuid,target_handle_uuid` | 四个 FK；禁止自环；active 四元组唯一；每个 target handle 至多一条 active 边 | 没有 `workflow_uuid`；通过节点归属解析；Handle UUID 是规范引用 |

### d123ce0 候选发布契约

000052..000054 最终建立 `published_workflow_contract`：除 Base 外包含
`workflow_uuid,workflow_revision,version,name,tags,node_template_uuid?,input_contract,
output_contract,executor_requirements,executor_binding_mapping,boundary_mapping,graph_snapshot,
source_hash,contract_digest,node_count,edge_count`。关键约束是 Workflow FK、可空 Node Template
FK、每 Workflow 的 revision/version 唯一，以及 active latest/created 索引。

候选 Router 新增：

- `POST /api/v1/workflows/{uuid}/publications`
- `GET /api/v1/published-workflow-contracts`
- `POST /api/v1/workflows/{uuid}/composite-invocations`

它是 Backend 所有的不可变版本制品，不是 Edge `workflow` 行、target
`local_workflow_version` 或 Lab `published_workflow_version` 的自动别名。Edge 需要的是版本制品
DTO/hydration 与 capability parity；除非本地 Quick Debug 自己拥有发布权威，否则不应复制该
Backend 表。

`workflow_node.type` 是作者图类型字符串；真正决定执行器的是 Job 的
`executor_kind=device_action/compute/condition/script/tool_call/manual_confirm`。不要把旧的
`Group/ILab`、PLR class 或 `host_node` 写进领域 FK。

## 工作流运行、控制和恢复

| 表 | 字段（除 Base） | 关键枚举、约束和公开面 |
|---|---|---|
| `workflow_task` | `workflow_uuid?,status,workflow_snapshot JSON,execution_plan JSON,run_mode,target_node_uuid?,control_status,cleanup_status,trace_context JSON,error_info JSON[],timeout_at?,attention_reason?,terminal_ghost_detected_at?,reconciliation_resume_control_status?,execution_kind,idempotency_key?,request_fingerprint,started_at?,finished_at?` | status=`pending/running/canceling/succeeded/failed/canceled/timeout`；run mode=`normal/step/single_node`；execution kind=`workflow/ad_hoc_device_action`；直接设备动作允许 `workflow_uuid=NULL`；`/workflow-tasks*` |
| `workflow_node_job` | `workflow_task_uuid,workflow_node_uuid,material_uuid?,edge_agent_uuid?,edge_command_uuid?,job_access_token_hash,feedback_sequence,topological_index,executor_kind,execution_policy JSON,execution_timeout_seconds,status,attempt,param JSON,feedback_data JSON,return_info JSON,control_data JSON,error_info JSON[],各阶段 deadline/command/uncertainty/start/finish` | status=`pending/dispatched/running/intervention_required/cancel_requested/execution_unknown/succeeded/failed/skipped/canceled/timeout`；每 Task/Node/attempt active 唯一；`/workflow-node-jobs*` |
| `workflow_task_command` | `workflow_task_uuid,type,target_node_uuid?,idempotency_key,status,result JSON,trace_context JSON,consumed_at?` | type=`step/pause/resume/cancel`；status=`pending/succeeded/rejected`；active 幂等键唯一 |
| `workflow_node_job_feedback_history` | `workflow_node_job_uuid,sequence,feedback_type,data JSON,observed_at,received_at,published_at?,idempotency_key` | Job 内 sequence、idempotency 唯一；feedback 补读 API |
| `workflow_node_job_result` | `workflow_node_job_uuid,edge_command_uuid,job_access_token_hash,idempotency_key,outcome,return_info JSON,error_info JSON[],committed_at,consumed_at?` | outcome=`succeeded/failed/canceled/timeout`；每 Job 一个结果 |
| `workflow_intervention` | `workflow_task_uuid,workflow_node_job_uuid,edge_agent_uuid,revision,status,options JSON[],resume_control_status,selected_option_id?,selected_option JSON,decision_idempotency_key?,edge_command_uuid?,opened_at,decided_at?` | status=`open/selected/superseded`；每 Job 一个 open 记录 |
| `workflow_manual_confirmation` | `workflow_task_uuid,workflow_node_job_uuid,status,assignee_user_ids JSON[],confirmed_by?,comment?,decision_idempotency_key?,opened_at,deadline_at?,decided_at?,param JSON` | status=`pending/approved/rejected/timed_out/canceled`；每 Job 一行 |
| `execution_lock_lease` | `lock_key,material_uuid,workflow_task_uuid,workflow_node_job_uuid,state,acquired_at,released_at?` | state=`reserved/running/released/uncertain`；active lock key 全局唯一；`material_site` scope 目前模型预留但持久层明确拒绝 |

`workflow_node_job_sample` 不存在于当前迁移；样本/物料绑定由 `material_uuid` 和节点参数表达。

Backend Workflow 公共成功终态是 `succeeded`。Edge Local REST v1 若继续返回 `success`，只能由
遗留 Adapter 执行 `succeeded → success`；Backend 表、DTO 和 Backend-shaped Interface 不得改成
`success`。

## Backend ↔ Edge 控制面与前端事件

| 表 | 字段（除 Base） | 约束 / 角色 |
|---|---|---|
| `edge_agent` | `edge_key,capability_revision,status,last_seen_at` | active `LOWER(edge_key)` 唯一；Backend Edge 注册权威 |
| `edge_device_binding` | `edge_agent_uuid,material_uuid,local_device_id,device_name` | active Material 只能绑定一个 Edge；Edge 内 local ID 唯一 |
| `edge_session` | `edge_agent_uuid,instance_uuid,status,last_seen_at,connection_uuid?,connected_at?,disconnected_at?` | 每 Edge 至多一个 registered/online session |
| `edge_command` | `edge_agent_uuid,sequence,type,payload JSON,status,trace_context JSON,sent_count,last_sent_at?,acked_at?` | `(edge_agent_uuid,sequence)` active 唯一；Backend 下行 Command outbox |
| `edge_event_inbox` | `edge_agent_uuid,edge_session_uuid,protocol_version,type,sent_at,payload JSON,processed_at` | `uuid` 即幂等 event identity；Backend 上行 Inbox |
| `frontend_event` | `sequence INTEGER PK,uuid,create_time,type,aggregate_uuid,payload JSON` | SSE 失效通知；不是领域表 BaseModel |

Backend 没有 `device_property_latest/history`。设备高频遥测属于 Edge 投影；Backend 如需持久化，
应另立遥测/时序契约，不能把 Edge EAV 表直接当共享领域 Schema。

## Go standalone / Lab target-design 附录

本节不是 d552078 migration 清单。它只登记导师提供、待实机复核的
`leaplab/designs@24fc4ce` 目标，并与上面的 `backend-implemented-candidate` 隔离。

### Go 业务后端职责

target-design 中，Go 业务后端拥有 Workflow/Resource 版本、业务持久化、运行投影、发布、鉴权和
前端网关；每个调度作用域由一个 Active Host OS 负责 DAG、就绪性、资源占用、设备动作和运行日志。
Go 不计算 `ready`、不持有活锁、不调用 Scheduler、不逐节点派发。该角色拆分尚未落入 d552078，
并与当前 `backend_controlled` 术语存在待版本化的迁移差异。

### 目标数据库与最小逻辑表

| Go target | 唯一数据库 writer | 目标最小逻辑表 |
|---|---|---|
| standalone | Go 进程独占 `workspace.sqlite` | `workflow`, `node`, `edge`, `version`；`resource`, `material`, `site`；`reagent_info`, `reagent`；`inventory_reservation`, `inventory_ledger`；`task`, `execution_event`, `run_trace`, `publish_record`；`local_owner`, `workspace`, `schema_migration` |
| Lab 开发 | Lab Go 进程独占 `lab-dev.sqlite` | `lab`, `workstation`；`published_workflow_version`, `composite_capability`；`lab_workflow`, `lab_workflow_version`, `lab_task`；`resource`, `material`, `site`, `location`；`reagent`；`reservation`, `material_ledger_entry`；`execution_event`, `run_trace`, `plan_revision_metadata` |
| Lab 生产 | Lab Go 进程独占 PostgreSQL | 与 Lab 最小逻辑模型同义，物理 DDL/类型/索引可按 PG 优化 |

浏览器不直读任何数据库；OS 不打开 `workspace.sqlite`、`lab-dev.sqlite` 或 PG 业务表。以上名称尚无
本轮可验证 migration/API，实施必须另行冻结 Base、FK、unique、软删除、时间、版本和鉴权。
设计稿中的短名 `node/edge/version/resource/task/reservation` 也不是 d552078 现有
`workflow_node/workflow_edge/...` 表的自动 rename；逐项映射、数据迁移和兼容 View 仍需冻结。

`site` 与 `location` 是两个实体：`site.uuid` 标识 carrier 内可占用库位（Site），`location` 表达更
宽泛空间/物流位置。二者的 FK、移动转换和生命周期仍是 Schema TODO，不能用 rename 代替。

### 发布、导入与 Site identity

- `os-quick-debug` 由 Host Edge 创建并持久化 Material/Site UUID。
- `go-standalone` 新建聚合由 Go 创建 UUID；导入 Quick Debug 聚合时必须接受 Edge UUID 或使用
  显式 identity mapping。
- `go-lab-dev/go-lab` 由 Lab Go 创建 UUID，OS hydration 原样沿用。
- `workspace.sqlite` 与 `lab-dev.sqlite` 不复制表行；只发布不可变版本制品。
- Quick Debug → Standalone 逻辑 export/import 携带 `schema_version/content_hash`；同 UUID 同 hash
  幂等，同 UUID 异 hash 冲突。

外部 Site UUID import/upsert、可信 ledger `operator_type/user` 注入和冲突响应仍是 Go Interface
TODO；不得把 d552078 当前 Backend 内部分配 UUID 的 create 路径描述成已经支持这些目标。
