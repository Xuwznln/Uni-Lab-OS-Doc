# 物料系统标准化重构方案

根据开发者的反馈，本方案旨在遵循“标准化而非绕过”的原则，对资源类（Deck、Carrier、Magazine）进行重构。核心目标是将物理结构的初始化与物料/极片的初始填充逻辑解耦，彻底解决反序列化过程中的初始化冲突。

## 拟议变更

### [参考] PRCXI9300 标准化模式
#### [参考文件] [prcxi.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/devices/liquid_handling/prcxi/prcxi.py)
*   **PRCXI9300Deck**: 演示了如何在 `serialize` 中导出 `sites` 元数据，以及如何在 `assign_child_resource` 中实现稳健的槽位匹配（支持按名称、坐标或索引匹配）。
*   **PRCXI9300Container**: 演示了标准的 `load_state` 和 `serialize_state` 模式，确保业务状态（如 `Material` UUID）能正确往返序列化。

### [组件] 台面 (Decks)
#### [修改] [decks.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/resources/bioyond/decks.py)
*   将 `BIOYOND_YB_Deck` 重命名为 **`BioyondElectrolyteDeck`**，对应工厂函数 `YB_Deck()` 重命名为 **`bioyond_electrolyte_deck()`**。
*   `BIOYOND_PolymerReactionStation_Deck` 和 `BIOYOND_PolymerPreparationStation_Deck` **保持不变**。
*   以上三个 Deck 的 `__init__` 中均移除 `setup` 参数和 `setup()` 调用，删除临时的 `deserialize` 重写。

#### [修改 + 重命名] [YB_YH_materials.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/devices/workstation/coin_cell_assembly/YB_YH_materials.py) → `yihua_coin_cell_materials.py`
*   将 `CoincellDeck` 重命名为 **`YihuaCoinCellDeck`**，对应工厂函数 `YH_Deck()` 重命名为 **`yihua_coin_cell_deck()`**。
*   从 `YihuaCoinCellDeck.__init__` 中移除 `setup` 参数和 `setup()` 调用，删除临时的 `deserialize` 重写。

### [组件] 容器类与弹夹 (Itemized Carriers & Magazines)
#### [修改] [magazine.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/resources/battery/magazine.py)
*   重构 `magazine_factory`：将创建 `MagazineHolder` 几何结构（空槽位）的过程与填充 `ElectrodeSheet` 物料的过程分离。
*   确保 `MagazineHolder` 和 `Magazine` 的 `__init__` 过程中不主动创建任何内容物。

#### [修改] [warehouse.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/resources/warehouse.py)
*   确保 `WareHouse` 类和 `warehouse_factory` 遵循相同模式：先初始化几何结构，内容物另行处理。

#### [修改] [itemized_carrier.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/resources/itemized_carrier.py)
*   移除之前添加的 `idx is None` 兜底补丁。
*   修复命名规范，确保 `assign_child_resource` 在反序列化时能准确匹配资源。

### [组件] 状态兼容性 (State Compatibility)
#### [修改] [resource_tracker.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/resources/resource_tracker.py)
*   在 `to_plr_resources` 方法中调用 `load_all_state` 之前，预处理 `all_states` 字典。
*   对于 `Container` 类型的资源，如果其状态中缺少 `liquid_history` 或 `pending_liquids` 等 PLR 新版本要求的键，则填充默认值（如空列表/字典），防止反序列化中断。

### [组件] 料盘 (Material Plates)
#### [修改] [YB_YH_materials.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/devices/workstation/coin_cell_assembly/YB_YH_materials.py)
*   重构 `MaterialPlate`：不在 `__init__` 中直接调用 `create_ordered_items_2d`。
*   重构 `YIHUA_Electrolyte_12VialCarrier`：将其修改为标准的基类定义或在工厂方法中彻底剥离内部 12 个 `YB_pei_ye_xiao_Bottle` 的强制初始化，以防反序列化冲突。

### [组件] 跨站转运与分液瓶板 (Vial Plate Transfer)
#### [修改] [bioyond_cell_workstation.py] & [YB_YH_materials.py]
*   **分析**：目前的 `bioyond_cell_workstation.py` 在执行转移时，是用 `sites=["electrolyte_buffer"]` 试图把整块 `YB_Vial_5mL_Carrier` 板转移给目标。但由于实际工艺中，配液站将分液瓶板传往扣电工站后，是由扣电工站的机械臂**逐瓶抓取**并放入内部的 `bottle_rack_6x2`（电解液缓存位），用完后再放入 `bottle_rack_6x2_2`（废液位），因此配液站的这一次“跨工位资源树转移”在逻辑上存在偏差：目标槽位不应该是装单瓶的载体 `bottle_rack`。
*   **修复方案**：
    1.  **目标端 (Yihua 侧)**：
        *   在 `YB_YH_materials.py` 中为从配液站传过来的“分液瓶板”本身设置一个接驳专用的 `PlateSlot`（或者单纯直接移到 Deck 指定坐标）。这个位置负责真正在资源树层级上合法接收配液站传过来的完整 Board。
        *   重构 `YIHUA_Electrolyte_12VialCarrier`：为了防止初始化反序列化冲突，取消内部在 `__init__` 中自动填充满 12 个 `YB_pei_ye_xiao_Bottle` 实例的逻辑。`bottle_rack_6x2` 和 `bottle_rack_6x2_2` 初始化时均应为空。
    2.  **转运端 (Bioyond 侧)**：
        *   修改 `bioyond_cell_workstation.py` 的资源树数字转运代码，将其转移目标对应到 Yihua 侧新设立的“分液瓶板接驳区域”资源，或者干脆只更新资源树坐标位置（使其脱离 Bioyond Deck 加入 Yihua Deck），而不再强行挂载到一个无法容纳 Carrier 的 `bottle_rack_6x2` 内部。

### [组件] 依华扣电组装工站物料余量监控 (Material Monitoring)
#### [修改] 寄存器直读与前端集成
*   **物理对象保留但虚化追踪**：原有的实体台面对象（如 `MaterialPlate`、`MagazineHolder` 各类型及其对应的洞位坐标）**仍然保留并使用**。保留它们是为了给机器臂提供基础的物理空间取放标定，以及作为前端页面的可视和可交互区块。
*   **内部物料免追踪**：既然余量完全由寄存器接管，**我们将不再在这些弹夹或洞位内部显式生成、塞入和追踪每一个具体的极片或外壳对象 (如 `ElectrodeSheet` 等)**。这恰好与我们的重构主旨（不主动在 `__init__` 建子物料以避开反序列化冲突）完美结合，进一步极大地减轻了后台资源树对象的复杂度。
*   **监控方式变更**：放弃现有的物料余量方式，直接读取依华扣电组装工站开放的寄存器地址以获取准确余量。
*   **前端界面集成**：在前端界面点击负极壳、弹垫片等弹夹的 data view 时，直接读取并显示寄存器中的各自余量。
*   **新增寄存器映射** (参考 `coin_cell_assembly_b.csv`)：
    *   `10mm正极片剩余物料数量（R）`：`read hold_register 520` (REAL)
    *   `12mm正极片剩余物料数量（R）`：`read hold_register 522` (REAL)
    *   `16mm正极片剩余物料数量（R）`：`read hold_register 524` (REAL)
    *   `铝箔剩余物料数量（R）`：`read hold_register 526` (REAL)
    *   `正极壳剩余物料数量（R）`：`read hold_register 528` (REAL)
    *   `平垫剩余物料数量（R）`：`read hold_register 530` (REAL)
    *   `负极壳剩余物料数量（R）`：`read hold_register 532` (REAL)
    *   `弹垫剩余物料数量（R）`：`read hold_register 534` (REAL)
    *   `成品电池剩余可容纳数量（R）`：`read hold_register 536` (REAL)
    *   `成品电池NG槽剩余可容纳数量（R）`：`read hold_register 538` (REAL)

### [配置] JSON 配置文件 (Configuration Files)
#### [修改] 资源类型名称更新
*   更新以下配置文件，将其中的 `BIOYOND_YB_Deck` 替换为新的类名 **`BioyondElectrolyteDeck`**，以及将 `coin_cell_deck` 替换为 **`YihuaCoinCellDeck`**：
    *   `yibin_electrolyte_config.json`
    *   `yibin_coin_cell_only_config.json`
    *   `yibin_electrolyte_only_config.json`

## 验证计划

### 自动化测试
*   对重构后的类运行 `pylabrobot` 序列化/反序列化测试，确保状态能够完美恢复。
*   检查各工作站节点启动时是否仍存在 `ValueError: Resource '...' already assigned to deck` 报错。
*   检查 `resource_tracker` 中是否仍存在重复 UUID 报错。

### 手动验证
*   重启各工作站节点，验证资源树是否能根据数据库数据正确还还原。
*   验证“自动”与“手动”传输窗资源在台面上的分配是否正确。
