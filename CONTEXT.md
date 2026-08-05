# Edge 资源与工作流领域上下文

本文定义前端、Backend 与 Edge 微后端共用的领域语言。Backend 是公共契约标准；Edge
按部署模式充当本地 Authority 或 Backend-controlled 执行适配器。数据库物理类型可以不同，
领域身份、字段语义、状态词汇、envelope 和幂等规则必须一致。

## 领域语言

**共享接口（Shared Interface）**：
Backend 与 Edge 对前端呈现相同的资源和工作流协议，包括路由语义、字段、枚举、envelope
和错误。避免使用“相似接口”、Edge-shaped response、前端 fallback。

**权威（Authority）**：
一次操作中唯一允许接受聚合写入的一方；副本和投影不会成为第二权威。避免 last-writer-wins、
双真相、就近写库。

**Material UUID**：
Material 唯一稳定身份，关系和工作流字段统一写作 `material_uuid`。避免 Edge UUID、
cloud UUID、instance UUID。

**Material 组成（Material Composition）**：
由 `material.parent_uuid` 表达的结构父子关系。避免与 Site 占用、实验室布局、调度锁混用。

**库位（Site）**：
由一个 Material 拥有的稳定具名位置；`site.uuid` 标识位置自身，`site.material_uuid` 指向
owner，`site.occupied_material_uuid` 指向 occupant。避免 Material UUID、slot name、PLR index。

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

**Edge 私有库存接口（Edge-only Inventory Interface）**：
Edge 内部 lot、reservation、inventory ledger 和诊断协议，不属于共享接口。避免把它作为前端
fallback 或伪装成 Backend route。

**增量同步（Incremental Sync）**：
以 UUID 幂等、sequence 有序、aggregate_version 防乱序、cursor 连续 ACK 的 durable
同步。避免用轮询全量覆盖或 last-writer-wins 代替。
