# PLC 通讯变量表到 Uni-Lab OS

## 设备包、实验操作、工作流与任务执行通用引导操作说明

> 本页由《PLC通讯CSV到 Uni-Lab OS 通用引导操作说明》整理而来。示例名称、编号、参数和地址均为可替换占位。

PLC 通讯变量表到 Uni-Lab OS

设备包、实验操作、工作流与任务执行通用引导操作说明

适用于：把任意 PLC 通讯变量表整理成可检查、可发布、可执行的 Uni-Lab OS 交付

| 文档性质 | 可迁移的 step-by-step 引导；示例名称、编号、参数和地址均为可替换占位 |
| --- | --- |
| 输入 | PLC 通讯变量表（CSV；编码、列结构和变量名以实际附件为准） |
| 输出 | 设备、设备动作、库位、物料/试剂、实验操作、父工作流、任务和运行证据 |
| 编制日期 | 2026-09-13 |

阅读方式：用户要求与参考资料的边界

本说明只描述可迁移的方法。文中的“示例设备”“示例库位”“示例任务号”和“示例参数”用于说明字段关系，执行时必须换成目标实验室的实际值。

用户提出的目标是：读取 PLC 通讯变量表，按功能拆分设备和动作，创建资源与库存，建立至少含一个设备动作的实验操作，再建立至少调用一个已发布实验操作的父工作流，完成发布、预检、任务执行和结果记录。

外部资料只用于补充产品安装、PLC-Sim 和 Demo Lab 的界面或概念背景。若在线资料与目标服务版本不一致，以目标服务实际返回的 schema、状态和诊断信息为准。

| 占位符约定：<包工作区>、<CSV源>、<API_BASE>、<设备类型 ID>、<实例 UUID>、<工作流 UUID>、<任务 UUID>、<动态端口>。尖括号中的内容不是固定值。 |
| --- |

### 在线参考资料

需要引用安装或示例时，请直接打开下列地址：

PLC-Sim 产品手册：[https://github.com/raoyi971102-gif/Uni-Lab-Sim/tree/main/PLC-Sim/docs/product_manual](https://github.com/raoyi971102-gif/Uni-Lab-Sim/tree/main/PLC-Sim/docs/product_manual)

Uni-Lab OS 安装说明：[http://115.190.137.109:30184/unilabos-installation.html](http://115.190.137.109:30184/unilabos-installation.html)

Demo Lab 案例：[http://115.190.137.109:30184/demo-lab.html](http://115.190.137.109:30184/demo-lab.html)

链接属于外部参考；本手册不复制其中的本机路径、固定端口或版本专属内容。

## 一、先建立对象关系

建议先画出对象关系，再写代码或在控制台录入对象。关系闭合后，发布和任务预检才有明确的失败位置。

| 层级 | 对象 | 必须回答的问题 | 通过标志 |
| --- | --- | --- | --- |
| 1 | 设备类型 / 设备实例 | 哪个物理单元负责这组变量？实例如何唯一识别？ | 类型可注册、实例可加载、状态可读取 |
| 2 | 设备动作 | 一次动作写哪些变量、等哪些返回状态、如何报告结果？ | 参数有单位，状态机可判定成功/失败/超时 |
| 3 | 资源与库位 | 动作取放什么资源，资源当前在哪个库位？ | 库位类型、容量、占用关系闭合 |
| 4 | 物料与试剂 | 条码、数量、容器和最低库存是什么？ | 可查询、可摆放、可被预检锁定 |
| 5 | 实验操作 | 哪些资源输入经过哪些设备动作得到什么输出？ | 类型为 experiment_operation，至少一个设备动作节点 |
| 6 | 父工作流 | 如何组合已发布的实验操作？ | 类型为 normal，引用已发布 child 发布合同 |
| 7 | 实验任务 | 用哪一个已发布父工作流、哪批物料、哪些参数执行？ | 预检通过，任务和作业终态可查询 |

推荐的闭环：变量表 → 变量分组 → 设备动作 → 资源/库位 → 实验操作 → 发布 → 父工作流 → 发布 → 摆放与预检 → 任务 → 证据。

## 二、检查 PLC 通讯变量表

### 2.1 保留原始字节并确认编码

把用户提供的 CSV 作为只读输入，先记录文件大小、行数、编码和校验值；不要先用表格软件另存，以免改变编码、引号或换行。

优先尝试 GB18030，再按实际结果处理 UTF-8 with BOM 或其他编码。解析失败时停止后续建模，先解决编码问题。

检查“变量名”是否唯一；检查数据类型、初始值、掉电保持、网络公开、注释和软元件地址是否成列对齐。

### CSV 检查逻辑（伪代码）

```text
# 仅示意：将 <CSV源>、<编码> 和 <输出摘要> 替换为当前环境值
```

read_csv(<CSV源>, encoding=<编码>)

assert required_columns <= columns

assert variable_name_is_unique(rows)

write_summary(<输出摘要>, row_count, encoding, sha256, duplicate_names)

| 检查项 | 建议规则 | 失败处理 |
| --- | --- | --- |
| 编码 | 能完整读取中文变量名，不能出现替换字符 | 保留原始输入，换编码重试 |
| 变量名 | 顶层结构体和叶子变量都可定位 | 报告重复或空名称，停止建模 |
| 类型 | INT、REAL、STRING、结构体/数组与 PLC 定义一致 | 建立类型映射表后再写动作 |
| 地址 | 地址连续性与数组步长符合 PLC 规范 | 让协议负责人确认，不自行猜测 |
| 状态 | 每个写入动作都有对应返回状态或错误信号 | 补充状态机或标记为不可执行 |

### 2.2 按功能分组，不按每一行机械拆分

用顶层结构体、动作语义和返回状态共同分组。一个动作通常由“任务号 + 参数数组/位置 + 触发约定 + 返回状态”组成。下表是从通讯表抽象出的示例分组；名称用于帮助读者定位，不限制实际命名。

| 变量表分组示例 | 动作语义 | 建议归属 | 关键返回字段 |
| --- | --- | --- | --- |
| 上料流程通讯数据2 | 把托盘或样品从源位搬到目标位 | 物料搬运机器人 | PLC返回数据.上料任务状态 |
| 加样流程通讯数据3 | 按重量和精度向样品加入试剂/粉体 | 加样工站 | PLC返回数据.加样加球磨任务状态、加样重量、加样结果 |
| 烧结上料流程通讯数据4 | 把坩埚送入烧结位置并写入工艺参数 | 烧结工站 | PLC返回数据.烧结上料任务状态、马弗炉状态 |
| 声共振上料流程通讯数据8 | 把托盘送入声共振设备并执行参数段 | 物料搬运机器人或声共振工站 | PLC返回数据.声共振上料任务状态 |
| 声共振下料流程通讯数据9 | 把托盘从声共振设备取出 | 物料搬运机器人或声共振工站 | PLC返回数据.声共振下料任务状态 |
| 烧结下料流程通讯数据5 | 把完成处理的坩埚送到结果位 | 物料搬运机器人 | PLC返回数据.烧结下料任务状态 |

如果一组变量既包含搬运又包含工艺设定，可拆成一个动作的两个阶段，或拆成两个设备动作；选择依据是现场是否有独立的资源占用、状态和故障恢复边界。

## 三、设计设备和设备动作

### 3.1 设备划分原则

按可独立调度、可独立占用、可独立报错的物理单元划分设备。

设备动作对应一个可观察的业务结果；动作输入应包含资源、位置、工艺参数和任务号。

动作内部统一经历：校验参数 → 写入 PLC → 轮询状态 → 读取结果 → 更新资源/库存 → 返回公开结果。

真实 PLC 与 dry-run 使用同一动作接口；只在驱动层切换，不在工作流里写两套逻辑。

### 3.2 设备清单参考示例

| 示例设备 | 类别 | 动作示例 | 占用资源 | 最低验收 |
| --- | --- | --- | --- | --- |
| 物料搬运机器人 | robot / material_transfer | run_loading、run_resonance_load、run_resonance_unload、run_unloading | 托盘、坩埚、目标库位 | 返回资源与目标库位，状态最终为完成 |
| 加样工站 | process_station / dosing | run_dosing | 样品托盘、试剂瓶、粉体瓶、球磨珠容器 | 重量在精度范围内，消耗量有记录 |
| 烧结工站 | process_station / sintering | run_sintering | 坩埚、工艺位置、烧结腔位 | 温度/时间参数被确认，炉状态完成 |

### 3.3 动作参数和 CSV 映射示例

| 动作 | 输入参数示例 | 写入变量示例 | 成功判定 |
| --- | --- | --- | --- |
| run_loading | task_no、pick_place=[1,2] | 上料流程通讯数据2.任务号；上料流程通讯数据2.取放编号[0].取编号/放编号 | 上料任务状态从 0 → 1 → 2 |
| run_dosing | task_no、source_position、dose_g=12.5、tolerance_g=0.5、bead_count=10 | 加样流程通讯数据3.任务号；加样数据[0].加样重量；加样数据[0].精度；球磨珠数量 | 加样加球磨任务状态=2 且加样结果=1 |
| run_sintering | task_no、temperature_c=850、hold_s=600 | 烧结上料流程通讯数据4.干锅上料数据[0].工艺温度1/工艺时间1；焦耳热数据.恒温温度设置/恒温保温时间设置 | 烧结上料任务状态=2，炉状态为完成 |
| run_resonance_load | task_no、acceleration_g=2、frequency_hz=1000、time_s=30 | 声共振上料流程通讯数据8.加速度1/频率1/时间1 | 声共振上料任务状态=2 |
| run_resonance_unload | task_no、source_position、target_position | 声共振下料流程通讯数据9.取托盘位置/放托盘位置 | 声共振下料任务状态=2 |
| run_unloading | task_no、pick_position、place_position、graphite_boat_type | 烧结下料流程通讯数据5.取放编号[0].取料位置/放料位置；石墨舟类型 | 烧结下料任务状态=2 |

| 上表的 850 °C、600 s、12.5 g 等是演示参数。发布前要按工艺规程设置范围、单位、精度和上限，并把越界检查放在动作入口。 |
| --- |

## 四、创建通用设备包结构

在包工作区中创建以下“职责区域”。区域可以由目录、模块或平台对象承载，名称由项目规范决定；本手册不要求固定文件名。

| 职责区域 | 写入内容 | 依赖关系 | 检查点 |
| --- | --- | --- | --- |
| 包元数据 | 包标识、版本、依赖和启动拓扑 | 引用设备/资源/工作流的公开 ID | 标识唯一，版本可追溯 |
| 设备声明 | 设备类型、实例、通信配置、动作和状态映射 | 变量映射、驱动基类 | 导入不触发真实动作 |
| 资源声明 | 托盘、料架、容器、工艺腔位和可用库位 | 库位模板和容量 | 父子层级无环，站点索引唯一 |
| 实验操作定义 | 资源输入、设备动作节点、输出和节点 UUID | 已注册设备与资源 | 类型为 experiment_operation |
| 父工作流定义 | 调用实验操作、参数传递和结果汇总 | 已发布 child 发布合同 | 类型为 normal |
| 验证与证据 | 静态检查、运行预检、任务事件和库存快照 | 目标服务 API | 证据来自本轮运行 |

### 设备和动作声明（示意代码）

```python
@device(
id="<transport_device_type>",
category=["custom", "robot", "material_transfer"],
displayname="示例物料搬运机器人",
)
class TransferRobot(PLCFlowDevice):
@action(displayname="托盘上料")
def run_loading(self, task_no: int, pick_place: list[int],
resource: ResourceSlot, target_site: str):
payload = {
"上料流程通讯数据2.任务号": task_no,
"上料流程通讯数据2.取放编号[0].取编号": pick_place[0],
"上料流程通讯数据2.取放编号[0].放编号": pick_place[1],
}
return self.run_and_confirm("上料", payload, resource, target_site)
```
示意代码中的装饰器、基类和返回类型以目标 Uni-Lab OS 版本的开发规范为准。重点是对象关系和变量映射，不是复制某一套本机源码。

## 五、资源、库位、物料和试剂

### 5.1 库位设计示例

下面的库位编码用于演示“来源 → 工艺 → 结果”的路径。真实项目可换成现场编码，但必须保持每个编码在资源图中唯一。

| 库位编码 | 用途 | 允许内容 | 示例容器/资源 |
| --- | --- | --- | --- |
| T01 | 样品输入位 | 样品托盘 | 输入托盘 |
| T02 | 声共振待处理位 | 样品托盘 | 中转托盘 |
| E01 | 液体试剂位 | 液体试剂瓶 | 乙醇瓶 |
| A01 | 粉体试剂位 | 粉体容器 | 氧化铝粉体瓶 |
| C01 / C02 | 坩埚准备位 | 坩埚 | 氧化铝坩埚 |
| D01 | 加样工站位 | 托盘或球磨罐 | 加样工位 |
| SINT01 | 烧结工艺位 | 坩埚 | 烧结腔位 |
| RES01 | 声共振工艺位 | 托盘 | 声共振腔位 |
| PT01 / PC01 | 结果与人工取出位 | 托盘或坩埚 | 结果架/人工位 |

### 5.2 库存实例示例

| 对象 | 容器 | 条码示例 | 数量示例 | 初始库位 |
| --- | --- | --- | --- | --- |
| 样品托盘 | 托盘 | SAMPLE-TRAY-#### | 1 个 | T01 |
| 乙醇 | 液体试剂瓶 | ETHANOL-#### | 50 mL（最低 10 mL） | E01 |
| 氧化铝粉体 | 粉体瓶 | ALUMINA-#### | 80 g（最低 20 g） | A01 |
| 氧化铝坩埚 | 坩埚 | CRUCIBLE-#### | 1 个 | C01 |
| 球磨珠 | 球磨珠容器 | BEAD-#### | 按颗数或质量计量 | D01 附近的供料位 |

| 条码只是示例格式。预检使用实际条码和服务端 material UUID；不要把“名称相同”当作同一物料。试剂必须同时检查当前数量、单位、最低可用量和容器兼容性。 |
| --- |

### 5.3 摆放与移动规则

先读取资源图，确认目标库位存在、允许的 content_type 匹配、当前没有其他物料占用。

再读取物料详情，确认 revision、current_site 和条码；把实际实物放入对应容器和库位。

若需要服务端调整库位，使用带 expected_revision 的原子放置请求；成功后重新读取并核对 current_site。

### 库存查询与受控摆放（接口示意）

```http
GET  {API_BASE}/materials/graph
GET  {API_BASE}/materials/{material_uuid}
PUT  {API_BASE}/materials/{material_uuid}
{
"site_placement": {"action": "place", "site_uuid": "<target_site_uuid>"},
"expected_revision": <revision_from_latest_read>
}
```

如果存在 active、unknown 或未结算的任务，先查任务、作业和库存结算，再决定是否恢复库位；不能直接覆盖服务端状态。

## 六、设计并发布实验操作节点

实验操作是可复用的最小实验单元。每个操作都必须包含至少一个设备动作节点，并明确资源输入、输出和库存变化。

### 6.1 操作一：样品上料与加样

| 顺序 | 节点类型 | 节点内容 | 输入/输出 |
| --- | --- | --- | --- |
| 1 | 资源节点 | 从 T01 取得样品托盘，从 E01 取得乙醇 | sample_tray、ethanol |
| 2 | 设备动作 | transport_robot.run_loading(pick_place=[1,2]) | 托盘到 D01，返回 resource/site |
| 3 | 设备动作 | dosing_station.run_dosing(dose_g=12.5, tolerance_g=0.5) | 消耗乙醇，返回实际重量和结果 |
| 4 | 结果节点 | 记录批次、条码、实际重量和事件 | sample_tray、consumption、evidence |

### 实验操作一（示意代码）

```python
@workflow(workflow_uuid="<operation_uuid>",
displayname="样品上料与加样操作",
workflow_type="experiment_operation")
def sample_loading_operation(sample, ethanol, task_no, dose_g=12.5):
tray = material_source(sample, site="T01")
loaded = transport_robot.run_loading(
task_no=task_no, pick_place=[1, 2], resource=tray, target_site="D01")
return dosing_station.run_dosing(
task_no=task_no, source_position=1, target_position=2,
dose_g=dose_g, tolerance_g=0.5,
sample=loaded.resource, reagent=material_source(ethanol, site="E01"))
```
### 6.2 操作二：烧结与声共振

| 顺序 | 节点类型 | 节点内容 | 输入/输出 |
| --- | --- | --- | --- |
| 1 | 资源节点 | 从 C01 取得坩埚，从 A01 取得氧化铝，从 T02 取得托盘 | crucible、powder、tray |
| 2 | 设备动作 | sintering_station.run_sintering(temperature_c=850, hold_s=600) | 坩埚到 SINT01 |
| 3 | 设备动作 | transport_robot.run_resonance_load(acceleration_g=2, frequency_hz=1000, time_s=30) | 托盘到 RES01 |
| 4 | 设备动作 | transport_robot.run_resonance_unload() | 托盘到 PT01 |
| 5 | 设备动作 | transport_robot.run_unloading() | 坩埚到 PC01 |
| 6 | 结果节点 | 记录炉状态、共振参数、条码和事件 | 结果物料与证据 |

### 实验操作二（示意代码）

```python
@workflow(workflow_uuid="<operation_uuid>",
displayname="烧结与声共振操作",
workflow_type="experiment_operation")
def sintering_resonance_operation(crucible, powder, tray, task_no):
sintered = sintering_station.run_sintering(
task_no=task_no, crucible_position=1,
temperature_c=850, hold_s=600,
resource=material_source(crucible, site="C01"),
reagent=material_source(powder, site="A01"))
loaded = transport_robot.run_resonance_load(
task_no=task_no, source_position=2,
acceleration_g=2, frequency_hz=1000, time_s=30,
resource=material_source(tray, site="T02"))
transport_robot.run_resonance_unload(
task_no=task_no, source_position=1, target_position=2,
resource=loaded.resource)
return transport_robot.run_unloading(
task_no=task_no, pick_position=1, place_position=1,
graphite_boat_type=0, resource=sintered.resource)
```
发布门禁：两个操作的 workflow_type 都是 experiment_operation；每个操作至少有一个动作节点；节点 UUID 唯一；所引用设备、动作、资源和库位都已经注册。

## 七、建立父工作流并发布

父工作流负责组合操作和传递参数。它不重复实现设备通讯。每个父工作流至少调用一个已经发布的实验操作。

| 父工作流示例 | 子操作 | 输入 | 发布顺序 |
| --- | --- | --- | --- |
| 样品上料与加样工作流 | 样品上料与加样操作 | 样品、乙醇、任务号、目标剂量 | 先发布 child，再保存/应用 parent，最后发布 parent |
| 烧结与声共振工作流 | 烧结与声共振操作 | 坩埚、氧化铝、托盘、工艺参数、任务号 | 同上；child 发布合同必须来自当前服务 |

### 父工作流与子操作关系（示意代码）

```python
@workflow(workflow_uuid="<parent_uuid>",
displayname="样品上料与加样工作流",
workflow_type="normal")
def sample_loading_workflow(inputs):
return sample_loading_operation(
sample=inputs.sample, ethanol=inputs.ethanol,
task_no=inputs.task_no, dose_g=inputs.dose_g)
# 1) child 发布成功
# 2) parent 保存并 apply authoring
# 3) parent 发布时使用当前 revision 和 child contract UUID/digest
| 不要手填旧的 contract UUID、revision 或 digest。每次新建工作区都从当前服务的 authoring 和 publication 响应读取这些值，并把响应保存为本轮证据。 |
| --- |
```
## 八、启动 PLC-Sim 与 Uni-Lab OS 服务

启动前先确认目标环境的 Python/依赖、PLC-Sim 模式和 Uni-Lab OS 版本。安装背景和 PLC-Sim 操作可参考本手册开头的在线地址。

打开两个终端或两个服务面板：一个运行 PLC-Sim，一个运行 Uni-Lab OS Host、backend 和 edge。

让服务自行分配或显示协议端口；记录启动输出中的实际 endpoint，不在文档或脚本里写死端口。

等待 Host、backend、edge 均为 ready，再查询健康状态；若为 dry-run，确认运行模式与预期一致。

### 服务状态检查（命令示意）

```text
export LAB_ROOT='<包工作区>'
export API_BASE='<Uni-Lab OS API 地址>'
export PLC_ENDPOINT='<PLC-Sim 或真实 PLC 地址>'
unilab workspace status --workspace "$LAB_ROOT" --json
curl -s "$API_BASE/health"
# PLC_ENDPOINT 只用于当前环境配置，不复制到工作流业务逻辑
```

| 状态 | 可以继续 | 需要处理 |
| --- | --- | --- |
| Host/backend/edge ready，health 正常 | 注册设备、加载资源、发布工作流 | — |
| 服务启动但 PLC 为 idle（dry-run） | 可以做软件链路和动作 smoke | 真实执行前还要完成协议和安全联锁验证 |
| offline / unreachable / schema mismatch | — | 查看服务日志、版本和 endpoint，修复后重新检查 |

## 九、加载、静态检查和动作 smoke

把设备包注册到当前工作区，确认包元数据、设备类型、资源模板和工作流源被识别。

执行静态检查：导入不应触发真实动作；所有装饰器、公开 ID、节点 UUID 和资源引用都应可解析。

逐个执行设备动作 smoke。只使用模拟数据或空载资源，验证参数校验、状态轮询、超时和错误映射。

### 加载与静态检查（命令示意）

```python
unilab package inspect --path "$LAB_ROOT"
unilab package validate --path "$LAB_ROOT"
unilab device action-smoke --device <设备实例 ID> --action <动作名>
# 通过标准：设备可注册，动作可发现，dry-run 返回结构化 success/message/result
```

| 检查对象 | 最低通过标准 |
| --- | --- |
| 设备 | 三个示例设备类型可加载，实例 ID 唯一，初始化不执行真实动作 |
| 动作 | 六个示例动作可发现；参数类型、单位、必填项和默认值明确 |
| 资源 | 托盘、坩埚、容器和工艺位可查询，库位类型与容量闭合 |
| 工作流源 | 两个 child 和两个 parent 的 UUID/节点标记唯一，引用关系可解析 |
| 驱动 | dry-run 与真实驱动共享动作接口，未确认协议时拒绝真实写入 |

## 十、发布前检查、保存和发布

### 10.1 API 检查闭环

常用接口（路径示意；以目标服务 OpenAPI 为准）

```http
GET  {API_BASE}/health
GET  {API_BASE}/workflows/{workflow_uuid}/authoring
POST {API_BASE}/workflows/{workflow_uuid}/publications
POST {API_BASE}/workflows/{workflow_uuid}/run-preflight
GET  {API_BASE}/materials/graph
```

| 步骤 | 动作 | 成功响应应保存什么 |
| --- | --- | --- |
| 1 | 读取 child authoring | 当前 workflow revision、diagnostics、source node UUID |
| 2 | 发布 child | publication status、contract UUID、version、digest/source hash |
| 3 | 保存并 apply parent | parent authoring revision、child contract UUID/digest 引用 |
| 4 | 发布 parent | parent publication status 和合同信息 |
| 5 | 运行 preflight | 每个门禁的 pass/fail、诊断和所需资源 |

发布前的硬门禁：child 必须为 published；parent 必须为 normal 且引用当前 child；authoring 已 applied；diagnostics 为空；每个操作至少一个设备动作节点。

## 十一、准备实验任务并预检

### 11.1 任务输入示例

### 实验任务输入（示意 JSON）

```json
{
```

"workflow_uuid": "<published_parent_uuid>",

"inputs": {

"sample_barcode": "SAMPLE-TRAY-####",

"reagent_barcode": "ETHANOL-####",

"crucible_barcode": "CRUCIBLE-####",

"powder_barcode": "ALUMINA-####",

"task_no": "<PLC任务号>",

"dose_g": 12.5,

"temperature_c": 850,

"hold_s": 600

```text
}
}
```

### 11.2 实物与服务端库存同时就位

按资源图把样品托盘、试剂瓶、粉体瓶、坩埚和球磨珠容器放到示例库位；现场条码必须与服务端实例一致。

拍照或记录摆放前快照，查询所有相关物料的 current_site、数量和 revision。

运行 parent 的 run-preflight；确认设备 ready、动作参数在范围内、库位空闲、试剂余量足够、child/parent 已发布。

| 预检门禁 | 通过条件 | 不通过时的调整 |
| --- | --- | --- |
| 发布状态 | 工作流修订与 child 发布合同一致 | 重新读取 authoring，按 child → parent 顺序发布 |
| 设备状态 | Host、设备实例和驱动 ready | 检查服务状态、endpoint、模式和安全联锁 |
| 资源占用 | 每个输入物料只在一个合法库位，目标位可用 | 先查 active 任务和占用，再受控移动 |
| 库存数量 | 当前量 − 预计消耗 ≥ 最低可用量 | 补充试剂或降低批量，重新预检 |
| 参数范围 | 温度、时间、重量、频率、加速度均有单位且在限值内 | 修改任务输入，不绕过动作入口校验 |

## 十二、执行两个实验任务并记录结果

### 12.1 任务一：样品上料与加样

提交已发布的“样品上料与加样工作流”，使用一组新的 PLC 任务号和实际条码。

等待任务进入可执行状态；记录 task UUID、job UUID、设备动作顺序和每个状态变化。

确认上料状态完成、加样结果 OK、实际重量在容差内，且乙醇消耗被结算。

### 12.2 任务二：烧结与声共振

提交已发布的“烧结与声共振工作流”，使用实际坩埚、粉体和托盘条码。

确认烧结上料、声共振上料、声共振下料和烧结下料按顺序完成；每个动作均有成功事件。

确认坩埚、托盘和剩余试剂的最终库位/数量与实验设计一致。

### 任务状态和证据查询（接口示意）

```http
POST {API_BASE}/workflow-tasks
GET  {API_BASE}/workflow-tasks/{task_uuid}
GET  {API_BASE}/workflow-tasks/{task_uuid}/jobs
GET  {API_BASE}/workflow-tasks/{task_uuid}/events
GET  {API_BASE}/workflow-tasks/{task_uuid}/inventory-consumptions
```

| 证据类别 | 至少记录 |
| --- | --- |
| 任务 | task UUID、parent workflow UUID、提交时间、最终状态 |
| 作业 | job UUID、动作名、开始/结束时间、状态和错误信息 |
| PLC | 任务号、写入参数、返回状态、实际重量/结果、超时信息 |
| 库存 | 物料 UUID、条码、执行前后库位、试剂消耗和单位 |
| 发布 | child/parent publication status、contract/version、revision |

| 只有服务端任务终态、作业终态、库存结算和事件记录都一致，才把任务标记为“执行成功”。命令行超时本身不能证明服务端任务已停止。 |
| --- |

## 十三、不满足条件时的调整处理

| 现象 | 可能原因 | 处理顺序 |
| --- | --- | --- |
| CSV 无法解析 | 编码或列结构不符 | 保留原始字节 → 检查编码 → 检查列名/重复变量 → 重新生成映射 |
| 设备动作不可发现 | 设备未注册、导入失败或 ID 不一致 | 查注册清单 → 查动作索引 → 修正声明 → 重启/重新加载 |
| child 发布失败 | 类型不是 experiment_operation、节点 UUID 重复或诊断不为空 | 读取 authoring diagnostics → 修正节点/引用 → 重新保存和发布 |
| parent invalid | child 未发布或 contract/version 过期 | 先发布 child → 重新读取 parent authoring → apply → 发布 parent |
| 预检缺料/占位 | 条码、库位、容器或数量不满足 | 盘点实物和服务端 → 受控摆放 → 重新预检 |
| 任务 failed/unknown | PLC 返回失败、断线、超时或动作幂等问题 | 保留 task/jobs/events → 确认设备安全状态 → 依据恢复方案处理，不盲目重提 |

### 13.1 受控库存恢复

先查询相关任务是否 active、unknown 或存在未结算 consumption；有则先完成任务处置。

读取物料最新 revision 和 current_site，确认目标位空闲且类型兼容。

使用 expected_revision 执行放置，重新读取确认；记录恢复原因、操作者和时间。

恢复不会补回已经消耗的试剂；补充数量必须依据实际盘点。

## 十四、最终验收清单

| 领域 | 验收问题 | 结果栏 |
| --- | --- | --- |
| 输入 | CSV 编码、列结构、变量名唯一，原始字节有记录 | □ 通过  □ 待处理 |
| 设备 | 设备类型和实例可加载，导入不触发真实动作 | □ 通过  □ 待处理 |
| 动作 | 每个动作有参数/单位/状态机，至少完成一次 smoke | □ 通过  □ 待处理 |
| 资源 | 资源模板、库位 content_type、容量和父子关系闭合 | □ 通过  □ 待处理 |
| 物料/试剂 | 条码、容器、数量、最低库存和实际摆放一致 | □ 通过  □ 待处理 |
| 操作 | 两个 child 都是 experiment_operation，且各含设备动作节点 | □ 通过  □ 待处理 |
| 工作流 | 两个 parent 都是 normal，并引用已发布 child | □ 通过  □ 待处理 |
| 发布 | authoring applied、diagnostics 为空、publication 成功 | □ 通过  □ 待处理 |
| 执行 | 两个任务预检通过，终态 succeeded，事件和库存结算一致 | □ 通过  □ 待处理 |
| 证据 | 保存本轮 UUID、版本、时间、模式、日志和库存快照 | □ 通过  □ 待处理 |

附录 A｜可复用的最小模板

A.1 动作返回值

```text
{
```

"success": true,

"message": "动作完成",

"status_code": 2,

"task_no": "<PLC任务号>",

"resource": "<material_uuid>",

"site": "<site_uuid>",

"result": "success",

"evidence": {"started_at": "<timestamp>", "finished_at": "<timestamp>"}

```text
}
```

A.2 状态机约定

未执行(0) → 执行中(1) → 执行完成(2)

↘ 任务失败(3)

动作轮询必须设置超时；超时后返回 unknown/timeout，保留最后一次状态和请求参数。

A.3 变量映射登记表

| 动作名 | 写入字段 | 读取字段 | 类型/单位 | 备注 |
| --- | --- | --- | --- | --- |
| <action_name> | <write_variable> | <status_variable> | <INT/REAL + unit> | 任务号和触发约定 |
| <action_name> | <parameter_variable> | <result_variable> | <type + unit> | 范围与精度 |

A.4 运行证据索引

```text
{
```

"source": "<CSV源说明>",

"package_identity": "<示例设备包 ID>",

"published_workflows": ["<child_uuid>", "<parent_uuid>"],

"tasks": ["<task_uuid_1>", "<task_uuid_2>"],

"runtime_mode": "<dry-run|real>",

"captured_at": "<timestamp>",

"inventory_snapshot": "<snapshot reference>"

```text
}
```

附录 B｜交付前填写页

将下表复制到项目记录中填写。填写内容应来自当前服务和当前实物盘点，不能从旧任务或别的工作区复制。

| 项目 | 本轮实际值 |
| --- | --- |
| CSV 编码 / 行数 / 校验值 |  |
| Uni-Lab OS API 地址 / 运行模式 |  |
| PLC endpoint / 协议确认人 |  |
| 设备实例与动作清单 |  |
| 库位图版本 / 物料条码 |  |
| child publication / contract / revision |  |
| parent publication / contract / revision |  |
| 任务 UUID 与最终状态 |  |
| 异常、调整和恢复记录 |  |

完成以上填写并核对“最终验收清单”后，才把设备包和工作流交给下一位操作者或现场系统。

## 附件

- {download}`PLC通讯CSV到Uni-Lab-OS工作流执行全流程skill.md`
