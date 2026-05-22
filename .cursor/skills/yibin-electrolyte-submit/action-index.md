# Action 索引

> Action JSON 文件需运行提取命令生成，详见 [SKILL.md](SKILL.md) 中「生成 Action Schema」。
> 以下描述和参数信息基于源码分析。

---

## 配液分液工站 (`bioyond_cell_workstation`)

源码：`unilabos/devices/workstation/bioyond_studio/bioyond_cell/bioyond_cell_workstation.py`

### 调度控制

#### `scheduler_start`

启动 Bioyond LIMS 调度系统

- **核心参数**: 无（仅需 apiKey/requestTime，由设备内部处理）
- **返回**: LIMS 响应 `{code, message, data}`

#### `scheduler_stop`

停止调度

- **核心参数**: 无

#### `scheduler_continue`

继续调度（从暂停状态恢复）

- **核心参数**: 无

#### `scheduler_reset`

复位调度

- **核心参数**: 无

#### `scheduler_start_and_auto_feeding`

**组合操作**：启动调度 + 自动化上料（4号→3号手套箱）

- **核心参数**: `xlsx_path`（Excel 物料模板路径，可选）
- **可选参数**: WH4 加样头面 12 个点位（materialName + quantity）、WH4 原液瓶面 9 个点位（materialName + quantity + materialType + targetWH）、WH3 人工堆栈 15 个点位（materialType + materialId + quantity）
- **流程**: 先 `scheduler_start()`，成功后执行 `auto_feeding4to3()`
- **备注**: 支持 Excel 模式和手动参数模式，Excel 路径存在时优先使用 Excel

### 物料上料/下料

#### `auto_feeding4to3`

自动化上料：从 4 号手套箱转运物料到 3 号手套箱

- **核心参数**: `xlsx_path`（Excel 物料模板路径）
- **可选参数**: 同 `scheduler_start_and_auto_feeding` 的 WH4/WH3 点位参数
- **返回**: 等待上料任务完成后返回结果

#### `auto_batch_outbound_from_xlsx`

自动化下料（从 Excel 读取下料信息）

- **核心参数**: `xlsx_path`（Excel 下料模板）
- **Excel 列**: locationId, warehouseId, 数量, x, y, z

### 物料管理

#### `create_and_inbound_materials`

批量创建固体物料并入库

- **核心参数**: `material_names`（物料名称列表，默认 `["LiPF6", "LiDFOB", "DTD", "LiFSI", "LiPO2F2"]`）
- **可选参数**: `type_id`（物料类型ID）, `warehouse_name`（目标仓库，默认 "粉末加样头堆栈"）
- **流程**: 创建物料 → 批量入库 → 同步

#### `create_material`

创建单个物料并可选入库

- **核心参数**: `material_name`, `type_id`, `warehouse_name`
- **可选参数**: `location_name_or_id`（库位编号如 "A01" 或 UUID）

#### `create_sample`

创建配液板物料（含子瓶）并入库

- **核心参数**: `name`, `board_type`（如 "5ml分液瓶板"）, `bottle_type`（如 "5ml分液瓶"）, `location_code`（如 "A01"）
- **可选参数**: `warehouse_name`（默认 "手动堆栈"）
- **备注**: 自动创建 2x4=8 个子瓶

#### `storage_inbound`

单个物料入库

- **核心参数**: `material_id`, `location_id`

#### `storage_batch_inbound`

批量物料入库

- **核心参数**: `items`（`[{materialId, locationId}, ...]`）

### 配液实验

#### `create_orders`

从 Excel 文件创建配液实验订单

- **核心参数**: `xlsx_path`（Excel 文件路径）
- **Excel 列**: 配方ID, 创建日期, 配液瓶类型, 混匀时间(s), 扣电组装分液体积, 软包组装分液体积, 电导测试分液体积, 电导测试分液瓶数, 以及所有以 `(g)` 结尾的物料列
- **流程**: 解析 Excel → 提交订单 → 等待全部完成 → 计算质量比 → 提取分液瓶板 → 创建资源树对象
- **返回**: `{status, total_orders, bottle_count, reports, mass_ratios, vial_plates}`

#### `create_orders_formulation`

从配方列表创建配液实验订单（前端/API 输入版本）

- **核心参数**: `formulation`（配方数组）
- **可选参数**: `batch_id`, `bottle_type`（默认 "配液小瓶"）, `mix_time`（秒，列表）, `coin_cell_volume`, `pouch_cell_volume`, `conductivity_volume`, `conductivity_bottle_count`
- **formulation 格式**:
  ```json
  [
    {
      "order_name": "配方A",
      "materials": [
        {"name": "LiPF6", "mass": 12.5},
        {"name": "EC", "mass": 50.0},
        {"name": "DMC", "mass": 37.5}
      ]
    }
  ]
  ```
- **返回**: 同 `create_orders`

### 物料转运

#### `transfer_3_to_2_to_1_auto`

**自动转运**：从 create_orders 结果中自动定位分液瓶板并转运到目标设备

- **核心参数**: `vial_plates`（分液瓶板列表，来自 create_orders 返回的 `vial_plates`）
- **可选参数**: `target_device`（默认 "BatteryStation"）, `target_location`（默认 "bottle_rack_6x2"）, `mass_ratios`（配方信息）
- **流程**: 遍历瓶板 → 解析 locationId → 调用 LIMS 转运 API → 更新资源树
- **返回**: `{total, success, failed, results}`

#### `transfer_3_to_2_to_1`

3→2→1 物料转运（手动指定坐标）

- **核心参数**: `source_wh_id`, `source_x`, `source_y`, `source_z`

#### `transfer_3_to_2`

3→2 物料转运

- **核心参数**: `source_wh_id`, `source_x`, `source_y`, `source_z`

#### `transfer_1_to_2`

1→2 物料转运

- **核心参数**: 无

### 查询

#### `order_list_v2`

批量查询实验报告

- **可选参数**: `timeType`, `beginTime`, `endTime`, `status`（60=运行中, 80=完成, 90=失败）, `filter`, `skipCount`, `pageCount`, `sorting`

---

## 扣电组装站 (`BatteryStation`)

源码：`unilabos/devices/workstation/coin_cell_assembly/coin_cell_assembly.py`

### 设备控制（组合操作）

#### `func_pack_device_init_auto_start_combined`

**组合操作**：设备初始化 → 物料搜寻确认 → 切换自动模式 → 启动

- **核心参数**: `material_search_enable`（是否启用物料搜寻，默认 `False`）
- **前置检查**: REG_UNILAB_INTERACT=False, COIL_GB_L_IGNORE_CMD=False, 所有握手寄存器无残留
- **流程**: 手动模式 → 初始化命令 → 监测物料搜寻弹窗并自动处理 → 自动模式 → 启动
- **返回**: `True`/`False`
- **备注**: 第一次运行必须调用此函数；后续批次调用 `func_sendbottle_allpack_multi`

### 批量组装

#### `func_sendbottle_allpack_multi`

**发送瓶数 + 批量组装**（适用于第二批次及后续批次）

- **核心参数**: `elec_num`（电解液瓶数）, `elec_use_num`（每瓶组装电池数）, `elec_vol`（电解液吸液量 μL，默认 50）
- **可选参数**:
  - 双滴模式：`dual_drop_mode`(bool), `dual_drop_first_volume`(μL), `dual_drop_suction_timing`(bool), `dual_drop_start_timing`(bool)
  - 组装参数：`assembly_type`(7=不用铝箔垫/8=用), `assembly_pressure`(N，默认 4200)
  - 物料参数：`fujipian_panshu`, `fujipian_juzhendianwei`, `gemopanshu`, `gemo_juzhendianwei`, `qiangtou_juzhendianwei`
  - 开关：`lvbodian`(铝箔垫片), `battery_pressure_mode`(压力模式), `battery_clean_ignore`(忽略清洁)
  - 其他：`file_path`(CSV保存路径), `formulations`(配方信息，用于CSV追溯)
- **流程**: 发送瓶数触发物料搬运 → 设置PLC参数 → 循环（等待PLC请求→下发参数→读取电池数据→写入CSV→更新资源树）→ 完成握手
- **返回**: `{success, total_batteries, batteries, summary}`
- **备注**: 设备已初始化后直接调用；`formulations` 来自 create_orders 的 `mass_ratios`

#### `func_allpack_cmd`

全套组装（基础版本，含断点续传）

- **核心参数**: `elec_num`, `elec_use_num`, `elec_vol`, `assembly_type`, `assembly_pressure`, `file_path`
- **返回**: `{success, total_batteries, batteries, summary}`

#### `func_allpack_cmd_simp`

增强版组装（含双滴模式 + 负极片/隔膜/枪头参数）

- **核心参数**: 同 `func_sendbottle_allpack_multi`
- **备注**: 被 `func_sendbottle_allpack_multi` 内部调用

### 设备控制（单步操作）

#### `func_pack_device_init`

设备初始化（手动模式 → 初始化 → 复位标志）

#### `func_pack_device_auto`

切换自动模式

#### `func_pack_device_start`

启动设备

#### `func_pack_device_stop`

设备停止

#### `func_pack_send_bottle_num`

发送电解液瓶数（触发物料搬运）

- **核心参数**: `bottle_num`（瓶数）

### PLC 参数设置

#### `qiming_coin_cell_code`

设置组装物料参数

- **核心参数**: `fujipian_panshu`（负极片盘数）
- **可选参数**: `fujipian_juzhendianwei`, `gemopanshu`, `gemo_juzhendianwei`, `lvbodian`, `battery_pressure_mode`, `battery_pressure`, `battery_clean_ignore`

### 数据采集

#### `func_read_data_and_output`

持续数据采集并导出 CSV（后台循环运行）

- **核心参数**: `file_path`（CSV 保存目录）
- **采集字段**: 开路电压, 极片质量, 组装时间, 压制力, 电解液加注量, 电池类型, 电解液二维码, 电池二维码

#### `func_stop_read_data`

停止 CSV 数据采集

### 设备状态属性（只读）

| 属性 | 类型 | 说明 |
|------|------|------|
| `sys_status` | str | 设备状态（启动中/停止中/复位中/初始化中） |
| `sys_mode` | str | 设备模式（手动/自动） |
| `data_assembly_coin_cell_num` | int | 已完成电池数量 |
| `data_assembly_time` | float | 单颗电池组装时间(秒) |
| `data_open_circuit_voltage` | float | 开路电压(V) |
| `data_pole_weight` | float | 正极片称重(g) |
| `data_glove_box_pressure` | float | 手套箱压力(mbar) |
| `data_glove_box_o2_content` | float | 手套箱氧含量(ppm) |
| `data_glove_box_water_content` | float | 手套箱水含量(ppm) |
| `data_coin_cell_code` | str | 电池二维码 |
| `data_electrolyte_code` | str | 电解液二维码 |

---

## 配置参考

设备图文件 `yibin_electrolyte_config.json` 中的仓库映射（`warehouse_mapping`）：

| 仓库名称 | 说明 | 典型操作 |
|---------|------|---------|
| 粉末加样头堆栈 | 20 个点位 (A01-T01) | `create_and_inbound_materials` 入库目标 |
| 配液站内试剂仓库 | 9 个点位 (A01-C03) | 试剂存储 |
| 自动堆栈-左 | 4 个点位 | 分液瓶板存放，`transfer_3_to_2_to_1_auto` 的源位置 |
| 自动堆栈-右 | 4 个点位 | 分液瓶板存放 |
| 手动传递窗左/右 | 各 15 个点位 | 人工上料/下料 |
| 4号手套箱内部堆栈 | 12 个点位 | `auto_feeding4to3` 的源位置 |
