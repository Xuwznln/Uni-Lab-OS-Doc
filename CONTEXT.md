# Edge 资源与工作流领域上下文

本文定义前端、Go 业务后端与 OS 本地微后端共用的领域语言。Go 业务后端与 OS 本地微后端
共享 Backend-shaped Interface，但按显式运行模式承担不同权威。数据库物理类型可以不同，
领域身份、字段语义、状态词汇、envelope 和幂等规则必须一致。

## 领域语言

**共享接口（Shared Interface）**：
Backend 与 Edge 对前端呈现相同的资源和工作流协议，包括路由语义、字段、枚举、envelope
和错误。避免使用“相似接口”、Edge-shaped response、前端 fallback。

**OS 本地微后端（OS Local Microbackend）**：
中文定义：运行在 Active Host 内、连接 Slave 和设备并拥有本作用域实时调度、资源占用、动作执行与
运行日志的 Backend-shaped 本地 Interface；它不是 Go 业务持久化的别名。
中文避免：临时假后端、Go Backend 代理、第二个同作用域调度器、只有 HTTP 路由的空壳。
English:
The Backend-shaped local Interface inside the Active Host that connects Slaves and devices and owns real-time scheduling, resource claims, device actions, and the runtime journal for its scope.
_Avoid_: temporary fake backend, Go Backend proxy, a second Scheduler in one scope, HTTP-only shell

**Go 业务后端（Go Business Backend）**：
中文定义：拥有工作流与资源版本、业务持久化、运行投影、发布、鉴权和前端网关的业务模块；在
Active Host 目标设计中不计算就绪性、不持有活锁、不调用调度器也不逐节点派发。
中文避免：Active Host、实时调度器、设备执行器、浏览器数据库代理。
English:
The business module that owns Workflow and Resource versions, durable business facts, runtime projections, publication, authorization, and the frontend gateway, but not Active-Host readiness or device dispatch.
_Avoid_: Active Host, live Scheduler, device executor, browser database proxy

**调度作用域（Scheduler Scope）**：
中文定义：必须由一个且仅一个活动 Host 推进 DAG、资源占用与设备动作的隔离运行范围。
中文避免：跨 Workspace/Lab 共享活锁、两个 Host 同时推进、靠数据库文件名猜作用域。
English:
An isolated runtime scope whose DAG, resource claims, and device actions are advanced by exactly one Active Host.
_Avoid_: cross-Workspace/Lab live locks, two advancing Hosts, scope inferred from a database filename

**活动 Host（Active Host）**：
中文定义：一个调度作用域（Scheduler Scope）内唯一持有实时调度循环、Slave 连接和设备执行接缝的
OS Host；其他 OS 实例只能等待、接管迁移或承担不冲突角色。
中文避免：多主调度、Go 逐节点派发、浏览器推进任务、断线自动选主。
English:
The sole OS Host in one Scheduler Scope that owns the live scheduling loop, Slave connections, and device-execution seam.
_Avoid_: multi-leader scheduling, Go per-node dispatch, browser task advancement, automatic election on disconnection

**权威（Authority）**：
中文定义：由显式运行模式选定、唯一允许创建或推进某类聚合事实的一方；业务持久化权威与实时
调度权威可以属于不同模块，但同一事实类别始终只有一个写入者，副本和投影不会成为第二权威。
中文避免：last-writer-wins、双真相、就近写库、把业务权威等同实时 Scheduler、断网自动接管。
English:
The single writer selected by an explicit runtime profile for one class of aggregate facts. Business persistence and live scheduling may belong to different modules, but replicas and projections never become a second Authority for the same fact class.
_Avoid_: last-writer-wins, dual truth, nearest-database writes, equating business Authority with the live Scheduler, automatic takeover on disconnection

**Material UUID**：
Material 唯一稳定身份，关系和工作流字段统一写作 `material_uuid`。避免 Edge UUID、
cloud UUID、instance UUID。

**Material 组成（Material Composition）**：
由 `material.parent_uuid` 表达的结构父子关系。避免与 Site 占用、实验室布局、调度锁混用。

**库位（Site）**：
由一个 Material 拥有的稳定具名位置；`site.uuid` 标识位置自身，`site.material_uuid` 指向
owner，`site.occupied_material_uuid` 指向 occupant。避免 Material UUID、slot name、PLR index。

**位置（Location）**：
中文定义：比库位（Site）更宽泛的空间或物流位置概念，可以表示区域、工位或运输节点；它不能替代
carrier 内具有稳定 UUID 与占用语义的库位（Site）。
中文避免：把 `location` 改名为 `site`、用自由文本位置充当 Site UUID、把 Site 占用写进空间层级。
English:
A broader spatial or logistics location such as an area, workstation, or transport node; it never replaces a stable occupiable Site inside a carrier.
_Avoid_: renaming Location to Site, free-text location as Site UUID, Site occupancy in a spatial hierarchy

**库位身份权威（Site Identity Authority）**：
中文定义：在一个明确运行模式中唯一允许为实例库位（Site）分配并持久化 `site.uuid` 的权威；
具体由 Host OS 或 Go 业务后端承担，权威接管必须显式迁移既有身份。
中文避免：Backend 永远生成、两端并行生成、label/index 充当 UUID、按名称猜测合并。
English:
The sole Host-OS or Go-business Authority allowed to allocate and persist `site.uuid` for an instance Site in one explicit runtime profile. Authority transfer migrates existing identities explicitly rather than regenerating them.
_Avoid_: Backend-always-generates, dual generation, label/index as UUID, name-based merge

**Site 占用（Site Occupancy）**：
Site 到当前摆放 Material 的可选关系，以 `occupied_material_uuid` 表达。避免与组成关系、
Site identity、执行锁混用。

**软删除（Soft Deletion）**：
`deleted_at` 非空后普通读取排除，但保留 UUID 和历史的生命周期状态。避免物理删行、空 status、
隐藏 alias。

**工作流（Workflow）**：
可复用的持久化图定义。避免一次执行、运行快照、Run。

**工作流任务（Workflow Task）**：
从冻结工作流图创建的一次执行。避免 Workflow definition、Run alias。

**工作流成功终态（Workflow Success Terminal State）**：
中文定义：Backend-shaped 共享 Interface 的规范成功值是 `succeeded`；Edge Local REST v1 的
`success` 只是遗留 Adapter 输出，进入共享模型时必须规范化回 `succeeded`。
中文避免：把 `success` 写入 Backend canonical 表、让调用方猜运行环境、两套终态语义。
English:
The canonical success value of the Backend-shaped shared Interface is `succeeded`; `success` is a legacy Edge Local REST v1 adapter value only.
_Avoid_: `success` in Backend canonical storage, environment-dependent callers, two terminal semantics

**Edge 私有库存接口（Edge-only Inventory Interface）**：
Edge 内部 lot、reservation、inventory ledger 和诊断协议，不属于共享接口。避免把它作为前端
fallback 或伪装成 Backend route。

**运行时存储所有权（Runtime Storage Ownership）**：
中文定义：每个数据库文件在同一时刻只有一个进程和一个迁移所有者可以写入；跨模块交换版本制品、
命令、事件和回执，而不共享可写数据库连接或复制数据库行。
中文避免：Go 与 OS 同开 SQLite、浏览器直读数据库、跨进程共享 WAL、双迁移所有者。
English:
The rule that each database file has one writer process and one migration owner at a time; modules exchange artifacts, commands, events, and receipts instead of writable connections or copied rows.
_Avoid_: Go and OS sharing SQLite, browser database reads, cross-process WAL sharing, dual migration owners

**库存同步发件箱（Inventory Sync Outbox）**：
中文定义：随库存聚合事实同事务写入、以聚合版本和连续游标向另一权威同步的持久事件序列。
中文避免：调度运行事件、前端通知队列、按表行镜像。
English:
A durable event sequence committed with Inventory aggregate facts and synchronized using aggregate versions and a contiguous cursor.
_Avoid_: Scheduler runtime events, frontend notification queue, row mirroring

**运行时事件发件箱（Runtime Event Outbox）**：
中文定义：随 DAG 游标或任务终态同事务写入、用于发布调试和执行事件的持久发件箱；其事件身份与
任务内序号不等同于库存聚合版本。
中文避免：库存同步发件箱、只存在内存的通知、终态提交后再补写事件。
English:
A durable outbox committed atomically with a DAG cursor or Task terminal event for debug and execution-event publication; its event identity and Task sequence are not Inventory aggregate versions.
_Avoid_: Inventory Sync Outbox, memory-only notification, event insertion after terminal commit

**版本制品（Version Artifact）**：
中文定义：从一个写入权威发布给另一个作用域的不可变、带 Schema 版本和内容哈希的 Workflow/Resource
版本载荷；接收方导入逻辑对象而不复制来源数据库行。
中文避免：复制 SQLite 文件、跨库双写、以最新草稿覆盖已发布版本、同 UUID 异内容静默覆盖。
English:
An immutable Workflow/Resource version payload with a Schema version and content hash, published across Authority scopes without copying source database rows.
_Avoid_: copying SQLite files, cross-database dual writes, latest draft overwriting a published version, silent same-UUID content conflict

**增量同步（Incremental Sync）**：
以 UUID 幂等、sequence 有序、aggregate_version 防乱序、cursor 连续 ACK 的 durable
同步。避免用轮询全量覆盖或 last-writer-wins 代替。
