# UniLabOS scheduler v1.0 — 未发布草案修订 3

冻结日期：2026-10-20。本文为飞书设计文档第 13 章的附件快照；文档与附件须一致发布。

# 13. 调度引擎接口规范 v1.0（开发基线）

本节为未发布的调度引擎 v1.0 草案修订 3（2026-10-20）。本轮补齐单步门禁、执行进程重启占用、反馈追赶、嵌套区域集合与装配校验五项规则；目标行为与当前代码差异明确标注。正式冻结前须通过 A01～A27 及协议/原生验收；产品默认策略、Rust 求解器绑定和进程部署仍为待决事项。

正文与 scheduler-engine-v1-contract-r3.zip 共同定义未发布草案修订 3。核心 Schema 保留 64 个类型定义与六个 C ABI 声明；附件含 68 个消息示例、17 组请求/响应配对、9 个装配元数据样例和 14 组运行时验收场景，另含装配清单 Schema、示例、冻结语义文本与验证报告。正文和附件冲突须修正规范，不能自行择宽解释。

## 13.1 接口概述与审核总表

本章定义 UniLabOS 与可替换调度引擎之间的统一调用协议。基础队列、商业 CP-SAT 和第三方引擎实现相同的四个入口：describe 声明能力，create 建立实例，decide 计算决策，close 释放实例。UniLabOS 负责组织输入、检查并落实决策、持久化结果和恢复；引擎负责 Task 选择、资源方案选择及排程算法。

四个核心业务契约集中映射到 decide：任务视图、资源视图和执行反馈作为输入，调度决策作为输出；checkpoint 用于恢复已经接受的算法状态。完整字段见对应小节及 JSON Schema。采用本章 C ABI 档案时，再提供 abi_version 与 free_buffer 两个技术辅助入口，共六个原生导出符号。

| 接口与适用范围 | 主要输入 | 主要输出与职责 | 详细规范 |
| --- | --- | --- | --- |
| describe｜所有引擎必需 | 宿主版本、支持的 schema 版本、必需能力。 | 返回引擎身份、支持能力、配置 schema、状态格式和容量上限，供装配前检查。 | 能力发现与配置 |
| create｜所有引擎必需 | 实例/调度域/运行世代身份，不可变配置、容量配额及 Restore。 | 返回实例确认、实际能力与配额、恢复模式、Checkpoint 及警告。 | 实例与恢复输入 |
| decide｜所有引擎必需 | 完整 Snapshot（含控制区域、容量池、在途开始状态和 head）、连续反馈、Checkpoint；计算预算及应用预算。 | 返回 Task 范围、start/admit_manual/continue_attempt 意图、反馈消费位置、next_state、原因及唤醒建议；预测可选。 | 输入字段；输出与应用；恢复和映射规则 |
| close｜所有引擎必需 | 实例、调度域、运行世代身份及关闭原因。 | 返回 closed，释放算法计算资源；实际任务、设备动作与结算仍由平台管理。 | 关闭与幂等 |
| abi_version｜采用 C ABI 时必需 | 无参数。 | 返回 ABI major/minor 标识，供调用原生入口前检查二进制兼容性。 | C ABI 档案 |
| free_buffer｜采用本章 C ABI 时必需 | 本库返回且尚未释放的响应指针，以及对应字节长度。 | 无业务返回值；由分配响应的同一动态库释放缓冲区。 | 内存所有权规则 |

调用顺序：describe → create → 多次 decide → close。所有调用由 UniLabOS 宿主或其适配器发起，由所选引擎实现。决策被接受与设备实际执行分别记录；wait 保留选择但不授权普通推进，sync 只同步反馈；引擎故障停止新的自动调度，平台继续处理在途结果。

四个业务契约、四个调用入口和六个 C ABI 符号保持不变。下表汇总原八项及本轮五项补充的规范位置与验收；规则已定义不表示运行时已验收。概述与审核表位于接口章节，产品默认策略和原生部署方式仍按待决事项管理。

| 审核项 | 本轮规范落点 | 验收 |
| --- | --- | --- |
| 人工两阶段与排空 | DispatchIntent.kind、InFlight.manual_phase/start_state；平台生命周期规则 | A15、A16 |
| 调度/执行分层恢复 | D/A/S 边界；head.run_epoch/authorization_valid；apply_budget_ms | A17 |
| 循环和控制结构 | ControlRegion 直属成员、结构入口/出口、输入依赖、阶段及嵌套归属；区域集合不由引擎猜测 | A18、A26 |
| 估时规则与样本兼容 | TimingProfile、Observation.timing_spec_id/version/provenance | A19 |
| 反馈不自行循环 | sync 的接受审计不入可消费反馈流；全部成功响应确认 through_seq；有限追赶与空闲停止 | A20、A25 |
| 历史引用与当前闭包 | 历史 Observation/反馈可引用归档对象；当前执行引用必须解析 | A19 |
| 运行配额 | CapacityView、Task/Node.capacity_pool_ids；与消息 limits 分开 | A21 |
| 旧状态与故障域 | 普通自动与受限单步分别判定；执行进程更换后未决占用保持；业务终止不等于结算完成 | A22、A23、A24 |
| 草案装配一致性 | 宿主批准的 contract manifest 摘要 + 插件 manifest + 实际载入物摘要；载入前拒绝混装 | A27 |

## 13.2 编码、类型与公共消息信封

业务协议名固定为 unilab.scheduler，首期 schema_version 固定为字符串 1.0。传输使用 UTF-8 JSON；Python 实现可以直接使用等价的已校验数据对象，动态库适配器使用 JSON 字节。禁止 NaN、Infinity、重复对象键及无法表示的整数。对象字段除声明为可选的 extensions 外，均按附件 required 列表处理；允许 null 和允许省略是不同语义。

编码与配置校验的外部依据：RFC 8259（JSON）、JSON Schema 2020-12。以下具体字段、限制、错误处理与恢复步骤为 UniLabOS 自身的 v1 设计约定。

| 类型 / 约定 | v1 规则 |
| --- | --- |
| Id / Uuid | Id 是 1～128 字符的非空稳定身份。Uuid 为小写标准连字符 UUID 字符串；用于请求、实例、运行世代、配置及执行 attempt。NodeRef 固定为 {task_id, node_id}。 |
| Counter | 无前导零的十进制字符串，0～18446744073709551615；按数值比较。用于修订号、队列序号和反馈游标。JSON Schema 校验格式，uint64 上界由语义校验补充。 |
| UInt / Number | UInt 为 0～9007199254740991 的 JSON 整数；Number 为有限 JSON 数字。所有时间戳使用 UTC Unix 毫秒，耗时/预算/偏移使用毫秒。 |
| JsonObject / Extensions | JsonObject 用于动作参数、明确开放的业务数据和引擎配置。其他类型均封闭，拒绝未声明字段。extensions 为可选、带命名空间键的扩展对象；不能隐藏必须执行的约束。 |
| 数组 / null | 集合字段必填；空集合写 []，已知空对象写 {}。未知值只在 schema 允许处写 null。数组默认不暗含优先级；dispatch_now 的排列明确表示建议应用顺序。 |
| 完整快照 | complete 必须为 true，表示当前已物化任务和资源事实完整；未知未来分支用 known_graph_complete=false 明示。首期不支持静默截断或把分页结果当完整快照。 |

| 公共字段 | 请求 / 响应 | 含义 |
| --- | --- | --- |
| protocol、schema_version、operation | 均必填 | protocol=unilab.scheduler；schema_version=1.0；operation 为 describe/create/decide/close。 |
| request_id | 请求和成功响应必填 Uuid | 成功响应原样回显。错误若无法解析身份可为 null；无法关联的错误不得触发任何决策应用。 |
| timeout_ms | 请求必填，1～600000 | 宿主从调用发出时使用单调时钟计时；覆盖调用与决策接受时限，不依赖引擎与宿主的墙钟一致。 |
| body | 请求必填 | 按 operation 对应 DescribeInput/CreateInput/DecideInput/CloseInput。 |
| ok、result / error | 响应必填 | ok=true 时只出现 result；ok=false 时只出现 error。成功结果必须符合对应操作类型。原生返回码成功不等于业务 ok=true。 |

同一实例最多一个 decide 在途。create/close 与 decide 不并发；宿主负责串行化。不同调度域实例可以并行；同一调度域只有一个活动引擎实例。宿主持久登记唯一 active run_epoch，并在每次决策接受与派发事务中再次验证，旧宿主不能继续写入。请求重传必须复用原 request_id 和完全相同的 UTF-8 请求字节；修改任何输入必须产生新 request_id。

## 13.3 describe：能力发现与配置契约

| 输入字段 | 类型 / 必填 | 规则 |
| --- | --- | --- |
| host_version | String，必填 | 宿主构建标识，仅作诊断，不代替 schema/ABI 协商。 |
| supported_schema_versions | String[]，必填且非空 | 宿主实际实现的精确协议版本集合；首期包含 1.0。 |
| required_capabilities | String[]，必填 | 运行场景必须具备的能力集合；首期最少包含 core.v1。 |
| extensions | Extensions，可选 | 纯附加信息，缺省为不提供。 |

| 输出字段 | 类型 / 必填 | 规则 |
| --- | --- | --- |
| engine_id、engine_version、build_id | Id / String / String，必填 | 分别是稳定引擎身份、发行版本和构建标识；不与 schema 或 ABI 版本混用。 |
| supported_schema_versions、capabilities | String[]，必填 | 仅声明实际实现并通过测试的协议版本和能力。core.v1 包含本文任务范围、反馈与恢复语义；forecast.v1 表示可返回预测。 |
| config_schema | JsonObject，必填 | 该引擎配置的完整 JSON Schema 2020-12；只允许文内本地 $ref，宿主不加载远程 schema。平台与引擎均校验 config。 |
| checkpoint_formats | Id[]，必填 | 本构建可读取和输出的 opaque 状态格式标识；无持久模型状态的引擎可返回 []。 |
| limits | Limits，必填 | 声明请求/响应字节、Task、节点、候选、反馈批次和解码后状态字节的支持上限。 |
| extensions | Extensions，可选 | 附加描述；不能作为核心能力的替代声明。 |

describe 无业务副作用。宿主选择双方都明确支持的最高精确 schema 版本；没有交集返回 UNSUPPORTED_SCHEMA。describe 使用 v1.0 固定发现信封；未来协议必须保留此发现入口或通过新的 ABI major 明确区分。不得按 engine_version 猜测协议兼容。

## 13.4 create：实例、配置与恢复输入

| 输入字段 | 类型 / 必填 | 规则 |
| --- | --- | --- |
| instance_id、domain_id、run_epoch | Uuid / Id / Uuid，必填 | instance_id 由宿主生成；domain_id 标识一个调度域；宿主重启或重建执行控制世代时生成新 run_epoch，用于隔离旧响应。 |
| config_id、config | Uuid / JsonObject，必填 | config_id 对应一份不可变、已持久化的有效配置；内容变化必须生成新 ID 并重建实例。config 必须通过 describe.config_schema。 |
| required_capabilities、limits | String[] / Limits，必填 | 能力必须全部满足。limits 为宿主期望值与引擎上限逐项取较小值后的配额；返回必须与接受值一致，不能静默截断输入。 |
| restore | Restore，必填 | 字段为 mode、checkpoint、feedback_base_seq、allow_model_reset；恢复语义见下文。 |
| extensions | Extensions，可选 | 命名空间扩展；有行为影响的扩展必须预先协商能力。 |

| 输出字段 | 类型 / 必填 | 规则 |
| --- | --- | --- |
| instance_id、domain_id、run_epoch、config_id | 对应身份，必填 | 原样回显输入，任何不匹配均拒绝实例。 |
| capabilities、limits | String[] / Limits，必填 | 实际启用能力及接受的配额。 |
| recovery_mode、checkpoint | resume 或 rebuild / Checkpoint，必填 | resume 返回原已提交 checkpoint；rebuild 返回 revision=0、feedback_cursor=feedback_base_seq、state=null 的新基线。 |
| warnings | Reason[]，必填 | 重建模型等明确退化在此说明；正常为空数组。不得用 warning 掩盖不兼容输入。 |

同一已加载引擎中，重复 create 同一 instance_id 和相同原始请求返回原结果；同一身份绑定不同配置、调度域或世代返回 IDEMPOTENCY_CONFLICT。不同实例互不共享可修改状态。动态库卸载后原 instance_id 失效，重启使用新 instance_id 与 run_epoch。

基础队列的 config 由该引擎 schema 明确要求 queue_order、paused_task、failed_task、canceled_task、manual_outside_scope。附件示例采用 fifo / hold / advance_after_settlement / advance_after_settlement / reject，仅演示建议策略组合，不把这些值宣告为已批准的产品默认值。实现可先按此组合完成可测试配置，正式发布前确认默认值；缺少必需配置返回 CONFIG_INVALID。商业引擎通过自身 config_schema 定义算法参数，不新增公共 API。

## 13.5 decide：完整输入

| 字段 | 类型 / 必填 | 职责 |
| --- | --- | --- |
| instance_id、domain_id、run_epoch | 对应身份，必填 | 必须与 create 绑定一致；所有实例内请求均检查。 |
| compute_budget_ms | UInt，必填，1～600000 | 引擎内部计算预算，必须小于等于 timeout_ms；引擎须为编码与返回留出余量。 |
| snapshot | Snapshot，必填 | 任务视图、资源视图、在途事实与上一次已接受 Task 范围。 |
| checkpoint | Checkpoint，必填 | 宿主最后已提交的算法状态与反馈位置，是本轮唯一有效状态基线。 |
| feedback | FeedbackBatch，必填 | checkpoint.feedback_cursor 之后的连续反馈批次；空批次仍传入。 |
| extensions | Extensions，可选 | 附加信息，遵守兼容与能力规则。 |
| apply_budget_ms | UInt，必填，1～600000 | 从接受决策时起计算的普通推进授权有效期；与调用 timeout_ms 分开。到期未进入执行准入的意图作废，已准入 attempt 进入独立的真实执行生命周期。 |

| Snapshot 字段 | 类型 / 必填 | 语义 |
| --- | --- | --- |
| revision、captured_at_ms | Counter / UInt，必填 | revision 是同一调度域的持久事实修订；captured_at_ms 是快照时间。revision 不因每次读取或墙钟流逝而自动变化。 |
| feedback_watermark | Counter，必填 | 该一致快照覆盖到的反馈日志末尾位置。反馈与快照必须属于同一已提交事实切面。 |
| complete、execution_gate | true / open 或 held，必填 | 完整的是当前执行事实与引用闭包。held 时引擎只能 wait/sync；已确认结果接收与结算仍继续。只有 lifecycle=running 才允许 open。 |
| task_view、resource_view | TaskView / ResourceView，必填 | 分别承载完整任务/节点与资源/候选/占用。 |
| inflight | InFlight[]，必填 | 所有已提交、运行中、取消请求中和未决执行。 |
| head | Head 或 null，必填 | 保留最近选择：decision_id、task_scope、status、accepted_at_ms、run_epoch、authorization_valid、authorization_expires_at_ms。恢复旧 epoch 的 head 可供选队参考，但 authorization_valid 必须为 false。wait 的授权为 false、到期时间为 null。advance 的到期时间仅供诊断，执行有效期由宿主单调时钟、当前 epoch 和撤销事实共同判定。 |
| extensions | Extensions，可选 | 不参与核心执行授权。 |
| lifecycle | running/draining/recovering/engine_fault，必填 | 平台运行阶段；优先级 engine_fault > recovering > draining > running。由平台维护，插件无权解除。 |
| capacity_view | CapacityView，必填 | 运行容量池及真实占用；与 describe.limits 的消息容量无关。没有运行配额时 pools=[]，不能用空数组遗漏已配置配额。 |

### 13.5.1 任务与节点字段

| Task 字段 | 类型 | 含义与约束；均必填，extensions 除外 |
| --- | --- | --- |
| task_id、queue_seq、created_at_ms | Id / Counter / UInt | 稳定任务身份、持久入队序号和创建时间；queue_seq 按调度域分配，重启不重排。 |
| priority、wait_since_ms | Number / UInt 或 null | 原始优先级和等待起点；平台不在这里计算老化结果。 |
| status | pending/running/succeeded/failed/canceled | 统一状态投影；原系统其他状态须显式映射，不能把无法理解的状态默认为 pending。 |
| admission | pending/admitted/blocked | 业务准入状态；尚未注册 EdgeScheduler 的 Task 同样必须出现。 |
| control、execution_mode | active/paused/cancel_requested/recovery_hold；normal/step/single_node | 平台控制事实。具体单步与人工授权另由节点字段表达。 |
| normal_progression_allowed、terminal_settled | Boolean / Boolean | 前者仅表示平台普通自动推进门禁，不是单步许可的总开关；受限单步按 §13.10.1 单独判定。后者只在终态且必要结算结束时为 true，不以“无 ready”替代。 |
| known_graph_complete、blockers | Boolean / Reason[] | 已知图是否覆盖剩余路径及当前阻塞原因。false 表示未来仍有待展开或待确认部分。 |
| extensions | Extensions，可选 | 附加信息。 |
| workflow_id | Id，必填 | 工作流定义身份；不同 Task 可共享。不得使用旧 Edge workflow_id 实例别名填充。独立单动作任务使用平台稳定分组身份。 |
| capacity_pool_ids | Id[]，必填 | 激活该 Task 必须同时满足的 active_tasks 容量池；已持有的池不重复计数。 |

| Node 字段 | 类型 | 含义与约束；均必填，extensions 除外 |
| --- | --- | --- |
| ref、job_id、attempt_no | NodeRef / Id 或 null / UInt | 节点是实际运行实例；设备派发要求已有 job_id。平台批准重试时才增加 attempt_no，单纯重算不增加。 |
| kind | device/condition/loop/workflow_command/material_source/manual | 执行种类；条件求值、循环展开及本地状态提交始终在 UniLabOS。 |
| status、predecessors | 节点状态枚举 / NodeRef[] | 状态为 pending/running/succeeded/failed/canceled/skipped/uncertain；依赖均引用已提供的真实节点实例。 |
| ready、parameters_final | Boolean / Boolean | 逻辑及控制条件是否允许推进、参数是否最终确定。未来节点可以存在但不能因此立即派发。 |
| parameters_revision、parameters | Counter / JsonObject | 本节点已知参数修订与参数值；未最终确定时仅作规划事实，不可派发。 |
| action_id、action_revision | Id 或 null / String 或 null | 设备动作与其契约修订身份；非设备节点可为 null，不承载任意可执行代码。 |
| information_boundary、manual_approved、step_authorized | Boolean，均必填 | 未知未来信息边界、真实人工批准和平台指定节点的有效单步/单节点许可；引擎不能伪造。step_authorized 只可覆盖暂停导致的自动门禁关闭，不能覆盖取消、恢复、排空、故障、依赖、参数或资源约束。 |
| blockers、extensions | Reason[] / 可选 Extensions | 阻塞原因及附加信息。 |
| control_region_id | Id 或 null，必填 | 节点所属的最内层已物化控制区域；顶层 null。控制节点自身属于父区域，不属于其控制的子区域。 |
| manual_mode | none/confirm_then_execute/material_io/intervention，必填 | kind=manual 时必须为后三者之一；其他 kind 必须为 none。确认后设备动作、物料人工确认和故障干预须区分，不能仅靠一个 manual_approved 布尔值混用。 |
| capacity_pool_ids | Id[]，必填 | 该节点取得执行准入时需要的 inflight_jobs 容量池。人工准入同样占用，continue_attempt 不重复领取。 |

Node.ready 表示本轮逻辑依赖与对应推进阶段的控制条件成立，不含引擎是否选择它；单步节点按 §13.10.1 的受限许可判定。未批准人工节点可 ready=true 并请求 admit_manual；真实启动仍需人工批准。已准入节点为 running，不能为了继续执行回退为 pending。task_scope 可保留暂停任务，但不能自行解除平台控制。

### 13.5.2 控制区域事实：TaskView.control_regions

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| region_id、task_id、controller | Id / Id / NodeRef | 区域身份、所属任务和控制节点；所有引用来自当前快照。 |
| parent_region_id、kind | Id 或 null；condition/loop | 区域关系无环；controller 的 control_region_id 必须等于 parent_region_id。parent 为 null 表示 controller 在顶层。每个控制节点最多控制一个当前区域；controller.kind 必须与区域 kind 相同。 |
| phase、iteration_no | pending/awaiting_result/body_active/resolved；UInt 或 null | 当前控制阶段；loop 未展开时 iteration_no=null，物化后为持久轮次号；condition 为 null。 |
| members、entry_nodes、exit_nodes | NodeRef[] | members 仅含直属成员，恰为 Node.control_region_id=region_id 的全部当前节点，不递归包含后代。entry_nodes/exit_nodes 是成员诱导 DAG 中无内部前驱/无内部后继的节点集合（空成员对应空集合）；包含已完成/跳过成员，不按 ready 裁剪。子区域以其 controller 作为父层的一个直属节点。 |
| input_nodes、unmaterialized_tail | NodeRef[] / Boolean | input_nodes 是下一次控制判断直接读取的真实结果节点，可位于同 Task 的区域外或后代区域；它不是 members/exit_nodes 的别名。unmaterialized_tail 表示本区域及其后代仍有未知尾部；子区域为 true 时所有祖先必须 true，resolved 必须 false。 |

平台投影真实结构，优化目标与信息前缀选择仍属引擎。Node.predecessors 表示当前已物化实例 DAG 的结构依赖，含已成功/跳过前驱；未满足依赖由状态推导，不能随完成删边而改变结构出口。归档节点只有在不再属于当前依赖、区域、资源或恢复引用闭包时才可移出快照。不得加入 controller→body→controller 的假依赖环。

区域阶段：pending 表示该区域尚未激活控制体；awaiting_result 表示当前没有活动控制体、控制判断正等待 input_nodes 的结果；body_active 表示已选分支/当前循环体已激活、区域完成尚未提交；resolved 表示该区域已完成且不存在未知尾部。区域阶段不是 controller.status 的别名，条件判断节点可以 succeeded 而所选分支区域仍 body_active。loop 物化前 iteration_no=null、物化后保持持久轮次号；condition 始终为 null。

嵌套边界：父层出口引用子区域 controller 时，它表达子区域完成屏障，不能仅因条件 controller 已 succeeded 就认为子区域已结束。引擎可通过 parent_region_id 递归读取子区域 exit_nodes、input_nodes、phase 和未知尾部来构建自己的信息前缀；真实完成与 ready 仍由平台决定。结构出口与判断输入分开保留，不替引擎预选目标。

附件 nested_regions_request 含外层 loop → condition → 内层 loop：外层 members 只列 condition-controller 与外层尾节点；condition 区域只列内层 loop-controller；内层 loop 区域只列当前轮动作。递归把该动作加入外层 members，或把后代动作直接冒充父层出口，均为 SNAPSHOT_INVALID。

### 13.5.3 资源、候选、占用与在途字段

| ResourceView 集合 | 元素字段 | 规则 |
| --- | --- | --- |
| resources: Resource[] | resource_id、kind、availability、attributes | kind=device/site/material_lock/other；availability=available/unavailable/unknown。v1 每个资源键是独占冲突单元；容量资源需先规范化或另协商能力，不能默认为无限容量。 |
| conflicts: Conflict[] | left、right | 不同资源键的对称冲突关系；同键隐含冲突。只记录一份无序对，不要求传双向边。所有引用键均在 resources 中。 |
| options: Option[] | option_id、node、attempt_no、device_id、resource_ids、eligible_now、parameters_revision、resolved_parameters、binding_facts、blockers | 平台生成的合法动作与设备/资源绑定方案。eligible_now=false 可用于预测，不能现在执行。参数不同的候选有不同 ID；选择候选不能绕过实时许可检查。 |
| allocations: Allocation[] | allocation_id、owner_task_id、owner_node、resource_ids、kind、release_boundary | 已存在的真实持有，kind=inflight/continuous/external/uncertain；外部占用可无任务所有者。release_boundary=null 表示未知退出边界；非 null 也不是已释放证明。 |
| intervals: Interval[] | interval_id、owner_task_id、resource_ids、members、acquire_node、release_node、held_allocation_ids、contained_in | 描述跨节点持续资源区间和包含关系；以 held_allocation_ids 关联真实持有，避免把同一占用重复相加。未知进入/退出边界可为 null。 |
| waiters: Waiter[] | node、attempt_no、resource_ids、enqueued_at_ms | 持久等待事实；结合 Task.priority 由引擎排序。平台保留真实互斥与持有者继续执行所需的约束，不另算竞争优先级。 |

Option 另有必填 timing_profile（TimingProfile 或 null）：非 null 为 {spec_id,spec_version,timing_boundary}。它是准入时可冻结的计时规则元数据，不含公式或学习模型；未知时为 null，不能臆造版本。Option.resolved_parameters 是绑定后的完整已知参数，parameters_revision 必须与节点一致。binding_facts 与 Resource.attributes 按注册契约解释，不能隐藏未协商的核心约束。

| InFlight 字段 | 语义 |
| --- | --- |
| attempt_id、node、attempt_no、job_id | 已完成执行准入的真实尝试及作业身份；执行准入早于真实开始，不能由调度接受记录替代。 |
| command_ids、device_id、resource_ids、parameters、parameters_revision | 原命令、绑定、实际参数与其修订；continue_attempt 不得更换。command_ids 为空仅允许无设备命令的本地人工结算；不能据空列表推断未执行。 |
| dispatched_at_ms、started_at_ms | 前者是首次执行准入/派发登记时间，不保证已发送设备；后者只记录可证明的开始时间，否则 null。 |
| status | admitted/submitted/running/cancel_requested/uncertain。admitted 表示准入已提交但尚未交付执行链路；submitted 不等于已经开始；uncertain 可覆盖业务已终态但仍未结算的尝试。 |
| start_state | NOT_STARTED/START_COMMITTED/UNKNOWN。NOT_STARTED 必须由持久开始闸门证明；START_COMMITTED 表示一次性开始权已提交，不能据此再次调用动作；证据缺失为 UNKNOWN。它是执行权威事实，不由引擎生成。 |
| manual_phase | none/awaiting_approval/approved；独立于执行状态，不把“批准”当作“开始”。 |
| continuation_allowed | 平台对本轮普通 continue_attempt 的最终许可：原尝试、NOT_STARTED、绑定完整、人工批准已满足，并满足普通自动门禁或绑定在该 attempt 上的有效单步许可。Task 取消/恢复、域级 held 或非 running 时为 false；排空特殊续行另走平台规则。 |
| timing_profile | TimingProfile 或 null；准入时冻结的计时规则元数据，与实时采集结果共同构成 Observation。 |

未知释放时间不能当作零时长。实际资源与配额只能按平台结果和结算规则释放。连续 reservation 和当前 active-use 的关系由 Allocation.kind、Interval.members/held_allocation_ids 等明确表达；平台保持持有者合法续行规则，不允许外部等待者反向阻塞。

### 13.5.4 运行容量：CapacityView

| CapacityPool 字段 | 类型 | 规则 |
| --- | --- | --- |
| pool_id、kind | Id；active_tasks/inflight_jobs | 独立容量池身份和计数单位，均为整数单位，不与资源锁或协议 limits 混用。 |
| scope、workflow_id | domain/workflow；Id 或 null | domain 对当前调度域全局生效，workflow 对指定工作流定义生效；前者 workflow_id=null，后者必填定义 ID。 |
| limit、used、external_used | UInt 或 null；UInt；UInt | limit=null 表示明确不限额，0 表示禁止新增；used 是总实际占用，external_used 是不在本快照任务集合内但由同一权威计入的占用。 |
| holders | CapacityHolder[] | 每项 {task_id,attempt_id}。Task 池 attempt_id=null，同 Task 计一次；Job 池必须引用当前在途 attempt，同 attempt 计一次。used=holders.length+external_used。 |

Task.capacity_pool_ids 和 Node.capacity_pool_ids 必须列出全部适用池。已准入人工 Job 计入 Job 容量；canceling Task 以及未决/取消请求中 attempt 不因预测结束释放。终态 Task 是否仍占 active_tasks 容量按既有平台计数规则投影，terminal_settled 另行决定队列可否切换。配额降低后 used 可以暂时大于 limit，此时只阻止新增，不撤销既有占用。

引擎按 dispatch_now 顺序试算整批需求：同 Task 首次激活只计一次，continue_attempt 不计新增。意图不能在输入快照上已明确超额，否则 INVALID_DECISION；接受后的并发占用变化可返回 CAPACITY_FULL 并部分拒绝。平台在执行准入事务内复验，资源/配额领取失败不得遗留半份新准入。跨域共享配额必须由同一权威原子计数，不能简单拆域规避。

### 13.5.5 执行反馈字段、历史引用与唤醒

| FeedbackBatch / FeedbackEvent 字段 | 类型 | 规则 |
| --- | --- | --- |
| after_seq、through_seq | Counter，必填 | after_seq 必须等于输入 checkpoint.feedback_cursor；事件覆盖 (after_seq, through_seq]。空批次时两者相等。 |
| has_more | Boolean，必填 | through_seq < snapshot.feedback_watermark 时必须为 true；否则为 false。不允许越过水位。 |
| events | FeedbackEvent[]，必填 | 严格按 seq 递增且连续；不得过滤掉日志中的中间事件。大小不超过协商上限。 |
| seq、event_id、occurred_at_ms、payload | Counter / Uuid / UInt / 事件联合类型 | seq 负责消费顺序，event_id 负责身份追溯；真实事件发生时间不替代日志顺序。payload.kind 决定字段。 |

| payload.kind | 额外必填字段 | 意义 |
| --- | --- | --- |
| decision_accepted | decision_id、task_scope | 宿主已持久接受该范围；不表示设备已开始。 |
| decision_rejected | decision_id、reason | 整个决策未被接受；对应 next_state 不提交。 |
| intent_result | decision_id、intent_id、node、attempt_no、disposition、attempt_id、command_ids、reason | disposition=admitted/submitted/rejected/uncertain。admitted 表示准入已提交、仍未实际发送（如待人工确认）；admitted/submitted/uncertain 必须关联真实 attempt；rejected 必须有 reason。已准入后发生交付不确定仍保留原身份，不能回退成全新候选。 |
| execution_started | node、attempt_id、started_at_ms | 平台确认实际开始。 |
| execution_result | node、attempt_id、outcome、terminal_confirmed、resources_settled、unknown_command_ids、result、errors | outcome=success/failure/canceled/timeout/unknown。仍有未知命令时 terminal_confirmed=false；settled=false 时不能假定资源已释放。 |
| task_changed | task | 完整 Task 投影；最新 snapshot 仍为当前事实权威。 |
| resources_changed | resource_ids、reason | 提示资源事实变化；当前完整资源快照提供实际状态。 |
| observation_recorded | observation | 原始实际耗时样本；用于模型学习，不能用预计耗时伪造。 |
| snapshot_resync | reason、model_reset | 明确的恢复重建事件；说明是否重置模型。 |

| Observation 字段 | 规则 |
| --- | --- |
| 原有实际观测字段 | observation_id、action_id、action_revision、device_id（可 null）、parameters、elapsed_ms、timing_boundary、outcome、complete、calibration 均必填；非完整成功样本不能默认作为完整成功耗时。 |
| timing_spec_id、timing_spec_version | 均必填且同时为非空 Id 或同时 null；分别为采集关联的规则身份与版本，独立于 action_revision。已知旧 spec_version 原样保留，不以当前规则覆盖。 |
| provenance、source_ref | provenance=live/legacy_import；source_ref 为稳定来源记录键，必填。live 的 node/attempt_id 必须非 null；legacy_import 可在确实缺失映射时为 null，保留已有原始 observation_id。 |
| node、attempt_id | 历史追溯引用，不要求节点仍在当前快照；不授予执行权限。新实时采样必须绑定实际 attempt，重试各自获得稳定样本身份。 |

采样由平台基于真实边界生成，基础队列也记录原始样本。规则元数据从已验证的只读配置/配置清单冻结；平台不调用估时器私有 _spec_for，也不计算模型公式。无规则元数据仍可保存原始样本，匹配、排除或显式再解释由引擎决定。旧记录以其 action key 和已有 spec_version 映射；无法证明的字段标 null，不生成虚假 UUID。规则更换只影响新准入，已准入样本保持原身份与版本。

当前执行引用与历史追溯引用分开校验：Node 依赖、当前区域、Option、Allocation、Interval、容量持有者和所有执行意图必须解析到当前快照；反馈事件中的历史 Task/Node/attempt/resource/decision 及 Observation 只校验身份、内容和可追溯性，不能强迫归档对象回到当前队列。已结束节点仅保留到当前依赖、占用或恢复引用闭包消失。

同 observation_id 同内容重放只学习一次，异内容为 IDEMPOTENCY_CONFLICT；“同内容”指经固定迁移映射后的规范 Observation，不包括反馈包装的 seq/event_id。迁移映射第一次导入后须持久固定，不因后来补齐信息修改同一样本。配置/模型重建可在新模型内重新消费同一批原始样本，但不得重复产生执行结果。

所有成功 decide 响应（advance/wait/sync，含最后一批和空批次）必须 consumed_feedback_seq=输入 feedback.through_seq；不支持部分批次确认。has_more=true 时只允许 sync、task_scope=null、dispatch_now=[]、forecast=null。宿主暂停普通推进并逐批确认；纯 sync 接受仅写请求/恢复审计账本，不追加到可消费 FeedbackEvent 日志，不生成 decision_accepted/decision_rejected 或其他替代 ACK。

反馈流与审计账本区分：advance/wait 的接受可产生一条 decision_accepted，但该审计事件不单独唤醒；sync 的成功、重传和拒绝均只留独立审计，不占反馈 seq。sync 处理过程中产生的真实业务结果仍正常入反馈流。重复 request_id 不得重复追加接受事件。

追赶完成：宿主提交 checkpoint 后，在同一权威串行切面比较 committed feedback_cursor 与当前 feedback_watermark；相等且其他恢复条件满足才可退出追赶，不相等则安排下一批。新事件若发生在比较之后，按正常真实事件唤醒规则处理。有限输入最终停止时，有限个非空批次必须排空；持续真实事件流不承诺固定轮数。追赶结束最多安排一次普通决策，其接受 ACK 留待下一次真实触发，不自激。

最小批次示例：max_feedback_events=1、起始日志 seq=1..3，连续三次 sync 分别确认 1、2、3；每次同步不增加反馈水位，第三次后可完成恢复。若其间新增真实事件 seq=4，则再同步一次。响应少确认、回退或越过 through_seq 一律 INVALID_DECISION，next_state 不提交。

| 事件类别 | 唤醒规则 |
| --- | --- |
| 真实 Task/节点/资源/容量/人工确认/开始/结果发生变化 | 修改相关事实 revision 并合并触发一次重算；重投且事实未变，不再次触发。容量变化可使用 resources_changed（resource_ids=[]、reason=CAPACITY_CHANGED），实际详情见 capacity_view。 |
| decision_accepted、decision_rejected、纯审计 intent_result | 仅 advance/wait 等非 sync 业务决策的审计可进入反馈流，且不单独唤醒。sync 审计只进独立账本；head 的新 decision_id 或 checkpoint 修订不触发重算。 |
| STALE_DECISION、局部拒绝 | 若输入事实确实变更，按最新修订合并一次重算；同一基线反复出现相同拒绝不自激重试，等待事实改变或有界退避计时器。违反协议则停调。 |
| 反馈追赶、样本导入、恢复启动 | 每个成功响应完整确认输入 through_seq；逐批提交后按最新水位判定，有限输入停止后须有限结束。sync 不制造可消费 ACK；追赶结束最多追加一轮普通决策。 |
| wakeup_after_ms / 授权到期 | 宿主合并计时器；0 必须经过最小退避和速率限制。授权到期撤销尚未准入的推进，若有未完成普通推进需求再安排重算；空队列 wait 且 wakeup=null 时保持静默。 |

## 13.6 decide：输出字段与应用规则

| 输出字段 | 类型 / 必填 | 规则 |
| --- | --- | --- |
| decision_id | Uuid，必填 | 固定等于本次 request_id，便于重传去重和审计。 |
| instance_id、domain_id、run_epoch | 对应身份，必填 | 回显实例、调度域与世代。 |
| base_snapshot_revision、base_checkpoint_revision | Counter，必填 | 精确回显本次输入基线；不能改写宿主事实修订。 |
| status | advance/wait/sync，必填 | advance 允许平台在有效期内尝试普通推进；可仅推进本地控制。wait 仅保留选择，sync 仅同步状态/反馈，两者均不授予新的普通执行权。 |
| task_scope | Id[] 或 null，必填 | advance/wait 必须为数组，替换上次普通 Task 范围；[] 表示不允许任何普通推进。sync 必须为 null，表示不修改原选择。 |
| dispatch_now | DispatchIntent[]，必填 | 保留字段名，承载 start/admit_manual/continue_attempt 三类本轮执行意图；wait/sync 必须为空。所有普通意图所属 Task 必须在 task_scope 内。 |
| consumed_feedback_seq、next_state | Counter / EnginePayload 或 null，必填 | 成功响应必须 consumed_feedback_seq=输入 feedback.through_seq，包含空批次和最后一批；不支持部分消费。next_state 与游标经宿主接受才共同生效。null 表示不保存 opaque 状态，不能表示沿用旧 payload。 |
| wakeup_after_ms | UInt 或 null，必填 | 接受决策后建议等待时长；null 表示仅靠事件唤醒。0 可请求立即重算，宿主应合并唤醒避免忙循环。 |
| reasons | Reason[]，必填 | 可机器读取的等待/拒绝解释；wait、sync 或 advance 但无推进内容时至少一项。 |
| forecast | Forecast 或 null，必填 | 只有已协商 forecast.v1 才能非 null；预测不授予执行权限。 |
| extensions | Extensions，可选 | 命名空间诊断扩展。 |

DispatchIntent 字段均必填：intent_id、kind、node、attempt_no、attempt_id、option_id、parameters_revision。intent_id 为 UUID，在同一 decision 内唯一；每个 node/attempt 本轮最多一个意图。三个 kind 均不能注入设备命令、任意参数或新的真实执行身份。

| kind | 输入前提与字段 | 平台落实 |
| --- | --- | --- |
| start | 节点 pending、ready、parameters_final、有 job_id；kind=device，或已获得平台明确授权的 manual/intervention。attempt_id=null，option_id 引用 eligible_now=true 的合法绑定。 | 原子取得资源/容量及执行凭证，形成唯一 attempt，然后交付执行链路。错误处置手动动作必须依据平台会话许可；跨队列例外不能由插件自行批准。 |
| admit_manual | 节点 pending、kind=manual、manual_mode=confirm_then_execute/material_io，ready=true、参数最终确定。attempt_id=null，option_id 必填且 eligible_now=true；不要求 manual_approved=true。 | 只完成原子准入与确认请求，返回 admitted；取得完整资源/容量，但不开始设备动作。确认被拒绝或取消仍由平台结算原 attempt。 |
| continue_attempt | 引用当前 InFlight，node/attempt_no/job/参数修订一致；节点为 running，start_state=NOT_STARTED、continuation_allowed=true；人工模式须 approved。attempt_id 必填，option_id=null。 | 复用原 attempt、command、参数、资源绑定和 Claim/Fence；不新领容量、不增 attempt、不切设备。confirm_then_execute 交付原设备动作；material_io 走原平台库存确认/结算入口。 |

人工确认批准是平台事实，不能在 Decision 中伪造。批准并不自动变成 pending：仍保留已准入的 running 节点与 InFlight。未批准人工节点不创建执行命令副作用；提交人工准入记录也不表示设备已经开始。

Forecast 必填 origin_ms、nodes、partial_task_ids、quality、metrics。每个 ForecastNode 含 node、option_id（可 null）、start_offset_ms、end_offset_ms、fixed、tentative；end 不得小于 start。quality=feasible/optimal/partial；未覆盖完整 Task 的预测必须标入 partial_task_ids。未知参数、未展开尾部及信息边界不能伪装成完整任务完工预测。

1. 先验证整个响应的结构、身份、能力、引用、反馈游标、状态摘要和三类意图的前提。输入时已超容量或引用非法为 INVALID_DECISION，整份拒绝，next_state 不提交并停止新自动调度。
2. 在宿主权威事务中比较 active run_epoch、snapshot revision 和 checkpoint revision；过期为 STALE_DECISION，保留原 checkpoint。timeout_ms 只约束调用与决策接受，迟到响应不能被接受。
3. 接受时原子保存 decision、head、待应用调度意图与 checkpoint；反馈游标随 next_state 一起提交。advance 的 head 记录当前 run_epoch 和 apply_budget_ms 对应有效期；wait 授权无效；sync 不替换 head，也不创建执行意图。
4. 按 dispatch_now 顺序应用，逐项复验当前 epoch/head 授权、Task 门禁、资源、容量、参数及 attempt。进入执行准入事务后，建立或复用真实执行身份；只有准入成功才反馈 admitted/submitted。状态变更可造成后续局部拒绝，但不能回滚已发生动作。
5. 本地控制提交同样检查 head、Task 范围及普通自动/受限单步分支；单步仅提交指定控制节点并原子消费其许可。会激活 Task 时复验容量；图展开产生新事实。Task 范围不授权平台补选设备或人工准入动作。
6. 新 head、授权到期或旧 epoch 只使尚未进入执行准入的调度意图 SUPERSEDED/DEADLINE_EXPIRED/RECOVERY_REPLAN。已经持久准入的 attempt 不被这一操作抹掉；按实时开始、原身份恢复或取消/结算规则处理。
7. apply_budget_ms 从接受时用单调时钟计时；到期后的未准入意图和普通本地控制不得开始。authorization_expires_at_ms 为墙钟诊断值，不能据其重建重启后的有效授权。已准入但未实际开始的执行凭证仍须通过平台实时开始闸门，不能因曾接受决策跳过暂停、取消或恢复屏障。

head 中的 task_scope 是持久选择记忆，authorization_valid 是本进程/世代下可撤销的普通推进授权。只有当前 epoch、status=advance、authorization_valid=true、未过期且门禁允许时，才能进入新的普通推进。恢复、追赶反馈、引擎故障期间授权无效。sync 不会把旧 head 重新激活；只有新接受的 advance 决策可以授予新授权。

若输入 checkpoint.state 非 null，正常 decide 必须返回兼容的非 null next_state；不能通过 null 静默丢弃已保存模型。格式迁移只能输出 describe 已声明可读写的格式，状态摘要须校验。

引擎必须将输入 checkpoint 视为唯一已提交算法状态。候选计算可使用缓存，但不能把尚未接受的 next_state 当成下轮真相。宿主拒绝或崩溃后，用旧 checkpoint 重算必须仍然成立；不同旧请求不能共享已偷偷前移的队列游标。

### 13.6.1 排空、异常和已有 attempt 的边界

| 平台阶段 | 普通引擎意图 | 平台独立通路 |
| --- | --- | --- |
| running | 有效 advance 下可执行三类意图；继续原 attempt 也须按公开 continuation_allowed 选择。 | 真实结果、取消、结算和明确批准的故障处置始终由平台处理。 |
| draining | execution_gate=held；引擎仅 wait/sync，不接纳新 Job 或新的人工准入。 | 保留现有行为：已完整准入且人工已批准的同一 Job，可依据其原许可与实时门禁继续，以便排空。只能复用原身份/绑定，不新增 attempt、换资源或启动队列下一 Task；每次续行留痕。 |
| recovering | held；先恢复真实执行身份、占用和反馈；旧 head 不授权。 | 可重建原 NOT_STARTED 出站路由，但物理开始仍被恢复屏障拦住。恢复就绪并取得新普通授权后才启动；结果接收与对账不等待新引擎决策。 |
| engine_fault | held；未开始的普通动作停止，不能借 drain 例外继续越过物理开始边界。 | 继续已开始动作的结果接收、取消协调和必要结算；明确人工处置按平台受控干预规则，不能伪装成自动降级。 |

排空续行例外是已经准入的真实执行生命周期，不是跨队列创建新人工动作的权限。基础队列人工跨范围默认策略仍待产品确认；没有明确平台许可时拒绝。close 只关闭算法实例，begin_drain/drain_status/resume_from_drain 等整站接口继续归 UniLabOS。

### 13.6.2 结构校验之外的必检关系

- 当前身份唯一；依赖 DAG、区域父子和区间包含各自无环。区域 members 恰等于直属归属节点，入口/出口由直属成员诱导 DAG 推导；controller 属于父区域，kind 匹配，输入引用同 Task。子区域未完成不能被 controller 成功状态替代。历史引用仅作追溯。
- terminal_settled=true 仅在 Task 已终态且全部必要结算结束、没有未决命令/待核实占用时成立。Task 状态、控制、执行模式和 InFlight.start_state 分轴映射，不能用一个状态覆盖其他证据。
- Snapshot.lifecycle!=running 时 execution_gate 必须 held；head.authorization_valid=true 还必须匹配当前 epoch、advance、未过期且允许普通推进。恢复旧 head 只能作为选择记忆。
- start/admit_manual 必须引用 pending、ready、最终参数及 eligible_now 候选；continue_attempt 必须匹配原在途对象、NOT_STARTED、continuation_allowed 和原参数，不接受 option_id。START_COMMITTED/UNKNOWN 不能用 continue_attempt 再次调用。
- 容量池 kind/scope/引用及计数一致；同 Task 或 attempt 不重复计数，所有适用池均出现。按意图顺序核算新增占用，真实事务再次复验。
- 反馈连续且不越水位，所有成功响应 consumed_feedback_seq 必须等于输入 through_seq；sync 审计不得追加可消费 ACK。state 严格解码、摘要/格式/配额校验；Counter 检查 uint64 上界。
- TimingProfile 与实际 Observation 的边界/规则元数据一致，未知值不得伪造。存量数据的迁移映射固定且可追溯；是否用于学习由引擎明确判断。

## 13.7 错误码、正常等待和应用拒绝

Error 固定包含 code、message、origin、retryable、field_path、details。origin=host/engine/adapter；field_path 为 JSON Pointer 字符串或 null；details 为结构化诊断对象。程序依据 code 处理，不能解析 message。retryable 仅表示修复条件后可重试，不授权自动恢复调度或切换引擎。

| 错误码 | 产生条件 | 处理规则 |
| --- | --- | --- |
| INVALID_REQUEST | JSON/结构/值域非法或入口 operation 不匹配。 | 修正请求后使用新 request_id；不得执行。 |
| UNSUPPORTED_SCHEMA / UNSUPPORTED_CAPABILITY | 无共同精确协议版本或缺必需能力。 | 装配失败；调整配置/实现，不能降格忽略限制。 |
| CONFIG_INVALID / ENGINE_DISABLED | 配置不符合 schema，或选定引擎明确不可用。 | 停止该引擎的新调度；修复后显式恢复。 |
| INSTANCE_NOT_FOUND / INSTANCE_CLOSED | 实例不存在或已关闭。 | 宿主根据持久事实重建实例；不能复用旧 epoch 响应。 |
| INSTANCE_BUSY | 同一实例有未结束调用。 | 宿主串行化；等待已发调用结束或执行隔离方案，不并发 close。 |
| IDEMPOTENCY_CONFLICT | 同请求/实例身份对应不同内容或样本身份冲突。 | 停止相关请求并纠正身份生成；不按新任务重复执行。 |
| SNAPSHOT_INVALID | 引用缺失、非完整快照、事实/资源关系互相矛盾。 | 停止新自动调度并修复平台投影。 |
| STATE_INCOMPATIBLE / STATE_CORRUPT | 状态格式/引擎/config 不匹配，或 base64/摘要校验失败。 | 不得静默清空状态；进入恢复流程，由显式 rebuild 策略决定是否重建。 |
| FEEDBACK_GAP | 游标不一致、事件缺口/重复或水位非法。 | 补回连续日志；无法补回时进入明确 rebuild，不能跳过缺口确认。 |
| LIMIT_EXCEEDED | 协商容量超限。 | 反馈可缩小批次；任务/资源完整快照不可截断，需提高容量或显式重划调度域。 |
| DEADLINE_EXCEEDED | 调用超过宿主时限，或引擎无法在时限内正常返回。 | 停止新的自动调度，忽略晚到结果；保留在途结算。 |
| ENGINE_INTERNAL / ENGINE_UNAVAILABLE | 计算异常，或 worker/动态库不可用。 | 停止新自动调度，保留结果通路；诊断后恢复，不自动切换引擎。 |
| INVALID_DECISION | 响应结构、引用、选择或能力声明不合法。 | 整份响应拒绝；不提交 next_state，停止新自动调度。 |
| STALE_DECISION | 合法响应的基线已过期。 | 正常竞争结果：不提交本轮 checkpoint，读取新快照重新决策。 |
| PERSISTENCE_FAILED | 宿主不能提交接受记录、执行意图或反馈事实。 | 停止新增派发并协调权威状态；不能以内存结果代替持久接受。 |

“当前没有动作可执行”“等待资源”“Task 暂停”“求解预算内尚未找到方案”是正常 wait，返回 ok=true 与原因，不能混成引擎崩溃。SOLVER_BUDGET 表示引擎在宿主截止前正常结束本轮搜索；DEADLINE_EXCEEDED 表示调用时限违约，两者处理不同。

标准 Reason.code：NO_TASK、WAITING_DEPENDENCY、WAITING_RESOURCE、WAITING_ADMISSION、WAITING_MANUAL、WAITING_CAPACITY、CAPACITY_CHANGED、TASK_PAUSED、RECOVERY_REQUIRED、FEEDBACK_CATCHUP、SOLVER_BUDGET、NO_FEASIBLE_PLAN、UNKNOWN_DURATION、MODEL_RESET、OUTCOME_TIMEOUT、ERROR_WAITING、EXECUTION_UNKNOWN、CANCEL_REQUESTED。应用拒绝码：STALE_SNAPSHOT、TASK_GATE_CLOSED、NODE_NOT_READY、PARAMS_CHANGED、OPTION_CHANGED、RESOURCE_BUSY、INVENTORY_BLOCKED、CAPACITY_FULL、ALREADY_DISPATCHED、CONTINUATION_NOT_ALLOWED、START_STATE_UNKNOWN、SUPERSEDED、RECOVERY_REPLAN、DEADLINE_EXPIRED。Reason 不是 Error；可带 task_id、node、resource_ids 定位。

同一 (domain_id,task_id,node_id,attempt_no) 最多建立一个真实 attempt。start/admit_manual 只能建立它一次；continue_attempt 或原身份出站重放只能复用它，不算新执行。command 的传输重投依赖原 command 的幂等账本和一次性开始闸门；不能重调物理动作。开始权已提交或证据未知时只对账。

## 13.8 状态恢复、反馈确认与崩溃窗口

| Checkpoint / Restore 字段 | 规则 |
| --- | --- |
| Checkpoint.revision、feedback_cursor | 宿主持久算法状态修订和已确认消费位置；接受决策时一起推进。业务对象 revision 与 checkpoint revision 独立。 |
| Checkpoint.engine_id、config_id、state | 绑定引擎身份和不可变配置。state=null 表示无 opaque 模型；非 null 为 {format,data_base64,sha256}。摘要对 base64 解码后的原始字节计算。 |
| Restore.mode=resume | checkpoint 必须非 null；feedback_base_seq 等于其游标；engine_id/config_id/format 必须兼容。create 返回相同 checkpoint。 |
| Restore.mode=rebuild | checkpoint 必须为 null；由宿主显式设定反馈基线并建立 revision=0 的 checkpoint。只有完整权威快照能够恢复、未决执行已识别时才允许继续。 |
| allow_model_reset | 从已存在但不可用的模型状态恢复时，只有显式 true 才允许丢弃模型重新学习，并返回 MODEL_RESET 警告。首次无状态启动不算丢弃模型。它不允许丢弃实际占用、已接受 Task 范围或未决命令。 |

三个提交边界必须分别持久化：D=调度决策接受（只有选择与候选）；A=执行准入（已有 attempt/command、参数绑定、资源/容量/凭证）；S=开始权提交（一次性开始闸门）。D 不代表 A，A 不代表 S，S 不代表物理成功。禁止只用“设备是否已发送”判断恢复分支。

1. 先停止新的普通推进，设置 lifecycle=recovering、execution_gate=held，恢复真实 Task/Job、A/S 边界、执行意图、资源/容量、库存和反馈日志。生成新 run_epoch 并隔离旧宿主；旧 head 保留范围用于选队，但 authorization_valid=false，不能按旧到期时间恢复授权。
2. 检查 checkpoint 的引擎、config、format 和摘要；兼容则 resume。模型状态仅代表已接受算法状态，不能覆盖 A/S 事实。D 尚未到 A 的旧候选标记 RECOVERY_REPLAN；A 已提交者保持原 attempt/command 与冻结绑定。
3. 从已提交 cursor 后连续回放反馈；has_more=true 只允许 sync，最后一批也完整确认 through_seq。sync 审计不入可消费流。checkpoint 与消费确认原子提交后方可清理旧日志；按最新水位检查追赶完成，未完整恢复时保持屏障。
4. A 已提交且 start_state=NOT_STARTED 的，平台可重建原出站路由，但不增 attempt、不重算参数、不重新领取资源。当前实时开始门禁必须在原执行链路实际调用前核验；恢复屏障解除、且新 head 明确允许普通续行后，才可由 continue_attempt 恢复原命令。START_COMMITTED 或 UNKNOWN 的不能再调动作，只能原身份对账。
5. checkpoint 缺失/损坏/不兼容或日志无法补齐时，不静默清空。显式 rebuild 以当前一致水位为基线，保留真实 head 选择、在途和占用，记录 snapshot_resync。allow_model_reset 只允许重建算法模型，不允许改变真实执行身份。
6. 历史样本以原 observation_id 和固定来源映射回放为新的 observation_recorded 事件；历史引用可不在当前快照。引擎按 timing_spec_id/version、action_revision 和 timing_boundary 决定是否适用；未知或不匹配不得默认套入当前规则。缺完整历史且不允许冷启动时返回 STATE_INCOMPATIBLE。
7. 平台执行事实恢复完毕、原命令已分类、必需模型恢复/回放完成且追上最新一致水位后，可将 lifecycle 置 running、execution_gate 置 open；此时旧 head 仍无授权。再请求新一轮决定，只有新 epoch 下接受 advance 才允许普通推进。若恢复出多个 Task 在途，先保留全部真实存量并按迁移策略收敛，不能抹掉并发后宣称已串行。

| 崩溃位置/证据 | 恢复行为 |
| --- | --- |
| D 前 | checkpoint 不变，无执行授权；使用新 epoch 和新快照重算。 |
| D 后、A 前 | 只作废待应用调度候选，记录 RECOVERY_REPLAN。不得因此创建或取消真实 attempt。 |
| A 后、S 前；可证明 NOT_STARTED | 保留原 attempt/command、参数、绑定及资源占用；重建原出站意图，实时门禁仍可等待/拒绝。通过新普通续行授权后恢复原命令，不能生成第二个真实执行。 |
| S 已提交，或是否开始无法确认 | START_COMMITTED/UNKNOWN 均禁止再次调用物理动作。保持原命令与占用，等待结果、取消回执或明确物理核实。 |
| 结果已提交、反馈未消费 | 按连续日志重放结果与样本；平台结果/库存结算幂等，模型按 observation_id 去重。 |
| 同批部分已到 A，部分未到 A | 已准入的保留原身份；其余作废重算。保留接受的 checkpoint，应用结果逐项反馈，不能回滚已经落实部分。 |

### 13.8.1 算法实例、宿主与设备执行进程的重启须分别识别

| 故障域 | 状态与责任 |
| --- | --- |
| 引擎 worker/动态库实例故障 | 平台仍接收真实结果并结算；停止普通推进并撤销旧 head 授权。显式恢复时建立新 instance_id/run_epoch、恢复模型与反馈；不生成 execution_process_restarted 事件，也不终结业务 Task。 |
| 调度宿主进程重启、执行端身份未变 | 按 D/A/S 恢复；保留 command/attempt/Claim/Fence。恢复屏障防止旧 head 或已排队未开始命令提前执行。 |
| 设备执行进程更换 | 终止原业务推进，但按每个 attempt 的开始/结果证据分类。可证明 NOT_STARTED 且无副作用者取消原出站意图，完成回滚后释放对应占用；START_COMMITTED/UNKNOWN 且结果未确认者保留原身份、未决记录及占用，禁止重执行。Task 可失败，terminal_settled 仍为 false，等待物理核实与结算。禁止因进程更换批量释放所有 permit 或直接标记 settled。 |
| 仅连接中断，未证明执行进程更换 | 保留原身份并进入待核实；不能猜成未执行或伪造执行进程重启。 |

与当前代码的显式差异：task_scheduler_bridge.py:1912 的既有清理会释放软件 permit 并把 cleanup_status 置 settled。目标重构不得对 START_COMMITTED/UNKNOWN 的未决 attempt 直接复用这条路径；阶段一必须改为按证据分类。这是已记录的目标行为变化，不宣称当前实现符合。

未决投影：Task 可为 failed，但 control=recovery_hold、normal_progression_allowed=false、terminal_settled=false；Node.status=uncertain，InFlight.status=uncertain，start_state 保留 START_COMMITTED 或 UNKNOWN，continuation_allowed=false。保留原 command/attempt、资源 Allocation 与 Job 容量持有者；可把 Allocation.kind 转为 uncertain。active_tasks 是否计数继续遵从权威业务状态，不能据 Task 容量释放推断设备资源空闲。

资源清理边界：失效进程的传输路由和进程本地句柄可以撤销；影响互斥/库存/容量的 Claim/Fence/permit 不得留下可被新任务利用的空窗。若技术上必须更换旧凭证，先原子建立等效 uncertain 占用再停用旧凭证；公开视图持续显示保护。只有原命令结果或有记录的物理核实证明相应动作/占用已收敛，且平台库存与资源结算成功后，才释放对应占用。

完成判定：每个未决 command 均已分类、必要物理核实与结算结束、无剩余受保护占用，才允许 terminal_settled=true 和 cleanup_status=settled。允许按已核实范围部分释放；剩余未知项继续保护。命令失联、超时或执行进程身份改变均不能单独作为完成证据。

宿主保存的是已接受的算法状态。引擎只有收到 admitted/submitted/执行反馈后，才能将候选视为真实准入或执行；不以 next_state 推断成功。四个入口足以承载这些规则，不增加 commit_state、on_job_finished 或强制恢复回调。

## 13.9 close 与请求幂等

CloseInput 必填 instance_id、domain_id、run_epoch、reason；reason=shutdown/reconfigure/engine_failure。成功 CloseResult 为 {instance_id,status:"closed"}。宿主先停止新调用，再等待当前调用结束；仍在计算时返回 INSTANCE_BUSY，不释放被计算使用的对象。未完成调用挂死时只能按已选进程隔离方案处理，不能假定 close 能强制终止原生代码。

同一库生命周期内重复关闭已知 closed 实例返回同一成功结果。无此实例记录时返回 INSTANCE_NOT_FOUND。关闭算法实例不修改业务 Task，不释放资源锁，不取消设备命令；真正的执行与结果通路由平台继续管理。

宿主只允许重传当前在途或最近一次完成请求。引擎至少保留最近完成请求的字节指纹与原始响应，确保相同 request_id 重传得到相同结果；正在执行的同 ID 请求不得再次启动计算。更早请求由宿主持久日志处理，不再调用引擎。同 ID 不同内容返回 IDEMPOTENCY_CONFLICT。新世代使用新请求和实例身份；不要求跨进程保存易失的响应缓存。

## 13.10 schema、能力与状态兼容

| 变化类别 | v1 规则 |
| --- | --- |
| schema major | 删除/更名字段、改变单位、状态语义、必填项或执行授权语义均需新 major。没有共同版本则拒绝连接。 |
| schema minor | 仅可增加可选能力/字段；双方仍须选择明确实现的精确版本。已协商 1.0 时不得发送 1.1 顶层字段。 |
| 未知字段与枚举 | 核心对象拒绝未知字段；只在 extensions 或明确 JsonObject 中允许开放内容。未知核心状态/事件/错误码不能默认为成功、空闲或已结束。 |
| 必需能力 | 新资源约束、执行种类或影响安全合法性的语义，必须新增/升级 schema 或声明必需能力。不能只放进可忽略扩展。 |
| 预测能力 | forecast.v1 为可选；未协商时 forecast 必须为 null。真实时间线与正常执行不依赖预测。 |
| 引擎发行升级 | engine_version/build_id 改变不自动改变业务协议；先用 describe 验证共同 schema、能力与 checkpoint_formats。 |
| 状态格式 | state.format 属于引擎私有版本空间；兼容读取由新构建明确声明。库可在下一次正常 next_state 中迁移格式，宿主仍按接受事务提交。跨引擎不得复用 opaque payload。 |
| 配置升级与回退 | 配置内容变化产生新 config_id；默认不接收旧配置 checkpoint，采用明确 rebuild 或以后另定义迁移能力。回退前检查旧构建能否读取当前 state.format，不能强行装载。 |
| ABI 与 schema | ABI 管二进制调用约定；schema 管 JSON 业务语义；state.format 管算法快照。三者分开检查，均不表示 workflow 业务版本。 |

当前为未发布 v1.0 草案修订 3，替代先前未冻结草案；不宣称与 r1/r2 互通。草案试接入必须通过下述装配清单检查，不能仅凭 schema_version=1.0 或 core.v1 接入。正式冻结后，必填字段或执行授权语义变更必须提升 schema major，禁止发布语义不同的同名 1.0。

契约清单：scheduler-engine-v1-contract.manifest.json 固定 format=unilab.scheduler.contract-manifest.v1、protocol、schema_version、release_state=draft、draft_revision=3、files 和 contract_sha256。files 列出本附件除清单自身外每个文件的 SHA-256；文件名仅允许附件内单层 ASCII 名称，不允许绝对路径、目录或 . / ..。语义文件 semantic-rules.md 是本次接口规范的冻结文本，参与摘要；不能只对 Schema 哈希。

摘要算法：先对原始文件字节计算 SHA-256。再取清单中除 contract_sha256 外的全部字段，按 JSON 对象键递归字典序排序、无空格/换行、ASCII 字符、UTF-8 编码得到字节，计算 contract_sha256；示例等价 json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=True)。清单无浮点字段，files 的摘要均为小写 64 位十六进制，数组不参与本清单。宿主部署配置保存已审核清单的准确摘要；不能以插件自报摘要替代该批准值。

插件清单：交付物携带符合 scheduler-engine-v1-manifest.schema.json 的 PluginManifest，含 format、engine_id/engine_version/build_id、contract（schema_version/release_state/draft_revision/contract_sha256）、artifact（kind/path/sha256）和 abi。native 的 abi={major:1,minor:0}；python_bundle 的 abi=null。artifact.path 是交付目录内单层文件名，禁止路径逃逸。示例清单只演示格式，未提供真实动态库，不能作为可加载商业引擎。

装配顺序：① 宿主校验批准的契约包及每个文件摘要；② 校验插件 manifest 并精确匹配批准的修订/契约摘要；③ 校验实际将被载入的动态库或不可变 Python bundle 的字节摘要，固定该载入物避免校验后替换；④ native 检查六符号及 ABI，调用 describe 并核对 manifest 中引擎身份/构建，同时进行原 schema/能力协商；⑤ 通过后才允许 create。Python 适配器也执行前三步及 describe 校验。此机制不增加引擎调用入口。

拒绝规则：manifest 结构缺失/非法为 INVALID_REQUEST；草案修订、契约摘要或协议不匹配为 UNSUPPORTED_SCHEMA；文件/载入物摘要不符、文件缺失或 describe 身份不符为 ENGINE_UNAVAILABLE。均由 adapter 产生、无 create/派发副作用，不自动降级。附件内 Manifest 示例给出预期结果；ABI 和 schema 校验仍独立。SHA-256 只证明与宿主批准内容一致，不替代签名、来源信任或许可证。

正式冻结时发布 release_state=released、draft_revision=null 的新批准清单并重新计算摘要；插件清单必须匹配。发布后的语义变化仍按 major/minor 规则处理，不能通过改 manifest 绕过协议版本升级。

### 13.10.1 旧状态到公共事实的映射基线

| 当前代码事实 | 公共字段映射 | 门禁/结算约束 |
| --- | --- | --- |
| Task pending / running | status=pending / running | 以持久 Task 为主；Edge 运行态用于细化，不覆盖已提交终态。 |
| Task success / succeeded | status=succeeded | 只有必要结算完成且无未决事实才 terminal_settled=true。 |
| Task failed / canceled / timeout | failed / canceled / failed；timeout 在 blockers 中保留 OUTCOME_TIMEOUT | 终态不代表资源已释放。cleanup_status 为 pending/requires_attention、仍有未决命令或占用时 terminal_settled=false。 |
| Task canceling | status=running，control=cancel_requested | normal_progression_allowed=false，terminal_settled=false；等待平台终态与结算，不能当作 canceled。 |
| Edge waiting_for_material、准入受阻 | 保留持久 status；admission=blocked，blockers=WAITING_ADMISSION | 不得把 Task 从视图丢掉，普通推进门禁 false；补料合法后重新投影。 |
| Task paused / dispatch_gate=paused | 保留持久 pending/running；control=paused | normal_progression_allowed=false，仅关闭普通自动推进；指定节点的受限单步按下文条件可执行。旧 paused 状态缺持久基线时，有激活/执行证据映射 running，否则 pending；必须可追溯。 |
| error_waiting / waiting_reconciliation / recovering | Task.control=recovery_hold；保留真实生命周期 | 普通门禁 false；保留 ERROR_WAITING / EXECUTION_UNKNOWN / RECOVERY_REQUIRED 原因。失败的旧 attempt 不等于父 Task 已结束。 |
| execution_mode=switching_to_step | execution_mode=step，control 至少 paused | 转换完成前不授予节点 step_authorized；正常模式、单步和单节点分别 normal/step/single_node。 |
| Node pending / ready | status=pending；ready 仅在真实依赖和门禁允许时 true | ready 是事实投影，不包含引擎是否选择它；人工准入的 ready 不要求已批准。 |
| Node dispatched 或已准入人工待确认 | status=running，保留 InFlight | InFlight.status 按准入/交付证据区分 admitted/submitted/running；manual_phase 按确认记录单独填写。 |
| Node success/succeeded、skipped、canceled、failed/timeout | succeeded、skipped、canceled、failed；timeout 保留原因 | 存在未决执行时 Node.status=uncertain 优先，真实业务终态由 Task 与执行结果保留。 |
| Node ERROR_WAITING | 有已确认失败证据时 status=failed；无终态但存在执行不确定性则 uncertain；尚未准入且仍 pending 的准备等待保持 pending | ready=false，父 Task.control=recovery_hold。禁止无依据把未知执行映射为已确认失败。 |
| start_state NOT_STARTED / START_COMMITTED / 缺失或未知值 | 原样 NOT_STARTED / START_COMMITTED / UNKNOWN | 只有持久开始证据可证明 NOT_STARTED；started_at=null、命令未确认或等待超时均不能单独证明未开始。 |
| executor_kind manual_confirm / material_io / error_manual 来源 | kind=manual，manual_mode=confirm_then_execute / material_io / intervention | 人工确认、物料确认和故障干预权限不同；condition/repeat_until/workflow_command/material_source 分别映射相应 kind。未知类型停止投影，不能降格为 device。 |

授权判定分为“不可越过的约束”和“推进许可分支”，不能把 normal_progression_allowed 与 step_authorized 简单相与。域级 lifecycle=running、execution_gate=open、有效当前 head/Task 范围，以及业务准入、依赖、参数、资源和开始闸门均须满足。Task 终态、cancel_requested、recovery_hold 一律不走普通或单步推进；受控故障处置另走平台入口。Task.control 优先级仍为 recovery_hold > cancel_requested > paused > active；取消证据保留在 InFlight.status=cancel_requested 和原因中。

普通自动分支：Task.control=active、execution_mode=normal 且 normal_progression_allowed=true。受限单步分支：execution_mode=step 或 single_node，Task.control=active 或 paused，且平台已完成模式切换并授予该节点 step_authorized=true；此时可不要求 normal_progression_allowed=true。两个分支均必须通过前述不可越过的约束。无许可的相邻节点不能因同处 task_scope 而推进。

单步许可由平台绑定稳定 Task/Node/attempt_no。start/admit_manual 在 A 事务中把许可一次性绑定到真实 attempt；重复应用只复用该 attempt，不能授权下一节点或重试 attempt。本地控制在其状态提交事务中消费许可；一次授权不能连续展开多个控制节点。控制提交失败则不得提前消费。

已准入人工单步 Job 获批后继续原 attempt，不再领取第二份单步额度；平台以该 attempt 上仍有效的原许可计算 continuation_allowed。即使新准入用的 step_authorized 已消费，也不能仅据此拒绝原 attempt 的合法续行；取消、许可撤销、恢复/故障屏障和人工批准仍实时复验。恢复后的普通续行还必须取得新 head 和 continue_attempt。

字段示例：Task.control=paused、execution_mode=step、normal_progression_allowed=false；目标节点 step_authorized=true、ready=true，可在有效 advance 范围内推进；同 Task 另一个 step_authorized=false 的节点不得推进。恢复屏障下，即使旧 step_authorized 为 true 也不得执行。

映射失败返回 SNAPSHOT_INVALID。所有新准入拥有稳定 attempt_id；旧已存在 attempt 可由持久身份建立一次性映射，但不得补造未发生的开始证据。历史样本缺失 attempt 映射时使用 legacy_import，不猜测与当前 attempt 的关系。

状态格式与计时规则分别兼容：checkpoint.format 决定 opaque 模型能否读取；timing_spec_id/version 决定原始样本的解释；action_revision 只标识动作契约。升级可显式迁移模型/再解释样本，但不得覆盖原始记录或静默把旧模型当空模型。

## 13.11 C ABI 1.0 原生适配档案

本节定义选择 C ABI 路线时可直接实现的 1.0 档案；是否同进程加载或由独立 worker 加载仍待选型。导出六个符号，声明见附件 scheduler-engine-v1.h。四个业务入口接收同样的请求字节并返回完整响应 JSON；实例身份在 JSON body 内，不跨边界传递 Python 对象或 C++/Rust 对象地址。

```c
#ifndef UNILAB_SCHEDULER_ENGINE_V1_H
#define UNILAB_SCHEDULER_ENGINE_V1_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* ABI 1.0: high 16 bits = major, low 16 bits = minor. */
uint32_t uni_sched_abi_version(void);
/* Return 0: valid response JSON (ok may be false). Negative: no response. */
int32_t uni_sched_describe(const uint8_t *req, uint64_t req_len,
                          uint8_t **out, uint64_t *out_len);
int32_t uni_sched_create(const uint8_t *req, uint64_t req_len,
                        uint8_t **out, uint64_t *out_len);
int32_t uni_sched_decide(const uint8_t *req, uint64_t req_len,
                        uint8_t **out, uint64_t *out_len);
int32_t uni_sched_close(const uint8_t *req, uint64_t req_len,
                       uint8_t **out, uint64_t *out_len);
void uni_sched_free_buffer(uint8_t *ptr, uint64_t len);
#ifdef __cplusplus
}
#endif
#endif
```

- uni_sched_abi_version 返回 (major << 16) | minor；1.0 为 0x00010000。只在 ABI major 兼容、宿主支持所需 minor 且六个符号存在时调用其余入口。
- ABI 使用标准 C 调用约定、固定宽度整数和 UTF-8 字节；请求不要求 NUL 结尾，req_len 是实际长度。宿主保证指针及长度有效，并在分配/解析前执行协商的字节上限检查。
- 调用期间请求内存由宿主持有，引擎不得保存其借用指针。引擎分配响应内存；宿主解析或复制完后，用同一已加载库的 uni_sched_free_buffer(ptr,len) 精确释放一次。传给 free_buffer 的长度必须等于该响应的 out_len，不使用宿主 free。
- free_buffer(NULL,0) 为无操作；其他值必须是本库仍未释放的原始指针与长度。close 不提前释放已经返回给调用方的响应缓冲区；所有缓冲区释放且调用结束后才能卸载库。
- 四入口返回 0 表示获得合法响应信封，仍须检查 ok。返回 -1 表示原生调用参数无效，-2 表示无法分配响应，-3 表示无法形成响应的内部故障；负值时 out=NULL、out_len=0。可形成结构化业务错误时应返回 0 与 ok=false。
- 不能让 C++ 异常或可捕获 Rust panic 穿过 ABI；转换为 ENGINE_INTERNAL 或 -3。进程崩溃、abort、非法内存访问和无限挂起不能依靠普通返回码隔离；独立 worker 方案由宿主转为 ENGINE_UNAVAILABLE/DEADLINE_EXCEEDED。
- 同一实例串行；describe 与不同实例可并行；free_buffer 必须可处理不同响应的并发释放。ABI minor 只允许向后兼容新增符号，不改变已有签名与所有权规则。

## 13.12 开发验收、附件与保留决策

- 结构契约：用附件 JSON Schema 验证四组请求/响应及错误响应；覆盖必填缺失、错误枚举、负 attempt、非法 null、未知字段与响应成功/失败互斥。
- 语义契约：校验节点/候选/资源引用、连续反馈游标、state 摘要、同一 attempt 唯一派发、Task 范围与门禁、epoch/revision 过期、forecast 能力和完整快照限制。JSON Schema 通过不代表这些关系自动成立。
- 恢复契约：按 D/A/S 提交边界、部分落实和结果已提交窗口注入中断，核对原 command/attempt、资源/容量、checkpoint 和反馈位置；覆盖旧 head 不授权、原 NOT_STARTED 续行、未知开始不重复执行、三类故障域、日志缺口及模型重建。
- 兼容契约：载入前验证批准的 contract manifest、插件声明及实际载入物摘要；拒绝旧草案、篡改和身份不符；继续验证共同 schema、能力、状态格式、配置及回退。四个入口和六符号不增加。
- 原生契约：校验 ABI 版本与六符号、C/C++ 声明、响应所有权、重复 close、实例并发规则以及故障隔离；同进程测试不替代 worker 崩溃隔离测试。

本轮已验证：两份 JSON Schema 自身合法性；68 个消息示例的结构预期；33 个快照的选定引用/控制区域/容量/门禁关系；17 组配对的身份、游标和派发许可；9 个合成装配元数据案例；2 个单事件批次追赶模型；C/C++ 头文件语法。validation-report.json 记录范围与限制。这些是协议级静态/模型检查，不是生产调度测试；14 组时序场景仍待 UniLabOS 集成及故障注入验收。

仍保留的决策：基础队列产品默认策略与人工干预边界、Rust/求解器的绑定方式和目标平台、同进程或独立 worker、性能阈值。它们不阻止依据本节实现公共数据类型、宿主校验/应用层和测试替身；正式交付前必须关闭相应决策。

当前开发附件为 scheduler-engine-v1-contract-r3.zip（未发布草案修订 3），替代 r2。valid 仅标记消息结构预期；expected_semantic_error 标记关系反例，semantic_scope=pair_only 表示错误仅由指定响应配对触发。pairs 定义配对；manifest_cases 使用合成文件/批准摘要验证装配规则，不实际加载引擎；scenarios 定义运行时验收。插件 manifest 示例中的摘要和身份均为合成值，不能用于实际装配。

| 附件文件 | 用途 |
| --- | --- |
| scheduler-engine-v1.schema.json | 64 个核心类型；字段描述与 r3 语义一致，不新增调用入口。 |
| scheduler-engine-v1.examples.json | 68 个消息示例、17 组配对、9 个装配元数据案例、14 组运行时场景。 |
| scheduler-engine-v1.h | 六个 C ABI 1.0 导出声明；签名与所有权不变。 |
| scheduler-engine-v1-manifest.schema.json | ContractManifest 与 PluginManifest 两类装配清单结构。 |
| scheduler-engine-plugin-manifest.example.json | 合成的 native 插件清单格式示例；摘要须替换为实际批准契约与载入物摘要。 |
| semantic-rules.md | 本修订第 13 章冻结语义，参与契约摘要，避免只有 Schema 一致而行为不同。 |
| validation-report.json | 本轮协议级检查结果与未覆盖的运行时验收边界。 |
| scheduler-engine-v1-contract.manifest.json | 包内七个文件摘要与契约总摘要；清单不对自身递归哈希。 |
