# 物料系统标准化重构方案 v2（增强版）

> **基于原始方案 (`implementation_plan.md`) 的补充与细化**。
> 本文档在原方案基础上：①增加当前代码现状核查结果；②明确各任务的执行顺序与文件级改动；③新增注意事项与回归测试命令。

---

## 0. 核心原则（保持不变）

"**物理几何结构初始化（Deck / Carrier / Magazine 的 `__init__`）与物料内容物填充（`setup()` / `klasses` 参数）必须彻底解耦**"，以消除 PLR 反序列化时的 `Resource already assigned to deck` 错误。

---

## 1. 当前代码现状核查（2026-03-12）

| 文件 | 计划要求 | 当前状态 | 是否完成 |
|---|---|---|---|
| `resources/bioyond/decks.py` | 重命名类；移除 `setup` 参数和 `deserialize` 补丁 | 仍是 `BIOYOND_YB_Deck`；`setup` 参数和 `deserialize` 均存在 | ❌ |
| `coin_cell_assembly/YB_YH_materials.py` | 重命名类；文件迁移；移除补丁 | 仍是 `CoincellDeck`；`setup` 参数和 `deserialize` 均存在 | ❌ |
| `resources/battery/magazine.py` | `magazine_factory` 不主动填充物料 | `MagazineHolder_6_Cathode` / `_6_Anode` / `_4_Cathode` 仍传 `klasses`，初始化时填满极片 | ❌ |
| `resources/battery/bottle_carriers.py` | `YIHUA_Electrolyte_12VialCarrier` 初始化时不填充瓶子 | 第 54-55 行仍循环填充 12 个 `YB_pei_ye_xiao_Bottle` | ❌ |
| `resources/itemized_carrier.py` | 移除 `idx is None` 兜底补丁 | 第 182-190 行仍保留该兜底逻辑 | ❌（待前置任务完成后移除） |
| `resources/resource_tracker.py` | `load_all_state` 前预填 `Container` 缺失键 | 第 616 行直接调用，无预处理 | ❌ |
| `bioyond_cell_workstation.py` | 修正跨站转运目标为合法接驳槽 | 第 1563 行仍 `sites=["electrolyte_buffer"]`，目标 UUID 为硬编码虚拟资源 | ❌ |
| `yibin_*.json` 配置文件 | 更新类名 | 仍使用 `BIOYOND_YB_Deck` / `CoincellDeck` | ❌ |
| `registry/resources/bioyond/deck.yaml` | 更新类名（原计划未提及） | 仍使用 `BIOYOND_YB_Deck` / `CoincellDeck` | ❌（**新增**） |

---

## 2. 执行顺序（含依赖关系）

```
阶段 A（底层资源类）
  A1. magazine.py — 移除 klasses 填充
  A2. bottle_carriers.py — 移除瓶子填充
  
阶段 B（Deck 层）
  B1. decks.py — 移除 setup 参数和 deserialize 补丁；重命名
  B2. YB_YH_materials.py → 重命名；移除 CoincellDeck 的 setup 参数和 deserialize 补丁
  
阶段 C（状态兼容）
  C1. resource_tracker.py — 预填 Container 缺失键
  C2. itemized_carrier.py — 移除 idx is None 兜底补丁（B 阶段完成后）
  
阶段 D（跨站转运修复）
  D1. YB_YH_materials.py 新增 vial_plate_dock（接驳专用槽）
  D2. bioyond_cell_workstation.py 修正 transfer 目标
  
阶段 E（配置与注册表）
  E1. yibin_*.json 更新类名
  E2. registry/resources/bioyond/deck.yaml 更新类名
  E3. coin_cell_assembly.py 更新导入路径（若文件重命名）
```

---

## 3. 分阶段详细说明

---

### 阶段 A — 底层资源类

#### A1. `unilabos/resources/battery/magazine.py`

**问题**：`MagazineHolder_6_Cathode`、`MagazineHolder_6_Anode`、`MagazineHolder_4_Cathode` 在调用 `magazine_factory` 时传入 `klasses`，导致每次 `__init__` 就填满极片，反序列化时重复添加。

**修改**：

- 将三个函数中的 `klasses=[...]` 改为 `klasses=None`（与 `MagazineHolder_6_Battery` 保持一致）。
- **理由**：物料余量已由寄存器管理（见阶段 F），不需要在资源树中追踪每一个极片。

```python
# 修改前（MagazineHolder_6_Cathode 举例）
klasses=[FlatWasher, PositiveCan, PositiveCan, FlatWasher, PositiveCan, PositiveCan],

# 修改后
klasses=None,
```

> **注意**：`magazine_factory` 中 `klasses` 参数及循环体代码保留（仍可按需在非序列化场景使用），只是各具体工厂函数不再传入。

---

#### A2. `unilabos/resources/battery/bottle_carriers.py`

**问题**：`YIHUA_Electrolyte_12VialCarrier` 第 54-55 行在工厂函数末尾循环填充 12 个瓶子。

**修改**：删除以下两行：

```python
# 删除
for i in range(12):
    carrier[i] = YB_pei_ye_xiao_Bottle(f"{name}_vial_{i+1}")
```

**理由**：`bottle_rack_6x2` 和 `bottle_rack_6x2_2` 均应初始化为空，瓶子由 Bioyond 侧实际转运后再填入。

---

### 阶段 B — Deck 层重构

#### B1. `unilabos/resources/bioyond/decks.py`

**改动列表**：

1. **重命名** `BIOYOND_YB_Deck` → `BioyondElectrolyteDeck`
2. **重命名** `YB_Deck()` 工厂函数 → `bioyond_electrolyte_deck()`
3. **移除** `__init__` 中的 `setup: bool = False` 参数及 `if setup: self.setup()` 调用
4. **删除** `deserialize` 方法重写（该临时补丁在 `setup` 参数移除后自然失效，继续保留反而掩盖问题）
5. `BIOYOND_PolymerReactionStation_Deck` 和 `BIOYOND_PolymerPreparationStation_Deck` 同步执行第 3、4 步

**重构后初始化模式**：

```python
class BioyondElectrolyteDeck(Deck):
    def __init__(self, name: str = "YB_Deck", ...):
        super().__init__(name=name, ...)
        # ❌ 不调用 self.setup()
        # PLR 反序列化时只会调用 __init__，然后从 children JSON 重建子资源

    def setup(self) -> None:
        # 完整的子资源初始化逻辑保留在这里，只由工厂函数调用
        ...

def bioyond_electrolyte_deck(name: str) -> BioyondElectrolyteDeck:
    deck = BioyondElectrolyteDeck(name=name)
    deck.setup()   # ✅ 工厂函数负责填充
    return deck
```

**同步修改**：
- `bioyond_cell_workstation.py` 第 20 行：
  ```python
  # 修改前
  from unilabos.resources.bioyond.decks import BIOYOND_YB_Deck
  # 修改后
  from unilabos.resources.bioyond.decks import BioyondElectrolyteDeck
  ```
- 同文件第 2440 行：`BIOYOND_YB_Deck(setup=True)` → `bioyond_electrolyte_deck(name="YB_Deck")`

---

#### B2. `unilabos/devices/workstation/coin_cell_assembly/YB_YH_materials.py`

**改动列表**：

1. **重命名** `CoincellDeck` → `YihuaCoinCellDeck`
2. **重命名** `YH_Deck()` → `yihua_coin_cell_deck()`（可保留 `YH_Deck` 作为兼容别名，日后废弃）
3. **移除** `CoincellDeck.__init__` 中 `setup: bool = False` 参数及调用
4. **删除** `CoincellDeck.deserialize` 重写方法
5. `MaterialPlate.__init__` 中移除 `fill` 参数，始终不主动调用 `create_ordered_items_2d`（当前 `fill=False` 路径已正确，只需删除 `fill=True` 分支）

```python
# 修改前（MaterialPlate.__init__ 片段）
if fill:
    super().__init__(..., ordered_items=holes, ...)
else:
    super().__init__(..., ordered_items=ordered_items, ...)

# 修改后（始终走 "不填充" 路径）
super().__init__(..., ordered_items=ordered_items, ...)
# holes 的创建代码整体移入独立工厂方法
```

**同步修改**：
- `coin_cell_assembly.py` 第 20 行导入：
  ```python
  # 修改前
  from unilabos.devices.workstation.coin_cell_assembly.YB_YH_materials import CoincellDeck
  # 修改后
  from unilabos.devices.workstation.coin_cell_assembly.YB_YH_materials import YihuaCoinCellDeck
  ```
- 同文件第 2245 行：`CoincellDeck(setup=True, name="coin_cell_deck")` → `yihua_coin_cell_deck(name="coin_cell_deck")`
- 文件重命名（可选）：`YB_YH_materials.py` → `yihua_coin_cell_materials.py`（若重命名，所有 import 路径需全局替换）

---

### 阶段 C — 状态兼容

#### C1. `unilabos/resources/resource_tracker.py`

**问题**：第 616 行直接调用 `plr_resource.load_all_state(all_states)`，若 `Container` 类资源的 `data` 字段缺少 `liquid_history` 或 `pending_liquids`，PLR 新版本会抛出 `KeyError`。

**修改**：在第 616 行前插入预处理：

```python
# 在 load_all_state 调用前预填缺失键
from pylabrobot.resources.container import Container as PLRContainer
for res_name, state in all_states.items():
    if state and isinstance(state, dict):
        # Container 类型要求这两个键存在
        state.setdefault("liquid_history", [])
        state.setdefault("pending_liquids", {})

plr_resource.load_all_state(all_states)
```

---

#### C2. `unilabos/resources/itemized_carrier.py`

**前提**：B1、B2 阶段完成，Deck 类名与资源命名规范已对齐后再执行此步。

**修改**：删除第 182-190 行的兜底补丁：

```python
# 删除以下整个 if 块
if idx is None:
    fallback_location = location if location is not None else Coordinate.zero()
    super().assign_child_resource(resource, location=fallback_location, reassign=reassign)
    return
```

**替代**：改为抛出带诊断信息的异常，便于后续问题排查：

```python
if idx is None:
    raise ValueError(
        f"[ItemizedCarrier] 无法为资源 '{resource.name}' 找到匹配的槽位。"
        f"已知槽位：{list(self.child_locations.keys())}，"
        f"传入坐标：{location}"
    )
```

---

### 阶段 D — 跨站转运修复

#### D1. `YB_YH_materials.py` — 新增分液瓶板接驳槽

在 `YihuaCoinCellDeck.setup()` 中，新增一个专用于接收 Bioyond 侧传来的完整分液瓶板的 `ResourceStack`（或 `PlateSlot`）：

```python
# 在 setup() 末尾追加
from pylabrobot.resources.resource_stack import ResourceStack

vial_plate_dock = ResourceStack(
    name="electrolyte_buffer",   # 保持与 bioyond_cell_workstation.py 的 sites 键一致
    direction="z",
    resources=[],
)
self.assign_child_resource(vial_plate_dock, Coordinate(x=1050.0, y=700.0, z=0))
```

> **说明**：槽位命名 `electrolyte_buffer` 与 `bioyond_cell_workstation.py` 现有的 `sites=["electrolyte_buffer"]` 对应，减少改动量。如改名，D2 需同步。

---

#### D2. `bioyond_cell_workstation.py` — 修正 transfer 目标

**问题**：第 1545-1552 行创建了一个 `size=1,1,1` 的虚拟 `ResourcePLR` 并硬编码 UUID，这个对象在 YihuaCoinCellDeck 的资源树中不存在，导致转移后资源树状态混乱。

**修改**：

```python
# 修改前：创建虚拟目标资源
target_resource_obj = ResourcePLR(name=target_location, size_x=1.0, ...)
target_resource_obj.unilabos_uuid = "550e8400-e29b-41d4-a716-446655440001"  # 硬编码

# 修改后：通过 ROS2/设备注册表查询真实资源
# （需要从 target_device 的资源树中取出 electrolyte_buffer 的真实对象）
target_resource_obj = self._get_resource_from_device(
    device_id=target_device,
    resource_name=target_location
)
if target_resource_obj is None:
    raise RuntimeError(
        f"目标设备 {target_device} 中未找到资源 '{target_location}'，"
        f"请确认 YihuaCoinCellDeck.setup() 中已添加 electrolyte_buffer 槽位"
    )
```

> **说明**：`_get_resource_from_device` 需根据现有 ROS2 资源同步机制实现，或复用已有的 `get_plr_resource_by_name` 类似方法。

---

### 阶段 E — 配置与注册表

#### E1. `yibin_electrolyte_config.json` / `yibin_coin_cell_only_config.json` / `yibin_electrolyte_only_config.json`

全局替换以下字符串：

| 旧值 | 新值 |
|---|---|
| `BIOYOND_YB_Deck` | `BioyondElectrolyteDeck` |
| `unilabos.resources.bioyond.decks:BIOYOND_YB_Deck` | `unilabos.resources.bioyond.decks:BioyondElectrolyteDeck` |
| `CoincellDeck` | `YihuaCoinCellDeck` |
| `unilabos.devices.workstation.coin_cell_assembly.YB_YH_materials:CoincellDeck` | 若文件已重命名：`unilabos.devices.workstation.coin_cell_assembly.yihua_coin_cell_materials:YihuaCoinCellDeck` |

---

#### E2. `unilabos/registry/resources/bioyond/deck.yaml`（**原计划未覆盖，新增**）

当前第 25 行和第 37 行仍使用旧类名，需同步更新：

```yaml
# 修改前
BIOYOND_YB_Deck:
  ...
CoincellDeck:
  ...

# 修改后
BioyondElectrolyteDeck:
  ...
YihuaCoinCellDeck:
  ...
```

---

### 阶段 F — 物料余量监控集成（原计划第5节细化）

**目标**：弃用资源树内极片对象计数，改为直读依华扣电工站寄存器。

#### F1. `coin_cell_assembly/coin_cell_assembly.py` — 新增寄存器读取方法

参考 `coin_cell_assembly_b.csv` 中的地址，封装读取工具方法：

```python
MATERIAL_REGISTER_MAP = {
    "10mm正极片":   (520, "REAL"),
    "12mm正极片":   (522, "REAL"),
    "16mm正极片":   (524, "REAL"),
    "铝箔":         (526, "REAL"),
    "正极壳":       (528, "REAL"),
    "平垫":         (530, "REAL"),
    "负极壳":       (532, "REAL"),
    "弹垫":         (534, "REAL"),
    "成品容量":     (536, "REAL"),
    "成品NG容量":   (538, "REAL"),
}

def get_material_remaining(self, material_name: str) -> float:
    """通过寄存器直读指定物料的剩余数量"""
    if material_name not in MATERIAL_REGISTER_MAP:
        raise KeyError(f"未知物料名称: {material_name}")
    address, dtype = MATERIAL_REGISTER_MAP[material_name]
    return self.read_hold_register(address, dtype)  # 复用现有 Modbus 读取方法
```

#### F2. 前端 data view 集成

- 前端点击 `MagazineHolder` 类资源的 data view 时，调用后端 `get_material_remaining` 接口（而非读取 `children` 长度）。
- 具体接口路径和前端调用代码需与前端开发同步，本文档不作具体实现约定。

---

## 4. 验证计划（细化）

### 4.1 单元测试（自动化）

```bash
# 序列化/反序列化往返测试
python -m pytest unilabos/test/ -k "serial" -v

# 特别检查以下错误消失：
# - ValueError: Resource '...' already assigned to deck
# - KeyError: 'liquid_history' 
# - 重复 UUID 报错
```

### 4.2 集成测试（手动）

按以下顺序逐步验证，确保每步正常后再进行下一步：

1. **单独启动 `BatteryStation` 节点**，检查 `CoincellDeck`（现 `YihuaCoinCellDeck`）能否从数据库状态正确还原，无 `already assigned` 报错。
2. **单独启动 `BioyondElectrolyte` 节点**，检查 `BioyondElectrolyteDeck` 反序列化正常。
3. **同时启动两个节点**，模拟执行一次分液→扣电的完整跨站转运，确认：
   - `electrolyte_buffer` 槽位正确接收分液瓶板。
   - `bottle_rack_6x2` 初始为空，不出现虚拟瓶子。
4. **重启两个节点**（模拟断电恢复），确认资源树从数据库还原后，`electrolyte_buffer` 中仍持有正确的分液瓶板对象。
5. **寄存器余量读取**：手动触发 `get_material_remaining("负极壳")`，确认返回值与设备显示一致。

---

## 5. 与原计划的差异对照

| 维度 | 原计划 | 本文档新增/修订 |
|---|---|---|
| 执行顺序 | 未排序 | 明确 A→B→C→D→E→F 的依赖顺序 |
| `itemized_carrier.py` | 移除兜底补丁 | 补充：替换为带诊断信息的异常，便于排查 |
| `bottle_carriers.py` | 提及 `YIHUA_Electrolyte_12VialCarrier` 需修改 | 明确：删除第 54-55 行的瓶子填充循环 |
| `MaterialPlate` | 提及移除 `fill` 参数 | 说明保留 `fill=False` 路径；整体删除 `fill=True` 分支 |
| `deck.yaml` | 未提及 | **新增**：该注册文件也需要同步更新类名 |
| `resource_tracker.py` | 简略描述 | 提供具体的 `setdefault` 预处理代码示例 |
| 跨站转运 | 描述了问题和方向 | 细化：新增 `electrolyte_buffer` 槽位的具体名称和坐标；修正 `transfer` 目标查找方式 |
| 验证计划 | 简述目标 | 提供具体测试命令和逐步手动验证流程 |
