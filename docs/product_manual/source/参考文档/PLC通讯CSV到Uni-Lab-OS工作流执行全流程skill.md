# PLC 通讯 CSV 到 Uni-Lab-OS 工作流执行全流程

## 1. 文档目的与适用范围

本文规定如何根据一份新的 PLC 上位通讯变量 CSV，独立创建 Uni-Lab-OS 设备包，并完成设备、设备动作、库位、物料、试剂、实验操作、实验工作流和实验任务的设计、发布、预检、执行与验收。

本文中的手册示例只用于解释对象关系和操作方法，不是实际项目配置。CSV 是变量和类型数据源，不能直接当作设备动作或安全时序执行。无法从变量名、注释和协议资料确认含义的变量必须标记为 `unresolved` 或 `auxiliary`，不得凭地址猜测触发、完成、复位、急停或联锁逻辑。

对象之间的关系是：

```text
PLC 通讯 CSV
    ↓
设备类型与运行实例
    ↓
设备动作合同
    ↓
实验操作（experiment_operation）
    ↓
父工作流（normal）
    ↓
实验任务
```

库位、物料和试剂是工作流执行时的资源。设备动作负责能力和通讯映射，实验操作负责可复用的工艺目标，父工作流负责输入、资源边界和任务入口。

## 2. 输入、输出和命名规则

开始前设置四个路径或模式变量：

```text
PACKAGE_ROOT=<新设备包的绝对路径>
CSV_PATH=<用户提供的 CSV 绝对路径>
OS_ROOT=<Uni-Lab-OS 源码或安装环境路径>
MODE=dry-run 或 real
```

新设备包必须使用新的目录、新的 Python 命名空间、新的设备实例 ID 和新的 workflow/node UUID。不得复制其他包的运行时发布缓存、合同 UUID、任务 UUID、资源实例或工作流快照。目录名可以采用项目要求的大小写形式，Python 包名建议使用小写下划线，例如：

```text
目录：PLC_CSV_Workflow_Demo/
Python 包：plc_csv_workflow_demo/
```

设备和资源 ID 只使用字母、数字和下划线；中文名称放在 `displayname`、`name` 和描述字段。动作参数使用业务单位，例如 g、mL、μL、°C、s；PLC 原始地址和编码转换放在驱动内部。

## 3. 推荐的设备包目录

```text
<PACKAGE_ROOT>/
├── README.md
├── SKILL.md
├── acceptance_report.md
├── package.yaml
├── pyproject.toml
├── requirements.txt
├── .gitignore
├── communications/
│   ├── source_upper_communication_variables.csv
│   └── upper_communication_variables.csv
├── config/
│   ├── csv_check.yaml
│   ├── device.yaml
│   ├── actions.yaml
│   ├── sites.yaml
│   ├── materials.yaml
│   ├── reagents.yaml
│   ├── experiment_operations.yaml
│   ├── workflows/*.yaml
│   ├── publication_record.yaml
│   ├── preflight_report.yaml
│   ├── runtime_check.yaml
│   ├── execution_summary.json
│   └── tasks/*.json
├── deployment/
│   ├── local_config.py
│   └── graphs/{dry-run.json,simulation.json}
├── <python_package>/
│   ├── __init__.py
│   ├── devices/{__init__.py,_base.py,*.py}
│   ├── resources/{__init__.py,carriers.py,decks.py,materials.py}
│   ├── experiment_operations/{__init__.py,*.py}
│   └── workflows/{__init__.py,*.py}
├── scripts/{inspect_csv.py,validate_package.py,run_demo.py}
└── tests/{test_package_structure.py,runtime_smoke.py}
```

`package.yaml` 应登记四类源码入口：至少两个实验操作和两个父工作流。若项目需要更多操作，可以增加入口，但每个入口必须有唯一 UUID、唯一节点 UUID 和独立的发布记录。

## 4. CSV 复制与检查

### 4.1 保留原始字节

原始 CSV 应直接复制到：

```text
<PACKAGE_ROOT>/communications/source_upper_communication_variables.csv
```

复制后记录文件大小、SHA-256、检测到的编码、表头和数据行数。源文件副本必须与用户附件逐字节一致；`upper_communication_variables.csv` 可以作为 UTF-8 的业务映射副本，但不能替代原始副本。

### 4.2 检查项目

至少检查：

- 能否使用实际编码解码，常见 PLC 表为 GB18030；
- 表头是否包含变量名、数据类型、初始值、注释、软元件地址等列；
- 变量名是否重复，数据类型是否缺失；
- 是否存在任务号、位置、工艺参数和返回状态变量；
- 命令变量、状态变量、结果变量和辅助位置变量能否按业务根分组；
- 地址是否重复，数组下标是否连续，保留变量是否被错误纳入动作；
- 触发、执行中、成功、失败、复位和安全联锁变量是否有协议依据。

建议使用脚本生成检查快照：

```bash
python scripts/inspect_csv.py --csv "$CSV_PATH"
```

`config/csv_check.yaml` 至少记录：

```yaml
source: /absolute/path/to/source.csv
encoding: GB18030
sha256: <hash>
data_row_count: <actual count>
groups:
  - name: <CSV 根分组>
    status: mapped | auxiliary | unresolved
    purpose: <已确认的业务含义>
warnings:
  - <无法由 CSV 确认的协议或安全边界>
conclusion: passed | passed_with_review | blocked
```

只有 CSV 检查为 `passed`，或所有 `passed_with_review` 项已经得到业务确认，才能继续生成动作。变量地址只能作为映射证据，不能单凭地址推断动作。

## 5. 按业务能力拆分设备

设备拆分依据是可独立调度、独立占用和独立报错的业务能力，而不是简单按照 CSV 的每一个结构体创建设备。一个可行的拆分是：

| 能力 | 典型通讯分组 | 对外设备类别 | 说明 |
|---|---|---|---|
| 物料搬运 | 上料、下料、工位进出、成品转运 | `robot` / `material_transfer` | 负责物料位置变化和取放编号 |
| 加样或加珠 | 加样、加珠 | `workstation` / `process_station` | 负责重量、精度、数量等工艺参数 |
| 烧结或温控 | 烧结上料、焦耳热数据 | `workstation` / `temperature` | 负责温度、保温时间和工位状态 |
| 状态监视 | PLC 返回数据 | `sensor` / `virtual` | 只读状态，不伪造未知控制动作 |

每个设备类型使用 `@device`，每个设备动作使用 `@action`，状态属性使用 `@property` 与 `@topic_config`。装饰器必须直接从 `unilabos.registry.decorators` 导入并使用原名。设备实例 ID 必须同时出现在资源图、`device()` 调用、设备配置和运行记录中。

共享通讯基类可以集中连接、读写、超时和 dry-run 状态，但业务动作必须留在职责明确的设备类中。辅助方法使用私有名称或 `@not_action`，避免 Registry 自动生成不需要的动作。

## 6. 设备动作合同

每个动作都必须写清楚以下内容：

1. 业务目的和对应的 CSV 命令根；
2. 输入参数、单位、范围、默认值和必填性；
3. 物料或设备 Slot 参数；
4. 输出字段，至少包含 `success`、`message`、状态码或业务结果；
5. 任务号变量、参数变量、状态变量、触发和复位变量映射；
6. 前置条件、成功条件、失败条件、超时和 unknown 语义；
7. 资源锁、幂等性和重复任务号处理；
8. dry-run 与真实驱动的行为差异。

动作中的变量键必须使用 CSV 中确认的完整变量名。例如：

```python
payload = {
    "上料流程通讯数据2.任务号": task_no,
    "上料流程通讯数据2.取放编号[0].取编号": pick_position,
    "上料流程通讯数据2.取放编号[0].放编号": place_position,
}
```

不要用 Python 动作名拼接出不存在的变量，例如把 `loading` 自动拼成 `loading.任务号`。`config/actions.yaml` 应逐字段记录业务参数到 CSV 变量的映射：

```yaml
actions:
  - id: run_loading
    device: <transport_device_id>
    command_group: 上料流程通讯数据2
    variable_mapping:
      task_no: 上料流程通讯数据2.任务号
      pick_place[0]: 上料流程通讯数据2.取放编号[0].取编号
      pick_place[1]: 上料流程通讯数据2.取放编号[0].放编号
      pick_place[2..19]: 上料流程通讯数据2.取放编号[1..9].取编号/放编号（按偶数索引成对展开）
      status_code: PLC返回数据.上料任务状态
    success_condition: status_code == 2
    failure_condition:
      - status_code == 3
      - timeout
      - source_or_target_site_invalid
```

参数必须与动作调用闭合：本包样品上料操作的 `source_position` 和 `target_position` 是加样动作的料架/球磨罐位置；`run_loading` 的 PLC 取放数组第 0 对采用演示路径 `1 -> 2`，对应 `取放编号[0].取编号/放编号`。如果现场要改变上料路径，必须同步修改动作输入、CSV 映射、任务输入和预检证据，不能只改库位名称。

设备动作的 Registry 校验和直接调用 smoke test 都必须通过。真实模式只有在实现 `_read()`、`_write()`、触发、完成、异常、复位和断线恢复协议后才能开启。

## 7. 库位、物料、试剂和资源图

资源建模顺序为：

```text
资源模板 → Deck/载架/工艺位 → 物料或试剂实例 → 初始占用
```

### 7.1 库位

每个库位必须声明：owner、稳定名称、允许物料类型、容量、坐标、取放权限、是否独占和初始占用。设备下的工艺位可采用 `logical_mount=true`；机器人夹爪使用隐藏 `gripper` site，并标记：

```yaml
meta_data:
  unilab:
    resource_role: robot.gripper
```

目标工艺位和成品位在任务开始前必须为空；源库位必须有正确类型的物料或试剂；同一个实例不得占用多个库位。

### 7.2 物料

物料实例至少包含：唯一 ID、条码、模板 ID、状态、当前位置和可用库位类型。容器尺寸单位为 mm，容量按 PyLabRobot 约定使用 μL；载架必须设置 `num_items_x/y/z`，Deck 的构造函数必须支持 `setup=False`。

### 7.3 试剂

试剂实例还要记录：物理状态、批次或有效期（业务需要时）、当前数量、单位、最低库存、供应信息和消耗规则。液体使用 mL/L，固体使用 g/kg，消耗必须在任务结果中留痕。只有动作成功且协议确认完成时才能扣减库存。

### 7.4 图文件

`deployment/graphs/dry-run.json` 和 `simulation.json` 应来自同一代资源图。图中要能找到所有设备实例、Deck、库位载架、工艺位和初始物料。建议在校验脚本中检查：

- 父节点存在且层级闭合；
- 设备数量与职责拆分一致；
- 库位 `content_type` 与物料模板匹配；
- 物料实例唯一占位；
- 目标位为空、源位占用正确；
- 设备的 pose 尺寸非零，兄弟节点无明显碰撞。

## 8. 创建实验操作（硬门禁）

实验操作是可以复用的子工作流，源码必须显式声明：

```python
@workflow(
    workflow_uuid="<new-operation-uuid>",
    displayname="<实验操作名称>",
    description="<业务目标>",
    workflow_type="experiment_operation",
)
def <operation_name>(...):
    # unilab:node_uuid=<new-node-uuid>
    result = device_instance.run_action(...)
    return result
```

每个实验操作必须满足：

- `workflow_type` 为 `experiment_operation`；
- `package.yaml` 的 source 与文件实际存在且 UUID 一致；
- 至少包含一个真实设备动作节点，动作节点数为零时禁止发布；
- 所有设备引用来自当前新包并存在于资源图；
- Material/Resource Slot 能与库位和模板闭合；
- 节点 UUID 唯一；
- 成功、失败、超时、unknown 和锁保留策略明确。

建议在源码、编译后的 graph 和服务端 authoring API 做三重检查。`config/experiment_operations.yaml` 应列出操作的动作节点、设备实例、资源、输入、输出、通讯分组和成功条件。

## 9. 创建父工作流（硬门禁）

父工作流使用 `workflow_type=normal`（可省略并采用默认值，但配置快照应明确记录），至少包含一个对子实验操作的 composite 调用。父工作流必须引用本包内已发布的操作，不能只复制操作源码，也不能从其他包导入对象。

父工作流配置应包含：

- child operation 的 workflow UUID；
- 最新已发布 contract UUID、revision 和 version；
- 工作流输入是否覆盖 child 必填参数；
- 资源冲突范围和并发策略；
- 运行前预检项目；
- success、failure、pause、cancel 的状态处理；
- 结果、状态码、库存消耗和最终库位记录。

推荐在 `deployment/local_config.py` 中关闭未发布组合兼容模式：

```python
class BasicConfig:
    allow_unpublished_composite_sources = False
```

### 9.1 发布顺序

严格按以下顺序操作：

```text
加载并检查四个源码
  ↓
发布所有 experiment_operation
  ↓
重新读取父工作流源码，保存并 apply
  ↓
验证 child_workflow_uuid、contract_uuid、revision
  ↓
发布 normal 父工作流
```

严格模式下，新工作区冷启动时父工作流出现 `draft_invalid` 或 `composite_child_not_found` 是预期门禁，不能通过重复发布旧 revision 绕过。子操作发布后必须重新保存并 apply 父源码，再确认 authoring `state=applied` 且 diagnostics 为空。

## 10. 离线检查、构建和启动

先在包根目录执行离线检查：

```bash
cd "$PACKAGE_ROOT"
python scripts/inspect_csv.py --csv "$CSV_PATH"
python scripts/validate_package.py
python -m compileall -q <python_package> scripts tests
conda run -n unilab unilab --check_mode --devices ./<python_package> --external_devices_only
conda run -n unilab unilab package inspect --path .
conda run -n unilab unilab package build --path . --out /private/tmp/<package>-build
```

构建产物不应携带 `.unilabos`、`runtime-results`、`workflow_publications.json` 或 `workflow_publications/`。这些内容是目标工作区运行态，不能成为可移植包的输入。

### 10.1 启动 dry-run 工作区

没有真实 PLC 协议时使用 dry-run：

```bash
conda run -n unilab unilab workspace start \
  --workspace "$PACKAGE_ROOT" \
  --graph deployment/graphs/dry-run.json \
  --runtime-mode dry-run \
  --wait 300 --json

conda run -n unilab unilab workspace status \
  --workspace "$PACKAGE_ROOT" --json
```

端口必须从 `workspace status --json` 动态读取，不能写死历史端口。至少确认 backend、edge、Workspace Host 为 `ready`，工作流加载数为总数，Registry 设备和资源数符合设计。

如果使用 PLC-Sim，必须先确认模拟器的变量文件、协议、端口和握手模型与 CSV 一致，再启动模拟器和 Uni-Lab OS。仅有变量 CSV 不能证明真实 PLC 握手可用。

## 11. 发布 API 与动态检查

下面的 `BASE` 是 workspace status 返回的 backend 地址加 `/api/v1`。本地 dry-run 通常不需要云端认证；云端环境按平台要求提供 `Authorization: Lab <token>`。

### 11.1 检查和发布

对每个 workflow 依次调用：

```text
GET  {BASE}/workflows/{workflow_uuid}
GET  {BASE}/workflows/{workflow_uuid}/authoring
POST {BASE}/workflows/{workflow_uuid}/publications
     body: {"revision": <authoring.workflow_revision>}
```

发布前必须满足：authoring `state=applied`、无 diagnostics、源码 revision 与预期一致。发布后保存 `workflow_uuid`、`status`、`workflow_revision`、`contract_uuid`、contract digest 和 source hash。

父工作流发布前，重新执行：

```text
PUT  {BASE}/workflows/{parent_uuid}/authoring/draft
POST {BASE}/workflows/{parent_uuid}/authoring/apply
GET  {BASE}/workflows/{parent_uuid}/authoring
```

检查父合同 metadata 中的 `child_workflow_uuid`、`contract_uuid` 和 `child_workflow_revision` 是否对应刚发布的 child。

### 11.2 动态四项硬门禁

服务端检查必须证明：

1. 每个 child `workflow_type=experiment_operation`，设备动作节点数至少为 1；
2. 每个 parent `workflow_type=normal`，至少有一个 composite child；
3. 每个 parent 的 child contract 状态为已发布且 revision 与发布记录一致；
4. 四个 workflow 的 authoring 状态为 `applied`、发布状态为 `published`、diagnostics 为空。

## 12. 物料准备、预检和任务执行

根据父工作流的资源合同创建新的物料和试剂实例，并将它们放入兼容且唯一的库位。任务开始前保存 occupancy 快照：源位占用、目标位空闲、工艺位空闲、试剂数量不低于最低库存。

预检和任务接口建议如下：

```text
POST /api/v1/workflows/{workflow_uuid}/run-preflight
     {"run_mode":"normal", "input":{...}}

POST /api/v1/workflow-tasks
     {"workflow_uuid":"...", "run_mode":"normal",
      "priority":"normal", "input":{...},
      "inventory_bindings":[], "description":"..."}

GET /api/v1/workflow-tasks/{task_uuid}
GET /api/v1/workflow-tasks/{task_uuid}/jobs
GET /api/v1/workflow-tasks/{task_uuid}/events
GET /api/v1/workflow-tasks/{task_uuid}/inventory-consumptions
```

只有同时满足以下条件才能创建任务：

- `preflight.status=runnable_now`；
- `can_run=true`；
- `blocking_items` 或 `blocking_reasons` 为空；
- 通讯服务和设备状态正常；
- child operation 已发布；
- 所有物料、试剂和目标库位可用；
- 没有同一设备、物料、试剂或独占库位的冲突任务。

任务执行时记录每个 Job 的 action、设备实例、状态、返回码、开始/结束时间、PLC 状态见证、异常和重试信息。完成后重新读取资源图，确认最终库位和试剂消耗。任务结果为 `failed` 或 `unknown` 时暂停并保留锁，先读取事件和实际物料位置，再决定人工处理或受控重试；禁止盲目重复提交。

## 13. 证据文件和验收标准

建议将以下内容写入包内：

| 文件 | 必须证明的内容 |
|---|---|
| `config/csv_check.yaml` | CSV 编码、哈希、行数、分组、未决项 |
| `config/device.yaml` | 设备类型、实例、类别、通讯模式和安全边界 |
| `config/actions.yaml` | 动作输入输出、CSV 映射、前置/成功/失败条件 |
| `config/sites.yaml` | 库位类型、容量、权限、独占和初始占用 |
| `config/materials.yaml` | 物料模板和实例、条码、当前位置 |
| `config/reagents.yaml` | 试剂批次、单位、数量、最低库存和消耗规则 |
| `config/experiment_operations.yaml` | child 类型、设备动作节点、资源和结果 |
| `config/workflows/*.yaml` | parent 类型、已发布 child 依赖、预检和并发策略 |
| `config/publication_record.yaml` | 发布顺序、合同 UUID、revision、digest 和 source hash |
| `config/preflight_report.yaml` | 每个父工作流的预检检查和阻断项 |
| `config/tasks/*.json` | 输入、资源、每个步骤、任务状态和结果来源 |
| `config/execution_summary.json` | 初始/最终 occupancy、发布结果、任务 UUID、状态和消耗 |
| `config/runtime_check.yaml` | 服务、Registry、加载数、运行模式和验证时间 |
| `acceptance_report.md` | 对象清单、验证命令、结果、限制和真实 PLC 边界 |

验收脚本至少检查：

```bash
python scripts/validate_package.py
python -m unittest discover -s tests -v
PYTHONPATH=. conda run -n unilab python tests/runtime_smoke.py
```

还应执行：

- Registry `--check_mode --external_devices_only`；
- Python import/compile；
- package inspect 和 wheel build；
- dry-run 设备动作 smoke test；
- 动态四项发布门禁；
- 两个父工作流的 preflight 和任务终态；
- `rg` 检查没有其他包命名空间、旧 UUID、旧任务快照或旧发布缓存；
- 解包 wheel，确认不含运行时发布缓存和 `.unilabos`。

## 14. 异常处理和调整流程

| 阶段 | 常见问题 | 处理方式 |
|---|---|---|
| CSV 检查 | 编码错误、变量重复、分组不清 | 停止生成动作，修正或补充协议说明 |
| Registry | 类型注解不完整、动作被自动暴露 | 补齐类型和 docstring，私有化辅助方法 |
| 图加载 | 库位类型不匹配、目标位已占用 | 修正资源模板或恢复库存后重新预检 |
| child 发布 | authoring 未 apply、动作数为零 | 修正源码并重新编译，禁止发布无动作操作 |
| parent 发布 | `composite_child_not_found`、stale contract | 先发布 child，再保存并 apply parent |
| preflight | 设备忙、资源冲突、试剂不足 | 输出阻断项，调整库位/任务排程后重新预检 |
| 执行中 | failed/unknown/timeout | 保留锁，读取事件和真实位置，完成异常处置后再重试 |
| dry-run 与现场差异 | 模拟成功但现场握手失败 | 按现场协议补齐读写和联锁测试，不能把 dry-run 当作硬件验收 |

## 15. Dry-run 与真实 PLC 的边界

dry-run 可以验证 Uni-Lab OS 的 Registry、动作 schema、工作流编译、child/parent 发布合同、资源锁、预检、调度、任务状态和库位结算。它不能证明真实 PLC 的电气连接、握手触发、执行中状态、完成/失败码、复位、急停、舱门、超时、断线恢复或现场物料保护。

真实 PLC 接入前必须：

1. 由设备或 PLC 负责人确认每个变量的读写方向和生命周期；
2. 明确任务号分配、触发、执行中、完成、异常、复位和重复任务号策略；
3. 配置通信协议、地址、端口、超时和断线恢复；
4. 在无物料风险的环境完成单动作、失败注入、急停和恢复测试；
5. 通过现场安全评审后再将 `MODE` 切换为 `real`。

最终报告必须明确区分“Uni-Lab OS dry-run 调度验证”和“真实 PLC 协议验收”。
