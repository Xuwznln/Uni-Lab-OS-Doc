# Backend、Edge、前端三向数据契约对齐

## 结论

对 `backend-implemented-candidate/edge-live/legacy-implemented`，Backend 的最新领域实现语义是
当前对齐标准，Edge SQLite 是按运行职责裁剪的持久化适配，前端是客户端投影。新增
`target-design` 单独登记未来职责和存储边界，不能反向伪装成当前实现。目标是同一身份、字段语义、
枚举、envelope 和增量同步规则；不是让三方拥有相同数量的表，也不是把 PostgreSQL 与 SQLite
DDL 逐字做成一样。

当前结果：

1. Edge `inventory.db` 已升级到 v6：六张 Backend 共享资源表、`material.type`、Site 稳定 UUID、
   Handle、Material parent、初始状态历史和四个旧兼容 View 均已落地；碰撞的旧 Edge-local v5
   也有保留实例类型的迁移路径。
2. Backend `main@d552078` 与候选 `feat/workflow@d123ce0` 已按源码逐项复核。Edge 已对齐候选的
   Material/Site CRUD 与序列化语义；`published_workflow_contract` 保持 Backend-only，不复制表、
   不向 Edge session 广告发布能力。
3. Edge Workflow SQLite 仍只是“字段相近”，缺迁移版本、关键 FK/index、ad-hoc Task、
   feedback/result/intervention/lock 等持久事实，不能宣称与 Backend 相同。
4. `ResourceDict.sites` 已与 barcode 一样提升为根字段；PLR 双形态输入、序列化/反序列化、
   Host 图合并与实例 Site UUID 均有回归测试。
5. 本地 `unilab-edge-ui` 已直接审计并更新为 26 个 v6 实体：6 张 Backend-shared 表、4 个
   edge-compat View、Edge-private 侧表及设备/历史表；Resource HTTP client 只通过 base URL 在
   Host 微后端与正式 Backend 间切换，并校验 HTTP 200 内的业务错误码。
6. `leaplab/designs@24fc4ce` 仍是 Target Design，只定义未来 Active Host、`os-local.sqlite`、
   Go standalone/Lab 存储与发布边界，不能写成已经落地的 migration 或运行能力。

完整物理证据见：

- [Backend 当前 Schema](backend_schema_d552078.md)
- [Edge SQLite 当前 Schema](edge_sqlite_schema_cd2d409.md)
- [前端当前契约](frontend_contract_355e2fc.md)

## 基线与权威判定

| 仓库 | Ref / SHA | 判定 |
|---|---|---|
| `deepmodeling/Uni-Lab-OS` | `origin/feat/edge-networking-and-scheduler@195434ab738c1d5123e41d1ef08f2d17d30928c4` | Edge 远端契约分支；本轮 fetch 后未前进 |
| Edge 隔离实现 | `cd2d409a007e233aec0e9422359bf85c5427e37b` | 上轮基于 195434 的 v5/API 对齐提交，完整保留 |
| 受指导模型提交 | `origin/feat/backend-contract-d552078@92743142572fcfdc69a2424945d5dcffd846920b` | 已拉取；包含 Backend 审计、Host identity、Site/物料设计文档等 6 个提交 |
| 本地 Edge 对齐分支 | `feat/backend-contract-local-align-20260808`，v6 基线 `2ae63572` + 本次 Authority 修正 | v6 migration、精确 Material/Site CRUD、状态/actor Adapter 已提交并通过本地验证；默认微后端不产生旧 Backend 启动写入 |
| `Uni-Lab-OS/uni-lab-backend` 默认 `main` | `d5520789975d6aa14792b8c1bde6565050b5fcf8` | 2026-08-07 直接 fetch/`ls-remote` 核验；原 000047..000049 实现候选已进入默认分支 |
| Backend 当前实现候选 | `origin/feat/workflow@d123ce0a4e3b3ff834c26f4f02e3f9f53bea3b3e` | 相对 d552078 有 7 个可达提交；新增 000050..000054、`material.type`、已发布工作流契约及对应 model/Router/test |
| 私有 `Uni-Lab-OS/uni-lab-fe` | `origin/integration/fe-os-migration@355e2fc498e4d58701b71289cdd031beedef5afa` | 本轮直接审计的客户端投影证据；不是数据库标准 |
| 本地微前端 `unilab-edge-ui` | `/home/wz/unilab-context/unilab-edge-ui`，`feat/backend-resource-contract-v6@dd5337e`，clean | 已直接读取并验证：68 Edge ops、5 Cloud ops、17 actions、26 typed entities、58 tests；26/26 实体经 Vite 同源代理实连 Host 成功 |
| `leaplab/designs` | 导师提供：`78da9a7 → 24fc4ce`（8 个提交），原有 `uni-lab-scheduler` dirty 修改未动 | 待实机复核的 target-design；不证明 Go/OS migration、API 或进程入口已经实现 |

Backend `main@d552078` 是本轮直接核验的默认分支；`feat/workflow@d123ce0` 仍未进入默认分支，
所以新增 000050..000054 只标成“当前实现候选”，不是伪称已经发布。若该分支被改写或未合并，
必须重新生成本矩阵。

## 五类证据成熟度

表名相似不能跨成熟度自动继承“已实现”状态：

| 成熟度 | 判定标准 | 本轮证据 |
|---|---|---|
| `backend-implemented-candidate` | 已有 Backend migration/model/Router/test；是否已进默认分支由 Ref 列另记，不能只凭成熟度名称推断发布状态 | 默认 `main@d552078` 基线与 `feat/workflow@d123ce0` 候选增量 |
| `edge-live` | 当前 Edge 分支或隔离提交能实际建库、迁移并由现有 Interface 读写 | Edge v6 shared 表、Backend-shaped Resource API、`edge_control.db` 和当前路由 |
| `legacy-implemented` | 旧物理表、旧数据库或兼容 View 仍真实存在且可能仍可写，但不再定义 canonical target | `workflow_history.db`、v6 上的四个兼容 View |
| `local-draft` | 已在本机实现并通过测试、尚未提交的改动 | 当前对齐实现已落本地提交；本轮没有把尚未提交的设计当作已实现证据 |
| `target-design` | 设计目标或迁移方向；尚不能据此声称 Schema/API/进程已落地 | 导师提供、待实机复核的 `leaplab/designs@24fc4ce` |

当前 v6 表/View 属于已实现事实；新增 target-design 不会自动把它们改名、合库、删表或改变写入者。
任何后续迁移仍必须有版本化 DDL、兼容窗口和实库验证。

## OS 本地微后端、Go 业务后端与浏览器

“微后端”在本文只指 OS 本地微后端（OS Local Microbackend），不是 Go 业务后端的缩小版：

| 模块 | target-design 职责 | 明确不拥有 |
|---|---|---|
| OS 本地微后端 / Active Host | Slave 连接、ROS/HostLink 配置、DAG 推进、Scheduler、资源占用、设备动作、运行日志（Journal） | Go 业务版本发布、用户鉴权、跨 Workspace/Lab 业务数据库 |
| Go 业务后端 | Workflow/Resource 版本、业务持久化、运行投影、发布、鉴权、前端网关 | `ready` 计算、活锁、Scheduler 调用、逐节点派发、设备动作 |
| 浏览器 | 通过 Backend-shaped Interface 读写被授权业务操作、订阅短通知 | 直读 SQLite/PG、推进 DAG、持有锁、生成持久事实 |

每个调度作用域（Scheduler Scope）只能有一个活动 Host（Active Host）。Go 可以创建或接收业务
Task、保存投影并提供网关，但 target-design 下不成为第二个实时 Scheduler；Host/Slave 是执行拓扑，
不是两个并行调度权威。

这与当前 Uni-Lab-Core 对 `backend_controlled` 的既有定义存在迁移差异：当前定义把 Task/Job
调度权威留在 Backend，而 target-design 把业务持久化权威与实时调度权威拆开。接受该设计前应
版本化更新 SchedulerAuthorityProfile/ADR；本文不把 `backend_controlled` 静默重解释为新模式。

## 两个前端证据源与三层登记

两个前端不能合并成一个“当前前端”结论：

| 证据源 | 证据状态 | 它能证明什么 | 它不能定义什么 |
|---|---|---|---|
| 私有 `uni-lab-fe@355e2fc` | 本轮直接审计 | `MaterialAggregate`、`WorkflowRuntimePort`、Backend wire Adapter 和状态投影 | Backend/Edge 物理表清单 |
| 本地 `unilab-edge-ui@6c0db30e` dirty | 本轮直接审计并运行门禁 | 26 个 v6 typed entities、对象类型/schema version/authority 分层、Backend-shaped Resource client 与 mock CRUD | Backend/Edge 领域表的写入权威本身 |

本地微前端 catalog 应分三层维护，不以一层覆盖另一层：

1. **Backend canonical/shared Schema**：`resource_template`、`resource_handle_template`、
   `material`、`relative_position`、`site`、`material_state_history` 及 Backend Workflow
   领域表；默认实现以 d552078 为准，d123ce0 已落 migration/model 的候选增量单独登记。
2. **Edge 当前物理表/兼容 View**：v6 catalog 的物理表与四个兼容 View 到 canonical 表的映射；
   catalog 已标明 `objectKind/schemaVersion/authority` 和 CRUD disposition。
3. **Edge 私有同步表**：`sync_outbox`、`processed_command`、`sync_cursor` 等只承担增量同步、
   幂等和恢复，不进入 Backend 公共 DTO。

## Canonical 身份、时间和删除规则

| 主题 | Canonical 规则 | Edge 适配 |
|---|---|---|
| Material identity | `material.uuid` 唯一稳定身份 | 旧 `edge_uuid` 映射到同一个 UUID；`legacy_cloud_id` 仅 sidecar |
| Site identity | `site.uuid` 是位置自身 | 不得用 owner/occupant UUID、label、数组下标替代 |
| Location identity | target-design 的 `location` 是更宽泛空间/物流位置 | 不能改名替代 carrier 内可占用的 `site`；二者 FK/映射仍是 Schema TODO |
| 组成关系 | `material.parent_uuid` | 与 Site 占用独立；旧 relation 只能在兼容 seam 转换 |
| Edge 归属 | Backend `edge_agent.uuid` / Job `edge_uuid` | `edge_id` 可作为同步 sender key；不得成为 Material PK |
| Lab scope | 当前本地公共 profile 为 singleton | `lab_id` 只留在 Edge 私有同步 envelope/旧 history，不塞共享表 |
| revision | Workflow 用 `revision`；同步聚合用 `aggregate_version` | sidecar 存储，不伪造成 Backend Material 字段 |
| 幂等 | Backend Command/Event UUID；Edge `command_id/event_id` | UUID 负责幂等，sequence 负责顺序，二者不可互换 |
| Runtime event identity | target-design `debug_node_event(scope_id,task_id,seq)` 与 `event_outbox.event_id` 分别唯一 | 不与库存 `aggregate_version/sync_cursor` 混用 |
| 时间 | 公共 DTO RFC 3339；Backend `DATETIME` | Edge 私表允许 epoch ms/s，但必须在 adapter 明确转换 |
| Trace | HTTP/WS 使用 W3C `traceparent/tracestate`；审计可索引 trace/span ID | 不把 Trace 当业务幂等键 |
| 软删除 | shared Base 表使用 `deleted_at`，普通读取排除 | Edge shared 表已支持；遥测/outbox 等不同生命周期私表不强加 Base |
| 成功终态 | Backend Workflow 公共 wire 为 `succeeded` | Edge Local REST v1 可由 Adapter 输出 `success`；进入共享模型时必须规范化回 `succeeded` |

这里的 UUID 是 canonical 新身份规则。v5 实库迁移不会静默改写既有 `tpl-*` / `demo-*`
主键，所以 Host 微后端的 Backend-shaped 路由把 path/query/body identity 作为不透明字符串交给
服务层校验；正式 Backend 仍可在自身入口执行 UUID 校验。这样客户端只切 base URL，不需要为
旧 Edge 数据另写一套 DTO，也不会为了“看起来像 UUID”破坏已持久化引用。

## 三向逐表差异矩阵

分类随调度权威运行模式（SchedulerAuthorityProfile）解释：A=`local_scheduler` 下 Edge
权威运行态必须持久化并迁移（`backend_controlled` 下可退为投影/镜像）；B=只做 Interface、
同步 DTO 或 Adapter 转换；C=Backend/客户端专属，Edge 不存；D=语义冲突或接管策略待决策。

| Backend 实体 | Edge 当前映射 | 前端映射 | 差异类别 | 处置 |
|---|---|---|---|---|
| `resource_template` | v6 同名表 + revision sidecar | 26-entity catalog + Resource client | 同义物理差异 / A | shared 表与 CRUD DTO 已对齐；revision 留 sidecar |
| `resource_handle_template` | v6 同名表 | 独立只读实体 + Material Graph handles | 同义物理差异 / A | UUID、`io_type`、三元业务键一致 |
| `material` | v6 同名表；旧 `material_instance` View | 独立 C/R/U/D 实体 | A | `type`、data create、组件展开、partial update、根列表与 resource-parent 规则已对齐 |
| `relative_position` | v6 同名表 | 独立只读实体 + Material detail | A+B | Edge 持久化；写入仍经 Material 聚合 |
| `site` | v6 同名表；旧 `resource_relation` View | 独立只读实体 + Material Graph | A | 新实例 UUID、tag allow-list、owner/occupant 与根字段序列化已对齐 |
| `material_state_history` | v6 同名表 | 独立 append-only 实体 | A | create 初始状态及后续 append；无冻结状态枚举，按 DTO 透传 |
| `material_ledger_entry` | Backend 已存在；Edge 仅有不同语义 `inventory_ledger` | 私有前端当前无 port | Backend canonical / Edge 接入延后 | 不能别名；Edge 等可信 `operator_type/user` 注入后再镜像或写入 |
| `material_warehouse` | `inventory_lot` 是不同聚合 | 当前 UI 仓储投影未冻结 | C+D | 禁止直接互相改名；先统一批次/数量领域模型 |
| `reagent_info/reagent/sample` | 无 canonical 表 | 有部分 UI 类型 | C | Backend 专属；Edge 只在执行参数需要时用 DTO |
| `current_substance/substance_history` | `material.data` + content version + 私有 ledger | 前端 Material 内容投影 | D | Backend 是规范；迁移现有 tracker 历史需单位/重放策略，不能自动改表 |
| `workflow` | 同名表 | Workflow Runtime Port | A | revision/软删除一致；补迁移版本和索引 |
| `published_workflow_contract` | 无同义表；target `local_workflow_version` 不是其别名 | 私有前端 355e2fc 无对应 Port | Backend 专属 / B+C | d123ce0 的不可变发布制品留在 Backend；Edge 只通过版本制品 DTO/hydration 执行，不复制 Backend 行；本地 Quick Debug 版本另有 Authority |
| `workflow_node_template` | 同名表 + `authority_id` | Authoring catalog | A+B | 私有 authority 可留 sidecar/列，不进入共享 DTO；补 FK/index |
| `workflow_handle_template` | 同名表 + `authority_id` | Authoring Handle | A+B | `handle_key` 兼容寻址；边仍以 Handle UUID 为准 |
| `workflow_node` | 同名表但多 `status` | Authoring node | D | `status` 只能 legacy read；移除/回填需历史迁移决策 |
| `workflow_edge` | 多 `workflow_uuid`，缺关键 FK/unique | Authoring edge | A | 可保留冗余 workflow UUID，但必须校验一致并补四元组/target Handle unique |
| `workflow_task` | 缺 ad-hoc 字段，保留 input/output | Runtime Task | D | 当前 DB 不能表达 Backend 全集；需版本化重建/双读 |
| `workflow_node_job` | 字段近似但约束/事实表不完整 | Runtime Job | A+D | local Scheduler 要持久化；Backend-controlled 只保留 execution mirror，职责需分模式 |
| Job feedback/result | `edge_control` outbox/outcome pending，非同表 | Runtime REST projection | B+D | backend_controlled 只同步；local_scheduler 需新增 durable 事实表 |
| intervention/manual confirmation | Edge 执行控制有协议状态，无 canonical 表 | Runtime UI | B+D | Backend 权威模式只缓存必要恢复；本地权威模式需要完整表 |
| `execution_lock_lease` | 内存 `_job_resource_locks` + inventory reservation | 无数据库投影 | D | 不能把 reservation 冒充 lease；需决定本地 Workflow DB 迁移 |
| `edge_agent/session/binding` | Edge 本机配置和 control meta | 设备/Edge UI 投影 | B+C | Backend 权威；Edge 只保存自身 session 恢复信息 |
| Backend `edge_command` | Edge `edge_control.edge_command` | 不直接展示 | B | 字段不同但协议同义；Command UUID+sequence+Trace 转换 |
| Backend `edge_event_inbox` | Edge `edge_event_outbox` | SSE 只接短通知 | B | 一端 Outbox 对另一端 Inbox；不是表名对齐问题 |
| `frontend_event` | Workflow SQLite 有私有 event 表 | SSE invalidation | B+D | 事件 envelope/sequence 需对齐，物理表可不同 |
| device property latest/history | Edge 两张 EAV 表 | Device status adapter | Edge 私有 / A | Edge 遥测投影；Backend 没有同义共享表 |
| `sync_outbox/processed_command/sync_cursor` | Edge 私有表 | 无 | Edge 私有 / A | 保留；定义增量同步，不对前端开放 |
| `workflow_runs/job_runs` | Edge 旧审计库 | 无 | Edge 私有 / B | 内部事实保留 `success`；Backend-shaped 方向规范化为 `succeeded`，Local REST v1 响应由 Adapter 做 `succeeded → success` |

## target-design 数据库单写者与逻辑表

以下内容全部来自导师提供、待实机复核的 `leaplab/designs@24fc4ce`，成熟度是
`target-design`。它没有改变当前 Edge/Backend migration，也不证明对应 Router 已存在。

### 数据库所有权

| 运行层 / 模式 | 唯一写入进程 | 可打开的数据库 | 禁止事项 |
|---|---|---|---|
| Active Host OS | OS 本地微后端进程 | target 为 `os-local.sqlite`；兼容期可由同一进程独占打开 `inventory.db`、`device_state.db`、`workflow_history.db` | Go/另一个 Host 并发打开；跨进程共享 WAL；自动把三库合并成 target |
| `go-standalone` | Go 业务后端进程 | `workspace.sqlite` | OS 或浏览器直开；把 OS 运行日志写入 Workspace DB |
| `go-lab-dev` | Lab Go 业务后端进程 | `lab-dev.sqlite` | 与 `workspace.sqlite` 复制行；OS 取得 Lab 业务表写权 |
| `go-lab` | Lab Go 业务后端进程 | PostgreSQL | OS 直写 PG 业务表；浏览器绕过网关 |
| 浏览器 | 无数据库写入者身份 | 无 | 直读 SQLite/PG、读取 WAL、执行 migration |

每个文件同时只有一个写入进程和一个 migration owner。兼容期“同进程独占旧三库”是迁移
约束，不代表 `os-local.sqlite` v1 已经建成，也不授权复制旧表到新文件后双写。

### `os-local.sqlite` v1 目标表

| 目标表 | 目标职责 / 关键不变量 |
|---|---|
| `local_workflow`, `local_workflow_version` | Quick Debug 本地 Workflow 与不可变版本；不替代 Go 已发布版本 |
| `local_resource_config` | Active Host 使用的本地资源配置版本 |
| `debug_task` | OS Quick Debug Task 的持久身份和业务状态 |
| `debug_node_event` | 节点运行事件；`(scope_id,task_id,seq)` 唯一，是向 Go `execution_event` 投影的幂等键 |
| `dag_cursor` | 每个 Task 已提交到的 DAG 事件位置；与对应终态事件、Runtime Outbox 同事务推进 |
| `event_outbox` | 运行时事件发件箱；`event_id` 唯一，不与库存 `sync_outbox` 合表 |
| `resource_snapshot_cache` | 可从权威资源配置重建的执行缓存，不成为 Material/Site 第二权威 |
| `plan_snapshot` | 已冻结的本地执行计划快照；不保存实时就绪队列 |
| `run_trace` | Task/节点/动作运行 Trace 关联记录，不作为幂等键或状态权威 |
| `local_reagent_info`, `local_reagent_batch` | Quick Debug 本地试剂目录与批次事实 |
| `local_material_binding` | 本地 Task 与 Material/Site 的冻结绑定，不替代 Site 占用 |
| `local_inventory_reservation` | Quick Debug 持久库存预留；不冒充活锁或作业执行占用 |
| `local_inventory_ledger` | Quick Debug 本地库存台账；与 Backend `material_ledger_entry`、现有 Edge `inventory_ledger` 分别建模 |

同一个事务必须提交 `dag_cursor`、Task/节点终态 `debug_node_event` 和对应 `event_outbox` 行，避免
终态成功但通知永久丢失。活锁、lease、`ready/running` queue、`PlannedOccupancy`、活甘特和当前
Scheduler epoch 都不是该数据库的权威事实；重启后由持久事件/计划重新计算或重新取得。
这里的活锁/lease 指 target-design 的实时调度缓存，不能据此删除任务物料预留等业务事实；该设计
是否以及如何持久表达作业执行占用（JobExecutionClaim）与栅栏仍需与 Core 规范另行对齐。

### Go standalone 目标最小逻辑表

| 目标表组 | 目标职责 |
|---|---|
| `workflow`, `node`, `edge`, `version` | Workspace Workflow 创作图与不可变版本 |
| `resource`, `material`, `site` | Standalone 业务资源、Material 与稳定 Site identity |
| `reagent_info`, `reagent` | 试剂目录与实例/批次业务事实 |
| `inventory_reservation`, `inventory_ledger` | Standalone 业务预留与审计台账 |
| `task`, `execution_event`, `run_trace`, `publish_record` | 任务业务记录、OS 事件投影、Trace 和版本发布记录 |
| `local_owner`, `workspace`, `schema_migration` | 本地所有者、Workspace 与 Schema 版本治理 |

### Go Lab 目标最小逻辑表

| 目标表组 | 目标职责 |
|---|---|
| `lab`, `workstation` | Lab 与工作站归属 |
| `published_workflow_version`, `composite_capability` | 从 Workspace 发布的不可变版本制品与组合能力 |
| `lab_workflow`, `lab_workflow_version`, `lab_task` | Lab 采用的 Workflow/version 与任务业务投影 |
| `resource`, `material`, `site`, `location` | Lab 资源、Material、稳定 Site 与更宽泛空间/物流 Location |
| `reagent` | Lab 试剂业务事实 |
| `reservation`, `material_ledger_entry` | Lab 预留与 Material 不可变台账 |
| `execution_event`, `run_trace`, `plan_revision_metadata` | Active Host 事件投影、Trace 与计划版本元数据 |

`site` 和 `location` 不是新旧别名：Site 是 carrier 内可占用库位（Site）的稳定 UUID identity，
Location 是区域、工位或物流节点等更宽泛位置。二者如何引用、是否允许 Site 绑定 Location、移动时
哪个事实更新，仍是 target Schema TODO；实现前不能用重命名代替关系设计。

## Site UUID 单写者与拆装规则

### 跨模式不变量

1. Material 模板中的 `config_info[*].config.sites` 是**模板规格**：label、几何、允许类型；
   它没有实例 Site identity。
2. `site.uuid` 是库位（Site）自身稳定身份；`site.material_uuid` 是 owner Material；
   `site.occupied_material_uuid` 是 occupant Material。`material.parent_uuid` 独立表达组成关系。
3. 同一 Material 聚合在一个调度权威运行模式（SchedulerAuthorityProfile）中只能有一个
   Site UUID 生成者；label/index 只是语义键，不能代替 UUID。
4. Backend/Edge 公共 Material Graph 必须返回 Backend-shaped Site DTO；客户端只从 DTO 的
   `uuid/material_uuid/occupied_material_uuid` 构建 `MaterialAggregate`。
5. PLR 构造时由唯一 Adapter 把 Site DTO 组装成 `config.sites` 所需的
   `label/position/size/content_type/occupied_by`；PLR 序列化回 Edge 时再提升到
   `ResourceDict.sites`，`config` 中不保留第二份。
6. `data` 只保存运行状态，不能再存 Site 定义。兼容 `config.sites` 只在输入 Seam 读取一次，
   转换后必须剥离。
7. 切换权威必须走显式迁移/接管，传递全部既有 Site UUID 和版本；禁止 Backend 与 Host Edge
   并行生成后再按 label 猜测合并。

### `backend_controlled`

1. Backend 是 Material/Site Authority；创建 Material 聚合时由 Backend 分配并持久化
   `site.uuid`。
2. Backend 返回完整 Material Graph；Edge hydration 必须沿用返回 UUID，并写入
   `ResourceDict.sites`/运行时资源树，绝不重生成。
3. Edge 保存的是执行所需投影或镜像，不接受同一 Backend-owned 聚合的第二套本地身份写入。

### `local_scheduler` / 默认开源 Host

1. Host Edge 是其设备树和 Material Graph 的本地 Authority，有权创建本地 Material/Site。
2. 第一次从 PLR/`ResourceDict` 构造实例 Site 时，Host 按模板 Site 的稳定语义键识别库位，
   为每个实例库位分配一次 UUID，先写本地 canonical `site` 表，再序列化为根级
   `ResourceDict.sites`。
3. 后续重启、HostLink、ROS、HTTP 和 PLR 往返都从持久表/根字段复用 UUID；label 或数组位置
   变化不能触发重新分配。
4. 本地公共 DTO 仍使用 Backend 名称：表为 `site`，字段为 `uuid/material_uuid/
   occupied_material_uuid`，组成字段为 `material.parent_uuid`。

### Edge-origin 聚合同步到 Backend

1. Backend 必须提供 import/upsert 接口接收外部已分配的 `material.uuid` 与 `site.uuid`，并执行
   幂等、owner/occupant FK、active unique 和版本冲突检查；或者提供显式、持久的 identity
   mapping。
2. d552078 当前 Material create 路径由 Backend 内部分配 Site UUID，尚不能完整导入
   Host-owned Material Graph；这是 Backend 接口 TODO，不是禁止本地 Host 创建 Site 的理由。
3. 同步使用 event UUID 幂等、sequence 排序和 aggregate_version 防乱序；通知不能承担身份创建。

### target-design 运行模式映射

下表不重解释当前 `backend_controlled/local_scheduler`，而是登记 `leaplab/designs@24fc4ce`
待接受的新模式：

| target-design 模式 | Material/Site 业务权威与 UUID 生成者 | Active Scheduler | 身份进入 Go 的规则 |
|---|---|---|---|
| `os-quick-debug` | Host Edge；首次物化时生成并保存稳定 Site UUID | 当前作用域唯一 Active Host OS | 导出时保留 Material/Site UUID |
| `go-standalone` 新建 | Standalone Go | 当前作用域唯一 Active Host OS；Go 不算 `ready`、不逐节点派发 | Go 创建的新聚合由 Go 分配 UUID；Quick Debug 导入必须接受 Edge UUID 或显式 identity mapping |
| `go-lab-dev` | Lab Go（`lab-dev.sqlite` writer） | 当前 Lab scope 唯一 Active Host OS | OS hydration 原样沿用 Lab Go UUID |
| `go-lab` | Lab Go（PG writer） | 当前 Lab scope 唯一 Active Host OS | OS hydration 原样沿用 Lab Go UUID |

`workspace.sqlite` 与 `lab-dev.sqlite` 不通过复制数据库行同步。Workspace 只发布不可变版本制品
（Version Artifact），Lab 通过 `published_workflow_version`/能力元数据接收；发布后两边各自继续
承担自己的写入权威。

Quick Debug → Standalone 使用带 `schema_version` 与 `content_hash` 的逻辑 export/import：同 UUID、
同 hash 幂等返回既有对象；同 UUID、不同 hash 必须冲突并停止，不能覆盖，也不能按名称合并。
Material/Site import/upsert、identity mapping 与冲突响应仍是 Backend/Go Interface TODO。

### 当前实现证据与缺口

- 远端 Edge 195434/cd2d409 的 `ResourceDict` 模型没有 `sites` 字段；唯一漏斗没有提升；
  PLR/ROS/HostNode/graphio 退出路径也没有对称组装。
- 导师另行提供：用户另一份本地 Edge 脏工作区已实现根字段提升及测试；这不是
  `/home/wz/.../unilab-edge-ui` catalog 本身的实现。该 Edge 工作区的路径/ref 未在本环境提供，
  无法直接核对其 diff 和测试；它应记为“本地待合并实现”，不能写成远端已完成。
- 当前 OS 本地微后端创建 Material 已按模板递归物化 child Material/Site，并为每个实例 Site
  生成、持久化稳定 UUID；创建、查询、更新、递归软删除和占用清理均有 HTTP/服务层回归测试。
- `backend_controlled` 下，Edge `instance_sync` 只保留 Backend 返回的 Material UUID，没有把 Material Graph
  的 Site DTO hydration 回运行中的 `ResourceTreeSet`。
- `local_scheduler` 下，远端代码缺“首次生成一次、事务持久化、全链路复用”的实现证据。
- 旧 `resource_relation` View 只含 owner+label+occupant，不含 Site UUID，只能继续作为 legacy seam。
- HostNode 的旧资源树/registry 写桥只在显式 `material_source=backend` 时启用；默认
  `microbackend` 与 `auto` 不因 FastAPI 存在而向正式 Backend 产生启动写入。跨 Authority 的
  Edge-origin Material/Site import/upsert 仍需独立协议，不能用旧资源树上传替代。

### HostNode 名称

当前 SQLite Schema/FK 中没有写死 `host_node`。`unilabos/workflow/common.py` 的
`DEVICE_NAME_HOST="host_node"` 是旧工作流编译/路由常量，不是领域 FK；HostNode 改名不污染
Material/Site 表。后续应把它改成能力/绑定解析，但不需要为此新增数据库字段。

### Workstation 支线的隐含协议影响

2026-08-07 直接核对 `workstation_dev_YB_260410@7d6580cd` 的 1 个增量提交和
`workstation_dev_YB_260711@1cc17b46` 的 12 个增量提交：改动集中在 Bioyond/Neware/DataCore
设备驱动、设备 Registry YAML 和资源定义，没有 migration、共享 SQLite/PostgreSQL Schema、
Edge HTTP/WS envelope 或调度 Authority 变更。新增的 `_interlock_claim`/`_handoff_claim`
仍是进程内厂商设备互锁：重启后丢失，且 `_release_handoff_claim` 明确不校验 token，旧任务可能
释放后来任务的新占用。它们不是可持久化的 `JobExecutionClaim` 或 fencing token，驱动也不应
因此接管调度 Authority；不能据此关闭对应的持久化待办。

`bbaa40e0` 对 `host_node.py::manual_confirm` 的函数签名、装饰器和返回值均未改变，只把 docstring
说明改成“参数只读、人工修改不生效”，因此不构成 Host identity 或表设计变化。但这也不只是
不可见的源代码注释：AST Registry Scanner 会提取 action docstring，
Registry 再用 `parse_docstring` 把首行和参数说明写入动作 JSON Schema/目录元数据。故该提交会
改变前端或 template-sync 可见的动作描述，并与“批准人可编辑设备动作参数”的生产契约存在语义
冲突；应在 action catalog/人工确认契约中单独修正，不能用数据库 migration 处理。

## 增量与即时同步协议

| 方向 | 即时通知 | 数据传输 / 增量恢复 |
|---|---|---|
| Backend → 多个 Edge | WS Command 短消息：command UUID、type、sequence、Trace | Edge 先落 `edge_command`，再 HTTP 拉参数/提交结果；Command UUID 幂等，sequence 有序 |
| Edge → Backend | WS Event 短消息：event UUID、type、关联 identity | 业务 payload/状态经 HTTP；Edge Outbox 保留至 ACK，Backend Inbox 按 UUID 去重 |
| Inventory 增量 | 可发“aggregate changed”短通知 | `sync_outbox` 按 `sequence` 连续上传，Backend 按 `(edge_id,event_id)` 去重并按 `aggregate_version` 防乱序；cursor 只前进 |
| Runtime 事件 | 可发“Task/Node changed”短通知 | target `event_outbox` 投递 `debug_node_event`；Go `execution_event` 按 `(scope_id,task_id,seq)` 幂等，终态 event/outbox/cursor 同事务 |
| 前端 → Backend/Edge | SSE/WS 只使 REST cache 失效 | 前端对相同 capability 使用同一 REST DTO/envelope；不得根据部署类型猜字段 |

库存同步发件箱（Inventory Sync Outbox）`sync_outbox` 与运行时事件发件箱（Runtime Event Outbox）
`event_outbox` 具有不同聚合、游标和重放语义，不能因为名称都含 outbox 就合表。前者使用
`aggregate_version` 和库存同步 cursor；后者用 `event_id` 与 `(scope_id,task_id,seq)` 把
`debug_node_event` 幂等投影成 Go `execution_event`。

同步不允许 last-writer-wins。一次部署只能为某个聚合选择一个写入权威：

- `backend_controlled`：Backend 是 Workflow/Material Authority，Edge 保存执行镜像和 durable
  delivery；Site UUID 只由 Backend 生成，Edge 不接受同一聚合的第二份身份写入。
- `local_scheduler`：Edge 本地库是 Authority，并以 outbox/command/cursor 与上游同步；
  Host Edge 生成并持久化本地聚合的 Site UUID，Backend 只能导入/映射，不能重新生成。

## 当前模式（非 target-design）的 Authority 表

下表保留 195434/cd2d409 与 d552078 审计时使用的现有模式，不是
`leaplab/designs@24fc4ce` target-design 的最终命名；目标模式及数据库所有权见前文。

| 模式 / 层 | 写入权威与 UUID 生成者 | 必须持久化 | Interface / 同步 | 下一步 |
|---|---|---|---|---|
| `backend_controlled` Backend | Backend；Backend 生成 Material/Site UUID | Backend canonical 资源表、`material_ledger_entry`、Workflow/Task/Job/控制与事实表；候选分支另有 `published_workflow_contract` | Backend-shaped HTTP；WS 短通知 | 保持 d552078 默认语义；评审 d123ce0 的 `material.type`/发布契约后再版本化下发 |
| `backend_controlled` Edge | 无 Material/Workflow 第二权威；只执行 Backend Command | `edge_control.db` 的 Command/执行镜像/结果 Outbox；遥测投影；必要的只读 Material/Site 投影 | HTTP 拉参数/报结果，WS 通知；原样复用 Backend Site UUID | 保持 hydration 不重生成；补 durable JobExecutionClaim/栅栏 |
| `local_scheduler` / 默认开源 Host | Host Edge；Host 首次生成并持久化本地 Material/Site UUID | `inventory.db` v6 canonical 资源六表；版本化本地 Workflow/Task/Job Authority；Edge 私有 lot/reservation/outbox/cursor | 对前端暴露 Backend-shaped DTO；Local REST v1 状态经 Adapter 兼容 | 完成 Workflow Schema 与 JobExecutionClaim 迁移 |
| Edge-origin → Backend | 原 Host Authority 保留 identity；Backend 是导入后的接管方或投影方 | Backend 保存传入 UUID，或持久 identity mapping；不得生成冲突 UUID | import/upsert + event UUID + sequence + aggregate_version | 新增 Backend Material/Site import/upsert、冲突与接管协议 |
| `offline_recovery` | 只恢复原本由 Host Edge 创建的聚合/任务 | 复用既有本地 canonical 表与执行事实 | 不接管 Backend-owned 聚合；恢复后按原权威同步 | 增加显式 Authority/profile 校验，禁止断网自动接管 |
| 前端 / 微前端 | 无领域写入权威 | 仅客户端缓存/投影；catalog 不成为写模型 | 私有 `uni-lab-fe` 用 Adapter；`unilab-edge-ui` 已分三层登记并用同形 Resource client | 继续补 Workflow Authority 接管后的 DTO，不复制 Backend 发布表 |

三种 SQLite 私表不反向成为 Backend 标准：设备遥测投影保留
`device_property_latest/history`；同步恢复保留 `sync_outbox/processed_command/sync_cursor`；旧
`workflow_runs/job_runs` 只作审计投影。`inventory_ledger` 也不替代 Backend canonical
`material_ledger_entry`。

## 本轮可直接确认与不可直接实施

### 已确认、无需再改表

- shared resource 六表已覆盖 d123ce0 范围内的 UUID、FK、软删除、active unique 和
  `material.type`；现阶段不新增第二份 Material/Site 表。
- Site 三 UUID 的语义和公共 DTO。
- `edge_uuid/cloud_uuid` 只能在兼容 seam，canonical Material UUID 不分本地/云端。
- `command_id/event_id/sequence/aggregate_version/trace` 各自职责。
- Backend 公共运行终态是 `succeeded`；Edge Local REST v1 的 `success` 是兼容输出，必须由
  Adapter 显式执行 `succeeded → success`，反向进入共享模型时规范化为 `succeeded`。
- 共享出入口已经集中执行状态规范化；可信 actor 由 Adapter 注入，不接受客户端 JSON 自报。
- Material create/update/delete、组件展开、初始 state、Site tag allow-list、默认根列表以及
  `resource` parent/root 校验已经用 Backend-shaped contract test 固化。
- `published_workflow_contract` 与 publication/composite admin capability 保持 Backend-only；Edge
  不复制表、不向 session 广告这些 capability。

### 仍需决策 / 尚未闭环

1. **Backend Site import/upsert**：为 Edge-origin Material Graph 增加外部 Material/Site UUID
   导入或显式 identity mapping；必须有幂等、版本和冲突规则。
2. **Workflow 本地 Authority / JobExecutionClaim**：若继续支持 `local_scheduler` 的完整 Backend-shaped
   Workflow Authority？若支持，需要版本化新库/重建表并迁移现有 Task/Job；若生产只支持
   `backend_controlled`，则本地 store 应降级为执行镜像。两种模式都需要明确 durable claim、
   attempt/fencing token、重启恢复与重复执行防护，不能继续只靠 `_job_resource_locks` 内存集合。
3. **SchedulerAuthorityProfile 版本化**：决定是否接受“Go 业务权威 + Active Host OS 实时调度”并
   取代/新增于当前 `backend_controlled`；冻结 scope identity、接管和单 Active Host 失败语义。
4. **`os-local.sqlite` v1 migration**：定义完整字段、FK、unique、索引、`user_version`、旧三库读取
   兼容与单进程独占升级；设计表名本身不能替代 migration。
5. **Go standalone/Lab migration 与 Router**：分别为 `workspace.sqlite`、`lab-dev.sqlite`、PG
   冻结物理 Schema、DTO、事务和鉴权；不能把 target 逻辑表直接当已实现接口。
6. **Site ↔ Location 关系**：确定 FK 方向、生命周期、移动语义和投影；禁止用 rename 回避。
7. **版本制品与 Quick Debug import**：冻结 Version Artifact、`schema_version/content_hash`、
   同 UUID 异 hash 冲突响应，以及 Site UUID import/upsert/identity mapping。
8. **双 Outbox 合同**：分别版本化库存 `sync_outbox` 与 runtime `event_outbox`，验证
   `debug_node_event → execution_event` 的 `(scope_id,task_id,seq)` 幂等和终态原子提交。

这些决策会改变 Backend 语义或旧库迁移取舍，本轮没有自行发明默认值。

## 验证证据

2026-08-08 本地门禁已直接完成：Edge `tests/app` 为 `382 passed, 1 xfailed`，HostLink
定向 Action 为 `4 passed`；聚焦 Backend Resource/迁移/同步测试为 `56 passed`；真实旧 v5 数据库副本迁移到 v6 后
`integrity_check=ok`、外键错误 0、13/13 Material type 与 10 Site 保留。微前端
`protocol:check` 输出 68 Edge operations、5 Cloud operations、17 inventory actions、
26 typed entities，`vitest` 为 58 passed，`vue-tsc + vite build` 通过；编译后的 Node 客户端
经 `:5180` 同源代理访问运行中的 Host，26/26 实体查询成功。

2026-08-07 增量审计将 d123ce0 用 `git archive` 解到独立临时目录，定向执行
`go test -count=1 ./internal/infrastructure/database ./internal/domain/model
./internal/service/resource ./internal/service/workflow ./internal/web`，五个 package 全部通过；
Backend d552078...d123ce0 的 `git diff --check` 通过。Uni-Lab-OS 两条 workstation 支线没有
migration/共享 Schema 变更，但它们各自的 `git diff --check` 因历史尾随空格返回 2，因此不能把
该静态门禁记为通过；这不影响本次按显式文件范围执行的 v6 功能测试。
