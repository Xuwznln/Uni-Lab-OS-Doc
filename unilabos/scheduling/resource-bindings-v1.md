# UniLabOS 资源绑定语义 v1

本登记补充 r3 中 `Option.binding_facts` 的平台解释，能力名为
`unilab.resource_bindings.v1`。冻结 r3 的 JSON Schema、C ABI 和八份协议附件不变。
UniLabOS 的生产组合根在 describe/create 中要求该能力；仅声明 `core.v1` 的旧引擎
返回 `UNSUPPORTED_CAPABILITY` 并停止装配，不自动降级或把共享资源改成另一种语义。

| 字段 | 约束 | 职责 |
| --- | --- | --- |
| `Option.resource_ids` | 全部规范资源身份，无重复 | 完整准入绑定，含持续所有权和当前动作使用；平台 A 事务逐项复验 |
| `binding_facts.active_resource_ids` | 可省略；出现时为无重复字符串数组，必须是 `resource_ids` 的子集 | 当前动作排他使用的资源；省略时保守地使用完整 `resource_ids` |
| 纯所有权集合 | `resource_ids - active_resource_ids`；每个键必须属于包含该节点的显式 `Interval` | 本动作需要保持的持续预留，不代表此刻正在排他使用 |
| `binding_facts.continuation_resource_ids` | 平台生成的当前已有所有权提示 | 不提供独立授权，不覆盖 `eligible_now`、实际占用或执行事务门禁 |
| `Interval.members` / `held_allocation_ids` | 遵守 r3 的同 Task 引用规则 | 标明哪些动作属于同一持续所有权，以及哪些 Allocation 是其实际持有 |
| `InFlight.resource_ids` | 当前真实活动使用集合 | 不把共同祖先预留复制成各分支的排他活动使用；预留由关联 Allocation/Interval 保留 |

同一规范资源键在两个候选之间重叠时，仅当两节点属于包含该键的**同一个显式区间**，
且两者并非都活动使用该键，才允许共同持有。两个动作活动使用同一键仍互斥。
跨 Task、无共同区间、或不同但存在层级冲突的资源键不能据此共享。
平台对已持有区间内的合法续行仍复验实时资源、Claim/Fence 和冻结绑定；
某 Task 的续行豁免不能抹掉其他 Task 或外部权威的同键占用。

库位组必须枚举其合法候选，单个模板不兼容不能隐藏其他合法库位。物理占用、
入口预留等当前条件通过 `eligible_now` 与 `blockers` 表达，不改变相同完整绑定的
`option_id`。它们不自动产生可预测的释放边界：引擎不能仅凭时长估计认为库位
已经腾空。执行占用若有在途动作或区间证据，可继续按这些事实预测后续可用时间。
这是既有可用性及释放边界字段的解释，不增加 r3 字段或能力版本。

例如同 Task 的两个动作共同持有仓库、反应器 A 和反应器 B 的一个外层区间：

```json
[
  {
    "option_id": "branch-A",
    "resource_ids": ["warehouse", "reactor-A", "reactor-B"],
    "binding_facts": {"active_resource_ids": ["reactor-A"]}
  },
  {
    "option_id": "branch-B",
    "resource_ids": ["warehouse", "reactor-A", "reactor-B"],
    "binding_facts": {"active_resource_ids": ["reactor-B"]}
  }
]
```

这是字段片段，完整 Option 和 Interval 仍按 r3 填写。两动作可以并行；
外部 Task 仍需等待持续区间真正释放。若两动作都把 `reactor-A` 列为活动使用，
就只能先执行一个。CP-SAT 对活动使用建动作互斥，对持续所有权建区间持有约束，
不能用共享能力忽略持续占用。

兼容规则：基础协议调用方可以省略该元数据，以完整绑定保守互斥。生产 UniLabOS
总是输出已计算的活动集合，并要求引擎声明支持本登记。不支持本能力的历史动态库
不能直接用于新的生产组合根，需要升级构建；已有 v2 模型状态无需因此清空。
后续若改变字段语义，必须另登记能力版本，不能复用 v1 名称改变解释。

验收包含：同区间不同设备并行、同资源活动使用互斥、无区间的共享声明拒绝、
活动集合越界拒绝、缺省元数据保守互斥、旧插件装配失败，以及真实动态库和平台
共同持有直到两个分支都结算的集成场景。
