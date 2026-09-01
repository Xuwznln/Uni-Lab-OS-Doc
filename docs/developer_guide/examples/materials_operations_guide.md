# 物料实例：物料操作规范（Materials Authority）

> **文档类型**：物料系统操作教程（新权威架构）
> **适用场景**：物料创建、挂载、换位、跨设备转移、内容物管理、状态同步
> **前置知识**：PyLabRobot 基础 | HostLink 概念
> **可运行参照**：`tests/backend/hostlink/test_material_creation_flows_e2e.py`（三种创建来源 + transfer 换位）、`tests/backend/hostlink/test_prcxi_material_e2e.py`（14 阶段全链路）。本文所有示例均从这两个端到端测试提炼，可直接运行对照。

---

## 1. 架构与三条铁律

```
┌─────────────────────────────┐
│  微后端物料权威（materials.db）│  ← 唯一事实源：uuid / 父子关系 / Site 占用 / substances
│  MaterialsService           │
└──────────────┬──────────────┘
               │ （Host 直连本地；Slave 自动经 HostLink 上行，调用方无感）
┌──────────────┴──────────────┐
│  Host（编排下行）             │ → RESOURCE_APPEND / RESOURCE_TREE_SYNC / MATERIAL_SYNC
└──────────────┬──────────────┘
               │ HostLink（TCP JSON 行协议；本进程设备直调协程）
┌──────────────┴──────────────┐
│  设备侧（物料实例）            │  PLR 实例 + resource_tracker + 快照观察者
└─────────────────────────────┘
```

三条铁律：

1. **物料必须带权威 uuid**。创建只发生在微后端（`materials.create` 权威发号，或
  `materials.ensure` 采纳既有 uuid）。设备侧严禁本地造 uuid、严禁快照 "add"。
2. **物料通信全部走 HostLink**，不依赖任何 per-device ROS service。
  本进程设备由 Host 直调节点协程，跨机经 HostLink RPC，语义一致。
3. **所有操作收敛到 `materials.*` 门面**（`unilabos/resources/materials.py`）：
  创建 `create`、对齐 `ensure`、查询 `get`/`search`、挂载 `assign`、
  转移/换位 `transfer`、同步 `update` 与 `node.update_resource`。
  不要手工构造 payload、不要调用带下划线的内部函数。

下文示例中：`node` 是设备的 `DeviceNode`（驱动里即 `self._ros_node` /
runtime 注入的节点对象）；`gateway=` 参数在设备运行时内部自动解析
（Slave 自动走 HostLink），只有脱离设备的脚本/测试需要显式传。

## 2. 最简操作示例

### 2.1 创建物料（权威发 uuid）

两种输入形态，返回的都是带权威 uuid 的新 PLR 实例：

```python
from unilabos.resources import materials
from unilabos.devices.liquid_handling.prcxi.prcxi_labware import PRCXI_BioER_96_wellplate

# 形态 1：本地草稿实例（不带 uuid）
draft = PRCXI_BioER_96_wellplate(name="plate_1")
materials.set_substance_on_target(draft.get_well("A1"), "Water", 100.0)  # 预置液体
plate = materials.create(draft, node=node)          # 权威发 uuid；输入草稿不被修改

# 形态 2：registry 资源类名（发给权威按类目录创建一个回来实例化）
tips = materials.create("PRCXI_300ul_Tips", name="tips_1", node=node)
```

要点：

- 草稿**不能带 uuid**（已有物料走 `ensure`/`update`）；一次 `create` 只允许一个根；
  子孔位液体（`substances`）与 PLR 运行态（`data_json`）随创建一并落库。
- **`node=` 让 tracker 登记在内部完成**：创建成功的权威实例自动进
  `node.resource_tracker`，快照观察者随即开始监听——不需要也不应该手动
  `add_resource`。脱离设备的脚本才省略 `node=`。
- 类名形态与微后端 `POST /materials/instantiate` 出库端点同款实例化 + 登记，
  语义一致（前端出库走端点，设备/脚本走本函数）。

### 2.2 已有 uuid 的物料（ensure / adopt）

适用：开机图对齐、出库扣减产物、任何"外部已定 uuid"的物料。

```python
deck = build_my_deck()                 # 定义/图中的物料，全树已带 uuid
deck.unilabos_uuid = known_uuid

ensured = materials.ensure(deck)
# 权威已存在 -> 直接取权威树（以权威为准，不重复创建）
# 权威缺失   -> 以原 uuid 显式创建（adopt 语义，uuid 分毫不差）
```

注意：adopt 要求**全树每个节点都带 uuid**（含 tip spot / well 等后代），
缺失会直接报错。

### 2.3 查询

```python
# 按 uuid 拉整棵权威树（未命中抛错）
tree_set = materials.get("2b7c1c8e-6a4f-4e1b-9a52-9d2f6c1e0a11")
plate = tree_set.to_plr_resources()[0]        # 实例化回 PLR，含全部子孔位/液体

# 按 resource id（dir 路径）查——物料 name 不允许重复，因此 dir 可定位
tree_set = materials.get("PRCXI_Deck/plate_1")

# 混用多个引用，一次取回多棵树
tree_set = materials.get([deck_uuid, "PRCXI_Deck/plate_1"])

# 按名字搜索（未命中返回 []，不抛错）
trees = materials.search("plate_1")
if trees:
    print(trees[0].root_node.res_content.uuid)

# 设备侧等价入口（DeviceNode，异步）：
tree_set = await node.get_resource(resources_uuid=[uuid], with_children=True)
tree_set = await node.get_resource_by_id("PRCXI_Deck/plate_1")
```

### 2.4 挂载到台面（materials.assign）

把权威已创建的物料 assign 到本设备的目标父物料下，一个调用完成
"拉取/复用实例 → 物理 assign → 权威 move 落父子与 Site 占用 → 快照回写"：

```python
# 物料实例 + 目标父物料名 + slot
materials.assign(node, plate, parent="PRCXI_Deck", slot="T2")

# 裸 uuid 在本地未命中时会从权威加载并实例化
materials.assign(node, plate_uuid, parent="PRCXI_Deck", slot="T2")

# site= 传权威 ResourceSite 的 uuid（机器路径，与 slot 二选一）
materials.assign(node, plate, parent="PRCXI_Deck", site=site_uuid)

# parent=None：挂到设备自身（只登记 tracker，不做 assign，如顶级 syncer 物料）
materials.assign(node, plate)
```

目标位参数（二选一）：

| 参数     | 承载                     | 示例                      | 适用                        |
| ------ | ---------------------- | ----------------------- | ------------------------- |
| `site` | 权威 `ResourceSite.uuid` | `"26da5442-…"`          | 机器路径（前端/微后端下发），move 直传免反查 |
| `slot` | label 或 0-based 数字字符串  | `"A1"` / `"T2"` / `"0"` | 人类/脚本路径；label 优先于数字解释     |

行为细节（自动完成，无需干预）：

- 本地已持有该 uuid 时**复用现有实例**（换位场景），不重复触发
  `resource_tree_add` 回调；
- Host 对远端设备下发挂载走 `RESOURCE_APPEND`（跨机 RPC），与本函数完全同语义；
- 本函数为阻塞式（驱动线程/脚本直接调）；已在节点执行器内的异步代码
  用 `await node.append_resource(payload)`。

### 2.5 内容物与状态同步

写内容物与移液用 PLR 自己的 API：

```python
# 写内容物（液体/固体，默认单位 ul/ug）
materials.set_substance_on_target(plate.get_well("A2"), "Buffer", 60.0)

# 移液（PLR tracker 语义：操作 -> commit）
plate.get_well("A1").tracker.remove_liquid(50.0)
plate.get_well("A2").tracker.add_liquid("Water", 50.0)
plate.get_well("A1").tracker.commit()
plate.get_well("A2").tracker.commit()

# 枪头（物理动作完成后 commit）
tip_spot = tips.get_item("A1")
tip = tip_spot.get_tip(); tip_spot.empty(); tip_spot.tracker.commit()
```

**同步语义（重要）**——本地改了状态，权威什么时候知道：

- **自动同步（设备上下文）**：物料在 `node.resource_tracker` 里（`create(node=)` /
  `assign` 都会登记）即被快照观察者监听，`commit()` 触发自动快照上行——
  syncer 场景全程零显式同步。
- **手动同步（其余场景）**：观察者只监听 `commit()`/assign 回调；绕过 tracker
  的直接赋值、或物料不在设备 tracker 里（纯脚本上下文）时权威不会自动知道，
  操作完成后必须显式同步一次：

```python
# 全部 update 收敛在 materials.update 一个位置。设备上下文首参传 node，
# 身份与网关自动取自 node（Slave 自动经 HostLink）；物料直接传，
# 单个、多个都行，不需要包 []，重复节点内部按 uuid 去重：
materials.update(node, plate)                # 驱动同步代码直接调
materials.update(node, deck, plate, tips)

# 异步等价入口（即 materials.update(node, ...) 的 async 包装）：
await node.update_resource(plate)

# 脱离设备的脚本：不传 node，显式给身份
materials.update(plate, source_device_id="my_tool")
```

习惯建议：驱动的一个动作（如一次移液批次）结束时统一
`await node.update_resource(所有涉及的物料)`，比逐孔位同步高效，也不必
关心观察者是否已经报过——快照是幂等 diff，重复提交无害。

### 2.6 换位与跨设备转移（materials.transfer）

换位（同设备）和跨设备转移是**同一个语义**：由权威先落位
（parent + Site 迁移原子提交），再按 unload → load 投影回两端设备。
全部走 `materials.transfer`：

```python
# 同设备换位：T2 -> T4（source 与 target 是同一设备）
await materials.transfer(
    tips,                      # 物料：实例或裸 uuid
    node.device_id,            # 目标设备
    deck,                      # 目标父物料：实例或裸 uuid
    "T4",                      # 目标 Site 选择器：uuid / label / 数字索引；可省略
    source_device_id=node.device_id,
)

# 跨设备转移：slave_a 的板转到 slave_b 的 deck
await materials.transfer(
    plate, "slave_b", deck_b, "S1",
    source_device_id="slave_a",
)

# 设备驱动内的便捷入口（自动填 source）：
await node.transfer_resource_to_another([plate], "slave_b", [deck_b], ["S1"])
```

自动完成的编排：权威提交位置（源 Site 释放、目标 Site 占用、parent 迁移）
→ 源设备 unload（卸载本地实例，触发 `resource_tree_remove`）→ 目标设备 load
（按 uuid 权威拉取重建实例挂目标 Site，触发 `resource_tree_transfer` + `add`）。
液体/枪头状态随权威 round-trip 恢复，不随进程丢失。
slave→slave、slave→host、host→host 三种形态完全一致。

## 3. 三种创建来源规范

### 来源 A：Syncer（本地接管外部物料系统，实时上报）

事实源在设备侧（如 Bioyond 等第三方工作站），规范两步：

```python
# 1. 注册 + 接管：权威发 uuid，node= 自动进 tracker（观察者开始监听）
created = materials.create(local_draft, node=node)

# 2. 实时上报：外部系统事件只需改本地 PLR 状态并 commit——无需任何显式同步
created.get_well("A2").tracker.add_liquid("Buffer", 25.0)
created.get_well("A2").tracker.commit()      # 观察者自动快照到权威
```

### 来源 B：本地正常创建 + assign 上台面

```python
# 按类名创建（或传草稿实例），node= 自动登记
tips = materials.create("PRCXI_300ul_Tips", name="local_tips", node=node)
# 挂上台面：复用本实例 -> assign -> 权威 move -> 快照
materials.assign(node, tips, parent="PRCXI_Deck", slot="T2")
```

### 来源 C：微后端仓储扣减（`apply_deduct_resource` 语义）

```python
# 扣减产物带 uuid（云端/仓储下发）——带条件创建：
ensured = materials.ensure(deducted_plr)
#   已在权威 -> 直接采用权威记录（不重复创建，version 不变）
#   权威缺失 -> 以原 uuid adopt 创建
# 挂载（本地未命中 -> 从权威加载实例 -> 触发 resource_tree_add）
materials.assign(node, deducted_uuid, parent="PRCXI_Deck", slot="T3")
```

生产入口即 Host 的 `apply_deduct_resource` action（`device_id` + `mount_resource` +
`slot_on_deck` 给齐则扣减并挂载，否则仅登记透传）；前端出库的实例化走微后端
`POST /materials/instantiate` 端点（与 `materials.create("类名", ...)` 同款语义）。

### Host 固定物料 API（画布 / 工作流入口）

`host_node` 对前端画布与工作流暴露**固定的四个物料动作**，方便与 Slave 通信
和在画布上展示；host_node 服务设备只有一份定义
（`unilabos/backend/host_services.py` 的 `HostServices`），两种 backend 都经
各自的通用设备管线从外部初始化它，动作是同一份共享实现
（`unilabos/backend/host_material_actions.py`）的薄壳，业务全部走
`materials.*`：

| 动作                      | 语义        | 底层                                                     |
| ----------------------- | --------- | ------------------------------------------------------ |
| `apply_deduct_resource` | 出库物料（=创建） | `materials.create`（类名现场创建）/ `ensure`（带 uuid adopt）+ 下发 `RESOURCE_APPEND` |
| `set_substance`         | 设置物质      | `materials.apply_substances` + `node.update_resource`  |
| `discard_resource`      | 丢弃物料      | `materials.remove` + 通知设备本地移除                           |
| `transfer_resource`     | 移动物料      | `materials.transfer`（换位/跨设备同语义）                         |

出库即物料进入系统的统一入口，创建是其语义的一部分，两种来源二选一：

- `resource`：已带 uuid 的扣减产物（云端仓储扣减 / 前端 instantiate 端点产物引用）
  → `ensure` 落权威（缺失时以原 uuid adopt 创建）；
- `registry_class` + `material_name`：按 registry 资源类名**现场创建全新物料**
  （权威发号，与 instantiate 端点同款语义，动作路径无需前端先调端点）。

**设备参数全部自动推断，只需给物料本身**。每棵权威根树在诞生时登记所属设备
（`materials.create(node=)` 与根树上台面的 `append_resource` 写根 extra 的
`unilabos_bound_device_id`），`materials.owner_device_of` 沿 parent 链爬到根即可
反查；转移只是并入目标根树，归属自动继承，无需维护：

- `transfer_resource`：只给 `resource` + `mount_resource`（+ 可选 `site`）——
  来源设备 = 物料当前所在根树的归属（unload 发给真实持有者），目标设备 =
  目标物料所在根树的归属；`target_device` 仅作显式覆盖；
- `discard_resource`：只给 `resource`，所属设备自动推断；
- `apply_deduct_resource`：给了 `mount_resource` 即可挂载，目标设备自动推断。

人工确认（`manual_confirm`）是系统自带的通用动作，不属于物料 API；人工搬运
工作流为 `apply_deduct_resource → manual_confirm（人工搬运到位）→
transfer_resource`，与机械臂 pick/place 流使用同一转移语义。

## 4. 设备驱动回调约定

`DeviceNode` 在物料变化时按命名约定回调驱动（存在才调用，支持 async）：

| 回调                                                         | 时机                  | 签名   |
| ---------------------------------------------------------- | ------------------- | ---- |
| `resource_tree_add(resources)`                             | 新实例登记到台面（挂载/下发 add） | 批量列表 |
| `resource_tree_transfer(old_parent, resource, new_parent)` | 物料挂载/换位完成           | 单个   |
| `resource_tree_remove(resources)`                          | 台面卸载（含 transfer 源端）  | 批量列表 |
| `resource_tree_update(resources)`                          | 权威更新投影到本地           | 批量列表 |

注意：换位/重复挂载**不会**重复触发 `resource_tree_add`（同一物理实例复用）；
`transfer` 的目标端 load 是重建实例，会触发 `transfer` + `add`。

## 5. 禁止事项（常见错误）

| 禁止                                | 正确做法                                                  |
| --------------------------------- | ----------------------------------------------------- |
| 设备侧本地生成物料 uuid                    | `materials.create` 权威发号 / `ensure` adopt              |
| 给 `materials.create` 传已带 uuid 的草稿 | 已有物料走 `ensure` 或 `update`                             |
| 手动 `node.resource_tracker.add_resource(created)` | `materials.create(..., node=node)` 内部完成    |
| 手工构造 RESOURCE_APPEND payload / 调内部 `_append` 类函数 | `materials.assign(node, ...)`             |
| 跨设备转移手工编排 remove + append          | `materials.transfer(...)` 一个调用                        |
| 快照上报中出现权威没有的节点（add）               | 先 `create`/`ensure` 再同步                               |
| `adopt` 时只给根 uuid、后代缺失            | 全树每个节点都带 uuid                                         |
| tracker 操作后不 `commit()`           | `add_liquid`/`empty` 等之后 `commit()`（观察者靠它触发）          |
| 绕过 tracker 改状态后不同步               | `await node.update_resource(物料)` 或 `materials.update` |
| 用 1-based slot 数字                 | `slot` 是 label 或 0-based 数字字符串（`"A1"` / `"0"`）        |
| `site` 参数传 label                  | `site` 只承载权威 `ResourceSite.uuid`，label 走 `slot`       |

## 6. 模块速查

| 职责                                                           | 模块                                                                                  |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| 物料门面（create/ensure/get/search/assign/transfer/update/内容物）    | `unilabos/resources/materials.py`                                                   |
| 设备侧投影（append_resource/update_resource/material_sync/回调）      | `unilabos/backend/runtime/node.py`                                                   |
| 权威快照/移动/乐观锁与观察者                                              | `unilabos/backend/runtime/resource.py`                                               |
| 权威服务与存储                                                      | `unilabos/server/services/materials.py`、`server/database/repositories/materials.py` |
| PLR ↔ 权威协议适配（草稿校验/快照投影/Site sidecar）                         | `unilabos/resources/adapters/plr_materials.py`                                         |
| HostLink 下行分发                                                | `unilabos/backend/hostlink/backend.py`、`unilabos/backend/ros2/hostlink_bridge.py`                    |
