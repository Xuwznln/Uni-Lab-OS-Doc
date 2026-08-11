# Edge SQLite 物理 Schema 证据（v7，2026-08-10）

本文记录隔离分支继承提交 `cd2d409a007e233aec0e9422359bf85c5427e37b`、同步受指导模型
`92743142572fcfdc69a2424945d5dcffd846920b` 后，
由当前源码实际建出的 SQLite 对象。证据来自空库初始化后的 `sqlite_master`、
`PRAGMA user_version/table_info/foreign_key_list/index_list`。`TEXT JSON` 与 Backend
的 JSON 类型是允许的物理差异；语义、标识、约束和公开 DTO 才是对齐目标。

本文主体由两类已实现证据组成：v7 shared 表和当前控制面属于 `edge-live`；旧三库、
旧 Workflow 审计表和兼容 View 属于 `legacy-implemented`。导师提供、待实机复核的
`leaplab/designs@24fc4ce` `os-local.sqlite` v1 只在文末作为 `target-design` 登记，不会自动改名、
合并或删除当前 Edge 表。

## `inventory.db`：user_version=7

### Backend 共享资源表

下列六张表已使用 Backend 字段名、Base 字段、软删除、FK 和 active unique：

- `resource_template`：字段与 Backend 当前表一致；缺少的仅是 Backend 后来增加的分页
  cursor 索引，业务唯一键/类型索引一致。
- `resource_handle_template`：字段一致；业务唯一键
  `(resource_template_uuid,io_type,name) WHERE deleted_at IS NULL` 一致。
- `material`：包含 Backend d123ce0 的实例 `type`，与 template/self-parent FK、条码、根名称、
  parent/template/type active 索引一致；旧值从组件 `config_info.type`、模板
  `resource_type`、最终 `resource` 依次回填。
- `relative_position`：字段和一 Material 一 active position 约束一致；Edge 为尺寸提供
  `0` 默认值，Backend 要求调用方给值，这是物理默认差异。
- `site`：字段、两个 Material FK、owner/name、occupant 唯一约束、排序索引一致；v7 独立
  持久化 `content_type` JSON array；Edge 额外显式禁止 owner=occupant，与 Backend 服务校验同义。
- `material_state_history`：字段、FK、时间线索引一致。

公共资源 API 从这些表读写，正常查询统一过滤 `deleted_at IS NULL`。删除 Material/模板/位置
采用软删除；历史记录不靠物理删除表达状态。

### v6/v7 已实现的 Backend 对齐

- `material.type` 为服务端派生事实；Material create/update DTO 不授予客户端写权。
- create 接收 `data`，递归合并模板组件默认值和请求值，并为根与每个组件写初始
  `material_state_history`。
- `config_info` 第一项物化根，其余项全部作为根的直接子 Material；模板里的组件 UUID/parent
  不复用，Site 也为每个实例分配新 UUID。
- `site.content_type` 与 `allowed_resource_template_uuids` 分开持久化。创建 Site 时不依赖当前
  模板注册顺序；物料放入 Site 时才以大小写不敏感的 `resource_template.tags` 动态准入，
  显式 UUID 或类型 tag 任一命中即可。`bottle(s)`、`bottle_carrier(s)`、`tip_rack(s)` 兼容
  单复数别名，其余值精确匹配；两组规则均为空表示不限制。
- Material 更新是 partial：显式 `null` 与省略均保留旧普通字段，`config` 整体替换但保护模板
  `sites`，`data/class/type/resource_template_uuid` 不可由更新请求改变。
- Material 列表默认 `with_children=false`；需要完整树的内部同步显式请求子组件。
- 删除根 Material 递归软删除组件子树、其 Site/position，并清除其他 Site 对该子树的占用。
- `published_workflow_contract` 仍是 Backend-only 管理能力，不复制进 `inventory.db`，Edge session
  也不广告 publication capability。

迁移器还识别曾与 canonical v5 撞号的旧 Edge-local v5（物理 `material_instance` 且无
`material`）：先保存旧 `type`，重跑 canonical v5，再执行 v6 回填并覆盖保存值。v7 使用
replay-safe additive migration 增加 `site.content_type`，既有 Site 默认 `[]`。真实 v5 数据库
副本验证结果为 13/13 Material 保留、10 个 Site 保留、外键检查 0 错误。

### Edge 本地运行态与同步表

| 表 | 字段 | 分类 / 权威 |
|---|---|---|
| `resource_template_inventory` | `resource_template_uuid PK,aggregate_version` | Edge 私有 revision sidecar；不进入 Backend DTO |
| `material_inventory` | `material_uuid PK,legacy_cloud_id,legacy_template_id,lot_id,inventory_status,disposition,aggregate_version` | Edge 私有同步/旧库存 sidecar；不与 Material 字段混写 |
| `material_content_version` | `material_uuid PK,version` | Edge 私有内容 revision |
| `inventory_lot` | `lot_id PK,template_id,batch_no,unit,quantity_total/available/reserved,expiry,quarantined,warehouse_zone_id,created_at(ms),version` | Edge 权威运行态；当前 Backend 没有同语义实体 |
| `inventory_reservation` | `reservation_id PK,workflow_id,node_id,attempt,status,amounts_json,created_at(ms),version` | Edge 本地库存预留；不是 Backend `execution_lock_lease` |
| `inventory_ledger` | `ledger_id INTEGER PK,occurred_at(ms),op_type,aggregate_type/id,delta_json,actor,reason,causation_id,trace_id,span_id` | Edge 私有库存事件账；Backend 000049 已有 canonical `material_ledger_entry`，两者语义不同且 Edge 接入延后，不能改名冒充 |
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
| `material_instance` | canonical Material + inventory sidecar → `edge_uuid,legacy_cloud_id,lot_id,template_id,barcode,type,status,version,parent_uuid` | `edge_uuid == material.uuid`；`type` 只读投影；insert/update 写 canonical 行；终态 status 转软删除 |
| `resource_relation` | active Site occupancy → `parent_uuid,slot_id,child_uuid,version` | `parent_uuid=site.material_uuid`、`slot_id=site.name`、`child_uuid=site.occupied_material_uuid`；insert/update/delete 改 Site 占用 |
| `substance_content` | `material.data` + content revision → `instance_uuid,state_json,version` | insert/update 写 canonical Material data |

这些 View 是兼容入口而不是第二事实源。新代码不得直接建回同名表；写入后必须从 canonical
表回读。`resource_relation` 不暴露 `site.uuid`，因此只能用于旧的 owner+name 定位；公共 API
和新同步必须使用稳定 Site UUID。

## Backend-shaped `workflow` SQLite：user_version=1

实际表：`workflow`、`workflow_node_template`、`workflow_handle_template`、`workflow_node`、
`workflow_edge`、`workflow_task`、`workflow_node_job`、`workflow_task_command`、
`workflow_node_job_feedback_history`、`workflow_node_job_result`、`workflow_intervention`、
`workflow_manual_confirmation`、`execution_lock_lease`、`workflow_source_registration`、
`workflow_authoring`、`frontend_event`。

主要差异：

- `workflow` 基本字段、revision 与 active cursor index 一致。
- Node/Handle template 保留 Edge 私有 `authority_id`，v1 已补 active unique/type/cursor index；
  `resource_template` 位于 `inventory.db`，因此不伪造跨文件 SQLite FK。
- `workflow_node` 仍有 Backend migration 42 已删除的 `status`；这是兼容列，不应出现在公共 DTO。
- `workflow_edge` 多存 `workflow_uuid`，Backend 通过节点归属推导；Edge 当前仅有 Workflow FK，
  v1 已补四元组/target-handle active unique 与 source/target index；物理 Node/Handle FK 仍待旧图
  孤儿清理后再启用。
- `workflow_task.workflow_uuid` 已可空，并用
  `execution_kind/idempotency_key/request_fingerprint` CHECK 区分 workflow 与 ad-hoc device action；
  `input/output` 只作为旧数据兼容列继续保存，不进入公共 DTO。
- `workflow_node_job` 已补 Backend 状态/executor/check、attempt/command unique、deadline、in-flight
  和 local recovery 索引。Material/Edge Agent/Command 分属其他 SQLite/上游 Authority，依赖由
  adapter/service 校验而非跨文件 FK 表达。
- v1 新增 Task Command、Job Feedback/Result、Intervention、Manual Confirmation 与 Execution
  Lock Lease，同步落下 Backend 的状态枚举、JSON shape、active unique 与幂等索引。
- `frontend_event` 已从 `id/event/data` 原地转换为
  `sequence/uuid/type/aggregate_uuid/payload`；Store 暂时继续输出旧 `id/event/data` alias 供现有
  SSE 客户端兼容，同时带上 uuid 与 aggregate UUID。
- Backend-shaped Workflow Store/Interface 使用 canonical `succeeded`。Edge Local REST v1、旧
  Scheduler 快照和历史仍使用 `success`；集中 Adapter 在共享出入口执行
  `success → succeeded`，Local v1 输出才做反向兼容。
- v0 Task/Job/Event 行在单个 `BEGIN IMMEDIATE` 事务内重建并保留；空库、旧库数据保留、事件
  cursor 和业务幂等约束均有回归测试。

Schema 已完成，不等于 Scheduler 运行闭环已完成。尚需把本地 Task 状态推进、控制命令消费、
反馈/结果提交、人工决策与 lease fencing 接到同一运行事务；`backend_controlled` 下仍以
`edge_control.db` 作为上下行投递镜像，不能双写出两个权威。

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
`interrupted` 等内部词汇不能成为 Backend canonical；仅 Edge Local REST v1 可经明确 Adapter
输出兼容 `success`。

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

## `os-local.sqlite` v1 target-design

本节来自导师提供、待实机复核的 `leaplab/designs@24fc4ce`。当前分支没有对应 migration、
`user_version=1` 建库代码或完整 Router，不能把目标名称描述成已存在表。

### OS 本地微后端职责与单写者

target-design 的 Active Host OS 负责 Slave 连接、ROS/HostLink 配置、DAG/Scheduler、资源占用、
设备动作和运行日志；每个调度作用域只能有一个 Active Host。OS 目标只打开
`os-local.sqlite`。兼容期可由同一 OS 进程独占现有 `inventory.db`、`device_state.db`、
`workflow_history.db`，但禁止另一个进程同时打开、禁止跨库双写，也不能据此声称已经完成合库。

当前 `edge_control.db` 仍是 `edge-live` 事实；提供的 target 表清单没有给出其 Command/Outcome
迁移落点。若目标最终要求 OS 只打开一个文件，必须先补 `edge_control.db` 的版本化迁移或替代
协议，不能静默丢弃未 ACK 事件和执行镜像。

### v1 目标逻辑表

| 目标表 | 目标职责 / 不变量 |
|---|---|
| `local_workflow` | Quick Debug 本地 Workflow identity |
| `local_workflow_version` | 本地不可变 Workflow version |
| `local_resource_config` | Active Host 本地资源配置版本 |
| `debug_task` | Quick Debug Task 持久业务记录 |
| `debug_node_event` | 节点运行事件；`(scope_id,task_id,seq)` 唯一 |
| `dag_cursor` | 已提交 DAG 事件位置 |
| `event_outbox` | Runtime Event Outbox；`event_id` 唯一 |
| `resource_snapshot_cache` | 可重建的资源快照缓存，不成为 Material/Site Authority |
| `plan_snapshot` | 冻结执行计划，不保存 live queue |
| `run_trace` | Task/节点/动作 Trace 关联，不承担幂等或状态权威 |
| `local_reagent_info` | Quick Debug 本地试剂信息 |
| `local_reagent_batch` | 本地试剂批次事实 |
| `local_material_binding` | Task 与 Material/Site 的冻结绑定 |
| `local_inventory_reservation` | Quick Debug 持久库存预留，不冒充活锁/Claim |
| `local_inventory_ledger` | Quick Debug 库存台账；不同于现有 `inventory_ledger` 和 Backend `material_ledger_entry` |

`dag_cursor`、终态 `debug_node_event` 和对应 `event_outbox` 必须同事务提交。活锁、lease、
`ready/running` queue、`PlannedOccupancy`、活甘特和当前 Scheduler epoch 不是 DB 权威；重启只能从
持久计划、事件和配置重建，不能把内存快照提升成 durable fact。
这里的活锁/lease 是 target-design 实时调度缓存，不等于已经接受“无持久作业执行占用”。
JobExecutionClaim/栅栏如何落入该目标仍是与 Core 持久执行安全模型的对齐 TODO。

### 两类 Outbox 不合表

- 库存 `sync_outbox` 以聚合 UUID、`aggregate_version` 和连续同步 cursor 表达 Inventory 增量。
- 运行时 `event_outbox` 以唯一 `event_id` 发布 `debug_node_event`；Go `execution_event` 按
  `(scope_id,task_id,seq)` 幂等接收。

二者的聚合、顺序、ACK 和重放失败语义不同。共同使用“outbox”模式不代表应共享物理表。
Backend-shaped 状态 Adapter、可信 actor 注入（local=`edge:local-api`，Backend command 使用
`backend:<claim>`）和本地 Site 实例物化已落地。跨 Authority 的外部 Material/Site UUID
import/upsert、持久 JobExecutionClaim/栅栏仍未闭合，不因 `os-local.sqlite` target 表登记而闭合。
