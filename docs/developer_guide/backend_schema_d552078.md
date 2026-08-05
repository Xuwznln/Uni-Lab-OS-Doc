# Backend 当前领域 Schema 证据（d552078）

本文记录 `uni-lab-backend` 的当前实现候选
`origin/feat/workflow@d5520789975d6aa14792b8c1bde6565050b5fcf8`。证据来自按顺序执行
`migrations/sqlite/000001..000049` 后的 `sqlite_master`、`PRAGMA table_info`、
`foreign_key_list`、`index_list`，并与 Go 领域模型及 Router 交叉核对。

这不是要求 Edge 把 PostgreSQL/SQLite DDL 逐字复制。它定义的是 Backend 领域语义、
公共 JSON 字段、约束和写入权威。

## 记号

- `Base`：`uuid UUID PK`、`create_time`、`update_time`、可空 `deleted_at`、可空
  `description`、`meta_data JSON object DEFAULT {}`。
- `JSON`/`JSON[]`：SQLite 物理类型为 `TEXT` 并由 `json_valid/json_type` 约束；
  PostgreSQL 使用对应 JSON 类型。
- `active unique`：仅对 `deleted_at IS NULL` 的行生效。
- 时间在公共 API 中是 RFC 3339；数据库使用 `DATETIME/timestamptz`，不是毫秒整数。

## 资源、物料、库位（Site）

| 表 | 字段（除 Base） | FK、唯一约束和索引 | 写入权威 / 公共 API |
|---|---|---|---|
| `resource_template` | `name, display_name, resource_type, header?, footer?, icon?, model JSON, module?, language?, tags JSON[], data_schema JSON, config_schema JSON, pose JSON, config_info JSON[], cover?, scene JSON[], device_params JSON, manufacturer_uuid?, ui_overlay JSON` | active unique `name`（区分大小写）；active `resource_type`、分页游标索引 | Backend 模板服务；`/api/v1/resource-templates*` |
| `resource_handle_template` | `resource_template_uuid, name, display_name, type, io_type, source?, key?, side?`；`io_type=source/target/bidirectional` | FK template RESTRICT；active unique `(resource_template_uuid,io_type,name)` | Backend 模板服务；随模板详情公开 |
| `material` | `resource_template_uuid, barcode, name, config JSON, data JSON, parent_uuid?, class` | FK template/self-parent RESTRICT；禁止自父；active unique `LOWER(barcode)`（非空）、根 Material 的 `LOWER(name)`；parent/template 索引 | Backend 资源服务；`/api/v1/materials*` |
| `relative_position` | `material_uuid, position_x/y/z, depth,length,width, scale_x/y/z, rotation_x/y/z` | FK Material RESTRICT；每个 active Material 至多一行 | Material 聚合内写；Material 详情/Graph 公开 |
| `site` | `material_uuid, name, sort_order, allowed_resource_template_uuids JSON[], occupied_material_uuid?, position_x/y/z, depth,length,width` | 两个 Material FK RESTRICT；禁止 owner=occupant；active unique `(material_uuid,LOWER(name))`；active occupant 全局唯一；owner+order 索引 | Material 聚合创建时由 Backend 分配 UUID；`/materials/{uuid}/sites`、`/sites/{uuid}`、Material Graph |
| `material_state_history` | `material_uuid,status?,state_data JSON,source?,observed_at` | FK Material RESTRICT；`(material_uuid,observed_at DESC,uuid DESC)` | append-only 状态事实；`/materials/{uuid}/states*` |
| `material_ledger_entry` | `uuid PK, material_uuid,event_type,operator_type,from_site_uuid?,to_site_uuid?,changes JSON,extension JSON,trace_id?,recorded_at`；没有 Base | `event_type=created/updated/deleted`；`operator_type=frontend/edge/system`；Site 变更前后不能相同；Trace ID 为 32 位小写 hex；FK Material/Site RESTRICT；时间线索引 | Backend 不可变操作台账；`GET /materials/{uuid}/ledger` |
| `material_warehouse` | `resource_template_uuid,name,alias?,sku?,spec?,unit,batch_no,order_no?,supplier,quantity,remaining,safety_stock,unit_price,storage_location?,operator?,inbound_at?,attachments JSON[],status` | FK template；active unique `(template,LOWER(batch_no))`；FIFO 索引 | Backend 仓储域；Edge 当前无共享实现 |

`Site.uuid` 是库位（Site）本身的稳定身份；`Site.material_uuid` 是拥有该库位的
Material；`Site.occupied_material_uuid` 是当前占用该库位的另一个 Material。三者不得互换。
Material 的 `parent_uuid` 表示组成关系，库位占用不改变组成关系。

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
