# 微后端数据库边界

微后端使用四个独立 SQLite 文件，每库一个 writer。分库是为了隔离关键控制、
物料事务、高频设备状态和大历史写入；不是为了把每个数据模型字段再拆成表。
运行过程产生的事实同域同库：workflow authority 与 registry 快照落
`runtime.db`（复用 RuntimeService 的连接与写锁），拓扑边与 lab graph
快照落 `materials.db`。业务代码不得使用 `ATTACH DATABASE` 或跨库外键。

代码命名空间统一归属微后端：工作流领域服务（编排、authoring、组合展开、
上传）位于 `unilabos.server.services.runtime.workflow`，线上 DTO 契约位于
`unilabos.protocol.runtime.workflow`（图结构校验在 `unilabos.protocol.utils.workflow_validation`），
调度与执行期 DAG 位于 `unilabos.server.backend.scheduler`，
四库组合根位于 `unilabos.server.composition`，运行时装配位于
`unilabos.server.backend.composition`。
启动过程只解析下表所列四个数据库路径，不自动发现或导入其他数据库文件。

| 数据库 | 权威内容 | 表数（含 identity） |
| --- | --- | ---: |
| `runtime.db` | 后端命令、执行 job、endpoint 与可靠收发；工作流定义/任务/job 与前端事件；edge 注册表版本快照 | 27 |
| `materials.db` | 资源模板、物料、Site、拓扑边、lab graph 快照、预留与库存账本 | 13 |
| `telemetry.db` | 设备最新状态和高频追加事件 | 4 |
| `history.db` | 大 payload 和统一执行历史流 | 3 |

四库合计 47 张表，其中 4 张是各库自己的 `schema_identity`。

## 目录即库

持久化到调用端的整条链（`tables` / `services` / `api` / `client`）目录都
严格按四库划分，导入只看表：需要拆分的库做成子包、经 `__init__` 聚合，
`workflow` 与 `registry` 归 runtime 库域，`graph` 与快照对比归 materials
库域。

```
server/database/
├── tables/                  # 行模型 + 建表 DDL（唯一来源）
│   ├── base.py              # 公共类型与 TableObject
│   ├── runtime/             # runtime.db（子包聚合）
│   │   ├── __init__.py      #   RUNTIME_DATABASE / RUNTIME_TABLE_MODELS + 全部表重导出
│   │   ├── data.py          #   运行控制表（backend_session/command_inbox/execution_job 等）
│   │   ├── workflow.py      #   Workflow Authority 16 张表
│   │   └── registry.py      #   注册表快照三表
│   ├── materials.py         # materials.db
│   ├── telemetry.py         # telemetry.db
│   └── history.py           # history.db
├── schema.py                # DatabaseSpec / initialize_database / 身份校验
└── sqlite_domain.py         # SqliteDomain：单连接、单写者、同库共存域基座

server/services/             # 领域服务（单层持库：service 即 writer）
├── runtime/                 # RuntimeService（继承 SqliteDomain）/ workflow 子包 / RegistryService
│   ├── data.py              #   RuntimeService：运行控制 SQL + 状态机
│   ├── workflow/            #   WorkflowService（继承 store.WorkflowStore）
│   │   ├── store.py         #     存储基座（行 CRUD、事务、稳定错误码）
│   │   ├── errors.py
│   │   └── service.py       #     编排/authoring/上传等业务
│   └── registry.py          #   RegistryService（借用 runtime 连接）
├── materials/               # materials.db 域
│   ├── store.py             #   MaterialsRepository：行 CRUD 基座（Service 继承它）
│   ├── core.py              #   MaterialsService(MaterialsRepository)
│   ├── graph.py             #   GraphService(MaterialsRepository)，借用 materials 连接
│   └── snapshot.py
├── telemetry.py             # TelemetryService（继承 SqliteDomain）
└── history.py               # HistoryService（继承 SqliteDomain）

server/api/                  # HTTP 路由（app.py 为装配面，非库域）
├── runtime/                 # data.py（runtime.v1 数据面）/ control.py（业务控制面）
│                            #   / diagnostics.py / workflow.py / registry.py
├── materials/               # core.py / graph.py
├── telemetry.py
└── history.py

client/                      # 出站客户端（http/session/envelope/output 为基础设施）
├── runtime/                 # data.py / workflow.py
├── materials/               # core.py / graph.py
├── telemetry.py
└── history.py
```

## 持久化代码收敛

- `database/tables/` 是持久化行模型、建表 DDL 和 SQLAlchemy metadata 的唯一来源；
  模型使用 SQLModel，同时承担 Pydantic 构造校验和 SQLite 字段映射，DDL 以
  `TableSpec`/`DatabaseSpec` 与行模型放在同一文件。
- 数据库采用 checksum 驱动的重建策略，不维护 migration 链：
  `schema_identity` 记录库身份与 DDL checksum；打开时 checksum 与代码声明
  不一致则删除文件并重建，身份属于其他库则报
  `DatabaseIdentityConflict` 拒绝打开。
- 各域 Service 直接继承存储基座持库
  （`SqliteDomain` 或域内 store 类），SQL、事务与业务规则同类分层——行级
  读写方法与业务覆写同名时经 `基座类.方法(self, ...)` 显式限定调用。
  建表 DDL 与行模型只在 `tables/` 声明。
- `protocol/` 只保留线上请求、响应和跨表聚合 DTO。协议形状与单表行完全相同时直接
  复用表模型。
- CI 以真实 DDL 创建四个 SQLite 文件，并逐表核对 SQLModel metadata 的表名、
  字段顺序和复合主键，防止表模型与落库结构漂移。
- 同库多组件共享连接：`WorkflowService` 与 `RegistryService` 在生产组合根
  借用 `RuntimeService` 的 connection 与 `write_lock`，`GraphService` 借用
  `MaterialsService`；每个物理文件始终只有一个连接和一把进程内写锁。

## 表目录

### `runtime.db`

| 表 | 职责 |
| --- | --- |
| `schema_identity` | 数据库身份和真实 schema checksum |
| `backend_session` | 后端连接 epoch 与命令/事件游标 |
| `executor_endpoint` | HostLink/ROS2 endpoint；route、action capability 和 availability 是 JSON 模型字段 |
| `command_inbox` | 后端命令幂等接收箱 |
| `execution_job` | 后端 job；物料 binding、错误 gate 和 terminal decision 是 job 字段 |
| `adapter_command_outbox` | 发往 HostLink/ROS2 的可靠命令 |
| `adapter_event_inbox` | adapter 控制事件、ACK 和 endpoint snapshot |
| `backend_event_outbox` | 发往后端的可靠领域事件 |
| `registry_entry` | 注册表条目级不可变版本行（任何字段变化自增版本，全量 payload copy） |
| `registry_entry_state` | 条目可变状态：active/pending 版本、pending 冲突、软移除与不可用标记 |
| `registry_report` | 每次 edge 上报的批次统计（新增/更新/挂起/移除/不可用） |

workflow authority 的 16 张表同样落在 `runtime.db`（见下节）。

### `materials.db`

| 表 | 职责 |
| --- | --- |
| `schema_identity` | 数据库身份和 schema checksum |
| `resource_template` | 完整模板；`category`、`available_sites`、`handles` 都是模型字段 |
| `inventory_lot` | 独立批次和数量聚合 |
| `material` | Material 身份、树关系及低频静态配置 |
| `material_position` | Material 的 1:1 `ResourceDictPosition` 几何和布局 |
| `material_data` | Material 的 1:1 杂项动态 `data`、内容版本和状态来源 |
| `material_substance` | `material_data` 的 1:N 当前内容物；每行是 `name/quantity/quantity_unit` 三元组 |
| `site` | 完整 ResourceSite 当前快照，包含 category 提示和 occupant |
| `material_link` | 物料/设备间拓扑边（node-link 的 link），身份为两端+handle+类型的稳定 uuid |
| `lab_graph` | 命名、版本化的设备图快照（node-link JSON 全量 payload） |
| `inventory_reservation` | 每个 backend job 一行，items 是 JSON 数组字段 |
| `inventory_command_effect` | materials command 的跨重启幂等状态 |
| `inventory_ledger` | append-only 事实账本，同时承担向后端投递状态 |

### `telemetry.db`

| 表 | 职责 |
| --- | --- |
| `schema_identity` | 数据库身份和 schema checksum |
| `telemetry_source_cursor` | endpoint epoch/generation/sequence 水位 |
| `device_state_latest` | 每个 endpoint/device 一行完整最新状态、属性、连接和告警 |
| `telemetry_event` | state/property/connection/alarm 的统一高频追加流 |

### `history.db`

| 表 | 职责 |
| --- | --- |
| `schema_identity` | 数据库身份和 schema checksum |
| `payload_object` | 最大 256 KiB inline payload；更大内容使用外部对象存储 |
| `history_event` | transition/feedback/result/log/error/decision 的统一追加历史流 |

### workflow authority 表（落 `runtime.db`）

| 表 | 职责 |
| --- | --- |
| `workflow` | 工作流聚合根：名称、标签与 revision |
| `workflow_node_template` | 节点模板：goal/feedback/result schema 与展示属性 |
| `workflow_handle_template` | 节点模板的输入输出 handle 定义 |
| `workflow_node` | 画布节点：模板绑定、pose、param 与执行策略 |
| `workflow_edge` | 节点连线：source/target 节点与 handle |
| `workflow_task` | 任务权威：snapshot、execution_plan、控制/清理状态机；ad-hoc 设备动作复用同一状态机 |
| `workflow_node_job` | 节点级 job：executor kind、attempt、feedback/return 与取消 deadline |
| `workflow_task_command` | step/pause/resume/cancel 命令的幂等接收箱 |
| `execution_lock_lease` | 物料 UUID 锁的租约与不确定态 |
| `workflow_node_job_result` | edge 提交结果的幂等落地与消费标记 |
| `workflow_node_job_feedback_history` | job feedback 的有序追加历史 |
| `workflow_intervention` | 人工干预：选项、决策与恢复控制状态 |
| `workflow_manual_confirmation` | manual_confirm 节点的确认单 |
| `workflow_source_registration` | published workflow 与包内源文件的注册关系 |
| `workflow_authoring` | 草稿观测、候选与 writeback 状态 |
| `frontend_event` | 面向前端的追加事件流 |

`workflow_runs` 与 `job_runs` 是随建库一起创建的只读审计投影视图；不属于
表模型，也不参与 SQLModel 核对。

## 聚合与数据模型原则

- 表对应需要独立身份、生命周期、事务或高频写入隔离的聚合，不对应每个 Pydantic
  类型或对象字段。
- `ResourceTemplate.category` 是 `list[str]` 数据模型字段，SQLite 使用
  `category_json` 保存；前端用它识别，后端和 Edge 不做 Site 准入校验。
- `available_sites` 和 `handles` 同样属于 ResourceTemplate，不建立模板子表。
- Material 是对象聚合原则的例外：位置结构稳定且有独立更新节奏，杂项 `data` 内容异构，
  因此分别保存为 1:1 `material_position` 和 `material_data`。
- `material.ordinal` 保存同一父节点下的 PLR child 顺序，`site.ordinal` 保存载架声明的
  Site 顺序；`site_index` 是业务索引，不能用标签排序替代序列化顺序。
- PLR child 的 `resource_id` 使用根资源内的转义路径形成全局稳定键；展示名仍保存在
  `name`，实例身份由微后端分配的 `material_uuid` 决定。
- `ResourceDict.substances` 是规范内容物字段，保存在 `material_data` 下的 1:N
  `material_substance`；每项为 `(name, quantity, quantity_unit)` 三元组。
  单位不在数据库枚举，Edge 写入侧主要使用 `ul`（液体）和 `ug`（固体）。
- 内容物变化历史进入 append-only `inventory_ledger`。
- route/capability/availability 跟 endpoint snapshot 同步重建，直接保存在
  `executor_endpoint`。
- material bindings、错误 gate 和 terminal decision 跟一次 job 同生命周期，直接保存在
  `execution_job`；审计历史另写 `history_event`。
- reservation items 随 backend job 整体申请和释放，保存在一行
  `inventory_reservation.items_json`。
- Scheduler 在 Task 准入时用一个事务 all-or-nothing 预留全部 Job 需求：实体物料
  `active -> reserved`，数量库存 `available -> reserved`。动作取得物料 UUID 锁后、
  调用驱动前才 consume：实体物料 `reserved -> in_use`，lot 同时扣减 total/reserved。
  Task 终态释放尚未开始的 active reservation；已经 consume 的失败/取消不返还数量，
  实体物料进入 `quarantined`。这组事实统一进入 `inventory_ledger`。
- `inventory_lot` 是 Scheduler 可用量权威；`material_substance` 是容器当前内容物快照，
  由 PLR 原子 observer 更新。Scheduler consume 不同时改 substance，避免双重扣减。
- latest 与 append-only history 读写模式不同，因此设备状态使用
  `device_state_latest` + `telemetry_event` 两张表。

## 跨库关联

跨库只保存规范 UUID 和内容哈希，不声明 SQLite 外键：

| 标识 | 权威库 | 其他引用位置 |
| --- | --- | --- |
| `command_uuid` | `runtime.command_inbox` | materials effect/ledger；history event |
| `job_uuid` | `runtime.execution_job` | materials reservation/ledger、telemetry event、history event |
| `endpoint_uuid` | `runtime.executor_endpoint` | telemetry latest/event、history event |
| `material_uuid`、`site_uuid`、`reservation_uuid` | `materials.db` | runtime job binding JSON |
| `payload_uuid` | `history.payload_object` | runtime command/event/job 与 history event |

## 调度和错误边界

- 后端调度器是唯一调度权威；执行侧不保存本地 DAG、待调度队列或本地 retry。
  调度权威的 DAG 与队列事实只落在 `runtime.db` 的 workflow 表
  （execution_plan 与 task/job 状态机）。
- retry 是新的后端命令和新的 `job_uuid`，通过 `retry_of_job_uuid` 关联原 job。
- action availability 是 endpoint 快照字段，不是 edge 调度锁。
- 非人工错误把 job 置为 `terminal_waiting` 并打开 gate；收到后端确认调度已更新的
  `release_failed` 后，才允许将同一 job 更新为 `failed`。
- 人工干预使用 replacement result；原结果和替换结果都追加到 `history_event`，通过
  `supersedes_event_uuid` 关联，不覆盖原始历史。
- Site category 仅供前端画布识别，不参与 materials writer 的占用准入。

## 四库业务接口

四个数据库都通过各自 Service 写入；materials 与 workflow 域分别使用
`MaterialsRepository`、`WorkflowStore` 作为行级存储基座。对外提供同构的
FastAPI、Local client 与 HTTP client。公共安装入口是
`unilabos.server.api.install_server_apis`，一次挂载以下命名空间：

| 数据库 | HTTP 前缀 | 写入语义 |
| --- | --- | --- |
| `runtime.db` | `/api/v1/runtime` | session/endpoint upsert、命令和 job 状态机、gate 与可靠 outbox |
| `runtime.db`（workflow 表） | `/api/v1/workflows`、`/api/v1/workflow-tasks`、`/api/v1/workflow-node-jobs`、`/api/v1/events` | 工作流图 CRUD 与全图 reconcile、任务提交/控制命令、job result/feedback 幂等落地 |
| `runtime.db`（registry 三表） | `/api/v1/resource-templates`、`/api/v1/registry/*` | edge 注册表条目级上报替换、pending 确认/驳回、历史还原与批次统计 |
| `materials.db` | `/api/v1/materials`、`/api/v1/graphs` | 模板/物料聚合 CRUD、transfer/snapshot、拓扑边、lab graph 快照、lot 入库、Task/Job reservation 转换与 ledger ACK |
| `telemetry.db` | `/api/v1/telemetry` | event ingest 推进 cursor/latest，另提供只读查询 |
| `history.db` | `/api/v1/history` | payload 保存、event 追加和人工 replacement chain |

`telemetry_event`、`history_event`、runtime outbox 与 workflow 的
result/feedback/frontend_event 是追加式数据，不提供任意 PUT/PATCH/DELETE。
Runtime job 更新必须经过合法 transition/error gate；这些约束在 Local 和 HTTP
两种调用方式下保持一致。

## workflow authority 实现入口

workflow 表落 `runtime.db`，writer 生命周期与调度权威绑定：未配置云端地址时由
`unilabos.server.backend.composition.setup_local_scheduler` 在本进程装配
（`WorkflowService` 复用 `RuntimeService` 的连接与写锁）；配置云端后调度权威
在远端 Backend，本机不装配 `WorkflowService`。

| 层 | 入口 | 职责 |
| --- | --- | --- |
| 通信协议 | `unilabos.protocol.runtime.workflow` | 节点/边写入 DTO、JSON 值约束与 UUID 规范化 |
| 图结构校验 | `unilabos.protocol.utils.workflow_validation` | 全图 reconcile 前的结构与模板一致性校验 |
| 持久化 | `unilabos.server.services.runtime.workflow.store` | `WorkflowStore` 单 writer：行 CRUD、事务与稳定错误码（Service 继承它） |
| 领域服务 | `unilabos.server.services.runtime.workflow` | `WorkflowService` 编排、authoring/组合展开/发布运行时、上传管道 |
| 调度 | `unilabos.server.backend.scheduler` | `BackendScheduler` 消费 execution_plan 并驱动 job 状态机 |
| HTTP / Client | `unilabos.server.api.runtime.workflow`、`unilabos.client.runtime.workflow` | workflow 命名空间路由与同构 client |

## materials authority 实现入口

`materials.db` 通过下列分层接入运行时，调用方不直接拼接 SQL：

| 层 | 入口 | 职责 |
| --- | --- | --- |
| 通信协议 | `unilabos.protocol.materials` | `materials.v1` DTO、写命令信封、版本前置条件和结果 |
| 持久化 | `unilabos.server.services.materials.store` | 表行 CRUD、`BEGIN IMMEDIATE` 单 writer、ledger/outbox（Service 继承它） |
| 聚合服务 | `unilabos.server.services.materials` | 模板、Material Tree、Position/Data/Substance、Site move 和软删除 |
| 快照 | `unilabos.server.services.materials.snapshot` | 规范哈希、逐 section diff 和一次事务应用 |
| PLR 边界 | `unilabos.resources.adapters.plr_materials` | PLR 创建草稿、权威 UUID 回填、上传和下载 |
| Registry 边界 | `unilabos.resources.adapters.registry_materials` | Registry/lab_resources 定义登记和模板 UUID 映射 |
| Helper | `unilabos.resources.materials` | `materials.create(plr_resource)`，按 Host/Slave 角色选择权威链路 |
| 设备运行时 | `unilabos.backend.runtime.resource` | `ResourceService` 把 create/get/update 统一路由到微后端；update 使用局部 snapshot 和版本前置条件 |
| HTTP / Client | `unilabos.server.api.materials`、`unilabos.client.materials` | `/api/v1/materials` 与同构 Local/HTTP/HostLink client |

所有写请求使用 `(command_uuid, effect_key)` 幂等。成功结果保存 ledger sequence
范围；拒绝结果保存稳定错误码。Material 的 identity、position、data/substances
任一 section 变化时，Material 聚合版本只增加一次；Site 使用自己的版本。Snapshot
不隐式创建或删除聚合，结构变化必须使用显式 create/delete。

`ResourceTreeSet.from_plr_resources(..., known_random_uuid=True)` 只允许创建草稿
生成临时 Resource/Site UUID。微后端 create 总是重新分配权威 UUID，并在
`client_ref_map` 返回映射；下载得到的权威树继续使用默认严格模式。

创建请求不接受 `template_uuid`。Helper 从完整 PLR Resource 中提取稳定的
`template_name` 及 identity/position/data/substances/sites；materials authority 按
`template_name` 对齐 complete registry。名称不存在时，authority 在同一事务内登记
自定义模板并分配内部 `template_uuid`。该 UUID 仅用于数据库外键、版本和回执，调用方
不负责提供。Slave 的创建、查询和 snapshot 更新固定经 HostLink 发给 Host，再由 Host
代发到当前微后端 Materials Authority。Host 的运行时 ResourceTreeSet 只是工作副本，
不能作为查询 fallback，也不能分配或接受实例 UUID。

Edge 侧的物料信息入口是微后端。设备、Host、Slave 和 Edge API 均通过
`ResourceService` 或 materials client 访问该入口；本地服务负责分配 UUID、
维护版本并执行 snapshot 比对。Host 的运行时资源树只是工作副本，不能作为查询
回退或绕过权威服务写入。
