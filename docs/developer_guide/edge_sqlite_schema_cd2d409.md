# Edge SQLite 物理 Schema 证据（cd2d409）

本文记录隔离分支继承提交 `cd2d409a007e233aec0e9422359bf85c5427e37b` 后，
由当前源码实际建出的 SQLite 对象。证据来自空库初始化后的 `sqlite_master`、
`PRAGMA user_version/table_info/foreign_key_list/index_list`。`TEXT JSON` 与 Backend
的 JSON 类型是允许的物理差异；语义、标识、约束和公开 DTO 才是对齐目标。

## `inventory.db`：user_version=5

### Backend 共享资源表

下列六张表已使用 Backend 字段名、Base 字段、软删除、FK 和 active unique：

- `resource_template`：字段与 Backend 当前表一致；缺少的仅是 Backend 后来增加的分页
  cursor 索引，业务唯一键/类型索引一致。
- `resource_handle_template`：字段一致；业务唯一键
  `(resource_template_uuid,io_type,name) WHERE deleted_at IS NULL` 一致。
- `material`：字段和 self/template FK 一致；条码、根名称、parent/template 索引一致。
- `relative_position`：字段和一 Material 一 active position 约束一致；Edge 为尺寸提供
  `0` 默认值，Backend 要求调用方给值，这是物理默认差异。
- `site`：字段、两个 Material FK、owner/name、occupant 唯一约束、排序索引一致；
  Edge 额外显式禁止 owner=occupant，与 Backend 服务校验同义。
- `material_state_history`：字段、FK、时间线索引一致。

公共资源 API 从这些表读写，正常查询统一过滤 `deleted_at IS NULL`。删除 Material/模板/位置
采用软删除；历史记录不靠物理删除表达状态。

### Edge 本地运行态与同步表

| 表 | 字段 | 分类 / 权威 |
|---|---|---|
| `resource_template_inventory` | `resource_template_uuid PK,aggregate_version` | Edge 私有 revision sidecar；不进入 Backend DTO |
| `material_inventory` | `material_uuid PK,legacy_cloud_id,legacy_template_id,lot_id,inventory_status,disposition,aggregate_version` | Edge 私有同步/旧库存 sidecar；不与 Material 字段混写 |
| `material_content_version` | `material_uuid PK,version` | Edge 私有内容 revision |
| `inventory_lot` | `lot_id PK,template_id,batch_no,unit,quantity_total/available/reserved,expiry,quarantined,warehouse_zone_id,created_at(ms),version` | Edge 权威运行态；当前 Backend 没有同语义实体 |
| `inventory_reservation` | `reservation_id PK,workflow_id,node_id,attempt,status,amounts_json,created_at(ms),version` | Edge 本地库存预留；不是 Backend `execution_lock_lease` |
| `inventory_ledger` | `ledger_id INTEGER PK,occurred_at(ms),op_type,aggregate_type/id,delta_json,actor,reason,causation_id,trace_id,span_id` | Edge 私有库存事件账；不是 Backend `material_ledger_entry` |
| `sync_outbox` | `sequence INTEGER PK,event_id UNIQUE,edge_id,lab_id,aggregate_type/id,aggregate_version,event_type,occurred_at(ms),causation_id,payload_json,traceparent,tracestate,trace_id,span_id` | Edge 增量同步事实；`sequence` 是本 Edge 单调水位 |
| `processed_command` | `command_id PK,result_json,status,processed_at(ms)` | 下行命令幂等 Inbox |
| `sync_cursor` | `cursor_name PK,acked_sequence,updated_at(ms)` | 连续 ACK 水位；禁止倒退/越过空洞 |
| `lab_meta` | `meta_key PK,meta_value` | Edge 私有布局元数据 |
| `lab_zone` | `zone_id PK,name,kind,x,y,w,h,meta_json,version` | Edge 私有布局投影 |
| `lab_placement` | `subject_id PK,subject_kind,zone_id,x,y,w,h,rotation,label,meta_json,version` | Edge 私有布局投影 |

`edge_id/lab_id` 只属于同步 envelope 和 Edge 私有 scope，不应添加到 Backend 共享六表。
Material 稳定身份只使用 `material.uuid`；`edge_uuid/cloud_uuid` 仅存在于兼容投影。

### 旧兼容 View（仍可写）

| View | 映射 | 写入规则 |
|---|---|---|
| `inventory_resource_template` | canonical template + revision sidecar → `template_id,name,category,spec_json,version` | INSTEAD OF insert/update/delete；delete 转为软删除 |
| `material_instance` | canonical Material + inventory sidecar → `edge_uuid,legacy_cloud_id,lot_id,template_id,barcode,status,version,parent_uuid` | `edge_uuid == material.uuid`；insert/update 写 canonical 行；终态 status 转软删除 |
| `resource_relation` | active Site occupancy → `parent_uuid,slot_id,child_uuid,version` | `parent_uuid=site.material_uuid`、`slot_id=site.name`、`child_uuid=site.occupied_material_uuid`；insert/update/delete 改 Site 占用 |
| `substance_content` | `material.data` + content revision → `instance_uuid,state_json,version` | insert/update 写 canonical Material data |

这些 View 是兼容入口而不是第二事实源。新代码不得直接建回同名表；写入后必须从 canonical
表回读。`resource_relation` 不暴露 `site.uuid`，因此只能用于旧的 owner+name 定位；公共 API
和新同步必须使用稳定 Site UUID。

## Backend-shaped `workflow` SQLite：user_version=0

实际表：`workflow`、`workflow_node_template`、`workflow_handle_template`、`workflow_node`、
`workflow_edge`、`workflow_task`、`workflow_node_job`、`workflow_source_registration`、
`workflow_authoring`、`frontend_event`。

主要差异：

- `workflow` 基本字段和 revision 一致，但缺 Backend active cursor index。
- Node/Handle template 有 Edge 私有 `authority_id`，且缺完整 FK/active unique。
- `workflow_node` 仍有 Backend migration 42 已删除的 `status`；这是兼容列，不应出现在公共 DTO。
- `workflow_edge` 多存 `workflow_uuid`，Backend 通过节点归属推导；Edge 当前仅有 Workflow FK，
  缺 Handle/Node FK 和四元组/target-handle active unique。
- `workflow_task.workflow_uuid` 仍 NOT NULL，仍保留 Backend migrations 37/40 已删除的
  `input/output`，缺 `execution_kind/idempotency_key/request_fingerprint`，因此不能完整表达
  Backend 的 ad-hoc device action。
- `workflow_node_job` 字段接近 Backend，但缺 Edge Agent/Command/Material FK、attempt 唯一、
  deadline/recovery 索引；它不持久化 result/feedback/intervention/manual-confirmation/lock lease。
- 公共运行状态已使用 Backend 当前的 `succeeded`；老 Scheduler 内部历史仍使用 `success`，
  只能在 adapter seam 转换。
- Schema 没有 `user_version`，无法证明旧库进行了有序升级。

由于这些差异涉及现有工作流历史的迁移和运行职责分配，不能以一次无版本
`CREATE TABLE IF NOT EXISTS` 直接改写。

## `device_state.db`：user_version=0

- `device_property_latest`：复合 PK `(device_id,property)`，`value TEXT,value_type,updated_at INTEGER(ms)`。
- `device_property_history`：`id INTEGER PK,device_id,property,value,value_type,recorded_at INTEGER(ms)`，
  索引 `(device_id,property,recorded_at DESC)`。

这是 Edge 高频遥测投影（B 类），Backend 当前没有对应共享表；不使用 Base/软删除是刻意的
生命周期差异。

## `workflow_history.db`：user_version=0

- `workflow_runs`：`workflow_id PK,task_id,lab_id,priority,node_count,state,submitted_at/started_at/finished_at REAL(s),duration_s,spec_json`。
- `job_runs`：`id INTEGER PK,job_id,workflow_id,node_id,device_id,action_name,device_action_key,started_at/ended_at REAL(s),actual_s,estimated_s,estimate_source,state,suc_type,ret_json`。

这是旧 Scheduler 审计/回放投影，不是 Backend-shaped Workflow Authority。`success`、
`interrupted` 等内部词汇不得直接上送公共 API。

## `edge_control.db`：user_version=0

| 表 | 字段 | 角色 |
|---|---|---|
| `edge_control_meta` | `key PK,value` | session/sequence 恢复元数据 |
| `edge_command` | `command_uuid PK,sequence,type,payload_json,traceparent,tracestate,status,received_at REAL(s)` | Edge 下行 Command Inbox；`command_uuid` 是幂等 identity |
| `edge_event_outbox` | `event_uuid PK,type,payload_json,created_at,traceparent,tracestate,last_sent_at?,acked_at?` | Edge 上行 durable Outbox |
| `edge_job_runtime` | `job_uuid PK,task_uuid,node_uuid,command_uuid,job_access_token,status,feedback_sequence,traceparent,tracestate,updated_at REAL(s)` | Backend-controlled 执行镜像，不是 Workflow Authority |
| `edge_job_outcome_pending` | `job_uuid PK,outcome,return_info_json,error_info_json,updated_at REAL(s)` | 结果 HTTP 提交完成前的恢复记录 |

`traceparent/tracestate` 是跨进程传播格式；`trace_id/span_id` 是库存审计索引字段；两种形式不可
互相替代。所有外发 Command/Event 都以 UUID 幂等，sequence 只负责有序重放。
