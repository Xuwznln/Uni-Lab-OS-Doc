# Backend、Edge、前端三向数据契约对齐

## 结论

Backend 的最新领域实现语义是标准，Edge SQLite 是按运行职责裁剪的持久化适配，前端是
客户端投影。目标是同一身份、字段语义、枚举、envelope 和增量同步规则；不是让三方拥有
相同数量的表，也不是把 PostgreSQL 与 SQLite DDL 逐字做成一样。

当前结果：

1. Edge `inventory.db` v5 的六张共享资源表已经与 Backend 000046 前的领域语义对齐，
   软删除、Site 三个 UUID、Handle、Material parent 和状态历史都已落表。
2. Backend 当前实现候选在 `d552078` 新增物料执行锁、动作身份回填、物料操作台账和
   Edge Material 变更通知；这些尚未完整进入 Edge 本地 Authority。
3. Edge Workflow SQLite 只是“字段相近”，缺迁移版本、关键 FK/index、ad-hoc Task、
   feedback/result/intervention/lock 等持久事实，不能宣称与 Backend 相同。
4. 当前远端 Edge 并没有 `ResourceDict.sites` 根字段；PLR `sites` 仍在 `config`。由于
   Backend 在 Material 创建事务内分配 `Site.uuid`，Edge 不能自行生成另一套 UUID。
5. 当前前端已没有旧 `STANDARD_TABLES/DATA_ENTITIES/data-model.md` 登记。真实契约是
   `MaterialAggregate`、`WorkflowRuntimePort` 和 adapter；其中 Material create DTO、
   `admission_blocked`、Task `input/output` 仍与 Backend 有冲突。

完整物理证据见：

- [Backend 当前 Schema](backend_schema_d552078.md)
- [Edge SQLite 当前 Schema](edge_sqlite_schema_cd2d409.md)
- [前端当前契约](frontend_contract_355e2fc.md)

## 基线与权威判定

| 仓库 | Ref / SHA | 判定 |
|---|---|---|
| `deepmodeling/Uni-Lab-OS` | `origin/feat/edge-networking-and-scheduler@195434ab738c1d5123e41d1ef08f2d17d30928c4` | Edge 远端契约分支；本轮 fetch 后未前进 |
| Edge 隔离实现 | `cd2d409a007e233aec0e9422359bf85c5427e37b` | 上轮基于 195434 的 v5/API 对齐提交，完整保留 |
| `Uni-Lab-OS/uni-lab-backend` 默认 `main` | `c35d82146854a1e56a0219561076dd1101a6c49f` | 已发布基线，但不含最新 migration/model/router |
| Backend 当前实现候选 | `origin/feat/workflow@d5520789975d6aa14792b8c1bde6565050b5fcf8` | 相对 c35d821 前进 13 commits；包含 000047..000049、对应模型、仓储、Service、Router 和测试，作为本轮行为 oracle |
| `Uni-Lab-OS/uni-lab-fe` | `origin/integration/fe-os-migration@355e2fc498e4d58701b71289cdd031beedef5afa` | 当前前端集成契约证据；不是数据库标准 |

Backend `feat/workflow` 尚未进入默认分支，因此本文把它标成“当前实现候选”，不是伪称已经
发布的生产默认。若该分支被改写或未合并，必须重新生成本矩阵。

## Canonical 身份、时间和删除规则

| 主题 | Canonical 规则 | Edge 适配 |
|---|---|---|
| Material identity | `material.uuid` 唯一稳定身份 | 旧 `edge_uuid` 映射到同一个 UUID；`legacy_cloud_id` 仅 sidecar |
| Site identity | `site.uuid` 是位置自身 | 不得用 owner/occupant UUID、label、数组下标替代 |
| 组成关系 | `material.parent_uuid` | 与 Site 占用独立；旧 relation 只能在兼容 seam 转换 |
| Edge 归属 | Backend `edge_agent.uuid` / Job `edge_uuid` | `edge_id` 可作为同步 sender key；不得成为 Material PK |
| Lab scope | 当前本地公共 profile 为 singleton | `lab_id` 只留在 Edge 私有同步 envelope/旧 history，不塞共享表 |
| revision | Workflow 用 `revision`；同步聚合用 `aggregate_version` | sidecar 存储，不伪造成 Backend Material 字段 |
| 幂等 | Backend Command/Event UUID；Edge `command_id/event_id` | UUID 负责幂等，sequence 负责顺序，二者不可互换 |
| 时间 | 公共 DTO RFC 3339；Backend `DATETIME` | Edge 私表允许 epoch ms/s，但必须在 adapter 明确转换 |
| Trace | HTTP/WS 使用 W3C `traceparent/tracestate`；审计可索引 trace/span ID | 不把 Trace 当业务幂等键 |
| 软删除 | shared Base 表使用 `deleted_at`，普通读取排除 | Edge shared 表已支持；遥测/outbox 等不同生命周期私表不强加 Base |

## 三向逐表差异矩阵

分类：A=Edge 权威运行态必须持久化并迁移；B=只做 API/同步 DTO/转换；C=Backend/前端
专属，Edge 不存；D=语义冲突，先决策。

| Backend 实体 | Edge 当前映射 | 前端映射 | 差异类别 | 处置 |
|---|---|---|---|---|
| `resource_template` | v5 同名表 + revision sidecar | Template catalog | 同义物理差异 / A | 维持 shared 表；revision 留 sidecar |
| `resource_handle_template` | v5 同名表 | Material Graph handles | 同义物理差异 / A | UUID、`io_type`、三元业务键一致 |
| `material` | v5 同名表；旧 `material_instance` View | `MaterialAggregate.material` | A+D | UUID/parent/soft delete 已对齐；Backend 新增 `resource` 必须有 parent，且列表默认不含 child（`with_children=false`），Edge 现有默认行为不同，不能静默切换 |
| `relative_position` | v5 同名表 | `placement`/pose | A+B | Edge 持久化；前端只做坐标投影 |
| `site` | v5 同名表；旧 `resource_relation` View | `MaterialAggregate.sites` | A+D | 表已对齐；身份分配/ResourceDict hydration 未闭环，见 Site 决策门 |
| `material_state_history` | v5 同名表 | 当前无独立 UI 表 | A | append-only；状态无冻结枚举，按 DTO 透传 |
| `material_ledger_entry` | 仅有不同语义 `inventory_ledger` | 当前无 port | D | 不能别名；需决定 local authority 的 actor/ledger API 后新增表 |
| `material_warehouse` | `inventory_lot` 是不同聚合 | 当前 UI 仓储投影未冻结 | C+D | 禁止直接互相改名；先统一批次/数量领域模型 |
| `reagent_info/reagent/sample` | 无 canonical 表 | 有部分 UI 类型 | C | Backend 专属；Edge 只在执行参数需要时用 DTO |
| `current_substance/substance_history` | `material.data` + content version + 私有 ledger | 前端 Material 内容投影 | D | Backend 是规范；迁移现有 tracker 历史需单位/重放策略，不能自动改表 |
| `workflow` | 同名表 | Workflow Runtime Port | A | revision/软删除一致；补迁移版本和索引 |
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
| `workflow_runs/job_runs` | Edge 旧审计库 | 无 | Edge 私有 / B | 只读历史投影；内部 `success` 在边界转 `succeeded` |

## Site 唯一拆装规则

### 已确定规则

1. Material 模板中的 `config_info[*].config.sites` 是**模板规格**：label、几何、允许类型；
   它没有实例 Site identity。
2. 创建 Material 聚合时，选定的写入权威为每个实例库位分配 `site.uuid`，写入 `site` 表；
   `site.material_uuid` 指向拥有它的 Material。
3. 占用关系只写 `site.occupied_material_uuid`。Material `parent_uuid` 仍表示组成，不因摆放改变。
4. Backend/Edge 公共 Material Graph 必须返回 Backend-shaped Site DTO；前端只从 DTO 的
   `uuid/material_uuid/occupied_material_uuid` 构建 `MaterialAggregate`。
5. PLR 构造时由唯一 adapter 把 Site DTO 组装成 `config.sites` 所需的
   `label/position/size/content_type/occupied_by`；PLR 序列化回 Edge 时再提升到
   `ResourceDict.sites`，`config` 中不保留第二份。
6. `ResourceDict.sites` 只能由已持久化 Material Graph hydration 得到；不得按 label/index
   临时生成另一个 UUID。若没有权威 Site UUID，资源实例尚未完成初始化，应 fail closed。
7. `data` 只保存运行状态，不能再存 Site 定义。兼容 `config.sites` 只在输入 seam 读取一次，
   转换后必须剥离。

### 当前缺口

- Edge 195434/cd2d409 的 `ResourceDict` 模型没有 `sites` 字段；唯一漏斗没有提升；
  PLR/ROS/HostNode/graphio 退出路径也没有对称组装。
- Edge microbackend 创建 Material 不按模板展开 child Material/Site；测试通过直接 SQL 插入 Site，
  不能证明生产创建闭环。
- Backend 创建 Site UUID，Edge `instance_sync` 只保留返回的 Material UUID，没有把 Material Graph
  的 Site DTO hydration 回运行中的 `ResourceTreeSet`。
- 旧 `resource_relation` View 只含 owner+label+occupant，不含 Site UUID，只能继续作为 legacy seam。

### HostNode 名称

当前 SQLite Schema/FK 中没有写死 `host_node`。`unilabos/workflow/common.py` 的
`DEVICE_NAME_HOST="host_node"` 是旧工作流编译/路由常量，不是领域 FK；HostNode 改名不污染
Material/Site 表。后续应把它改成能力/绑定解析，但不需要为此新增数据库字段。

## 增量与即时同步协议

| 方向 | 即时通知 | 数据传输 / 增量恢复 |
|---|---|---|
| Backend → 多个 Edge | WS Command 短消息：command UUID、type、sequence、Trace | Edge 先落 `edge_command`，再 HTTP 拉参数/提交结果；Command UUID 幂等，sequence 有序 |
| Edge → Backend | WS Event 短消息：event UUID、type、关联 identity | 业务 payload/状态经 HTTP；Edge Outbox 保留至 ACK，Backend Inbox 按 UUID 去重 |
| Inventory 增量 | 可发“aggregate changed”短通知 | `sync_outbox` 按 `sequence` 连续上传，Backend 按 `(edge_id,event_id)` 去重并按 `aggregate_version` 防乱序；cursor 只前进 |
| 前端 → Backend/Edge | SSE/WS 只使 REST cache 失效 | 前端对相同 capability 使用同一 REST DTO/envelope；不得根据部署类型猜字段 |

同步不允许 last-writer-wins。一次部署只能为某个聚合选择一个写入权威：

- `backend_controlled`：Backend 是 Workflow/Material Authority，Edge 保存执行镜像和 durable
  delivery；不接受前端写本地第二份 Material。
- `local_scheduler`：Edge 本地库是 Authority，并以 outbox/command/cursor 与上游同步；
  Backend 不同时接受同一聚合的独立写入。

## 本轮可直接确认与不可直接实施

### 已确认、无需再改表

- shared resource 六表的 UUID、FK、软删除和 active unique。
- Site 三 UUID 的语义和公共 DTO。
- `edge_uuid/cloud_uuid` 只能在兼容 seam，canonical Material UUID 不分本地/云端。
- `command_id/event_id/sequence/aggregate_version/trace` 各自职责。
- Backend 公共运行终态是 `succeeded`；`success` 仅允许在旧 Scheduler 内部 adapter 前存在。

### 最小决策题

1. **Site hydration 时序**：是否采用“先由选定 Authority 创建 Material 聚合并返回完整
   Material Graph，再构造/刷新 `ResourceTreeSet`”作为唯一流程？推荐是。若不是，必须扩展
   Backend create DTO 接受调用方提供的 Site UUID，并规定冲突检查；不能让两端各自生成。
2. **Edge 本地物料台账 actor**：Edge shared route 如何可靠区分 `frontend/edge/system`？推荐由
   已认证 adapter 在调用 Service 时注入，不接受客户端 JSON 自报。确定前不创建空壳
   `material_ledger_entry` 或把 `inventory_ledger` 改名。
3. **Workflow 本地 Authority 迁移**：是否继续支持 `local_scheduler` 的完整 Backend-shaped
   Workflow Authority？若支持，需要版本化新库/重建表并迁移现有 Task/Job；若生产只支持
   `backend_controlled`，则本地 store 应降级为执行镜像，不能同时承诺完整 CRUD。
4. **前端 Material create 命令**：采用 Backend 当前 DTO，还是把 Backend 升级为前端聚合命令
   `template_id/placement/expected_revision/idempotency_key`？路径不能继续同名不同义。
5. **Material 根列表兼容窗口**：Backend 候选分支已经令 `with_children=false` 默认只返回根
   Material，并要求 `resource_type=resource` 的实例必须有 parent；Edge 旧客户端默认读取全部且允许
   根 Resource。需要先冻结发布版本、兼容期和 capability，再改变 Edge 默认行为。

这些决策会改变 Backend 语义或旧库迁移取舍，本轮没有自行发明默认值。
