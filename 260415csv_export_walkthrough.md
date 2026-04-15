# CSV 导出功能变更概要

## 修改的文件

### 1. [bioyond_cell_workstation.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/devices/workstation/bioyond_studio/bioyond_cell/bioyond_cell_workstation.py)

#### 新增导入
- `import csv` 和 `import os`（L14-15）

#### 新增方法

| 方法 | 功能 |
|------|------|
| `_extract_prep_bottle_from_report` | 从 order_finish 报文提取**配液瓶**信息（每订单最多1个） |
| `_extract_vial_bottles_from_report` | 从 order_finish 报文提取**分液瓶**信息（每订单可多个，返回数组） |
| `_export_order_csv` | 汇总所有信息写入 CSV 文件 |

#### 配液瓶筛选逻辑 (`_extract_prep_bottle_from_report`)
- `typemode="1"`, `realQuantity=1`, `usedQuantity=1`
- `locationId` 以 `3a19deae-2c7a-` 开头（手动传递窗）
- LIMS API 二次确认：`typeName` 含"配液瓶(小)"或"配液瓶(大)"

#### 分液瓶筛选逻辑 (`_extract_vial_bottles_from_report`)
- `typemode="1"`, `realQuantity=1`, `usedQuantity=1`
- `locationId` 以 `3a19debc-84b5-` 或 `3a19debe-5200` 开头（自动堆栈-左/右）
- LIMS API 二次确认：`typeName` 为"5ml分液瓶"或"20ml分液瓶"
- **返回数组**，支持 1×5ml + n×20ml 的组合

#### 修改的方法

| 方法 | 变更 |
|------|------|
| `_submit_and_wait_orders` | 新增配液瓶+分液瓶提取步骤，将 `prep_bottles` 和 `vial_bottles` 存入 `final_result` |
| `create_orders` | 添加 `csv_export_path` 参数，末尾调用 `_export_order_csv` |
| `create_orders_formulation` | 添加 `csv_export_path` 参数，末尾调用 `_export_order_csv` |

#### CSV 输出格式
```
orderCode, orderName, 配液瓶类型, 配液瓶二维码, 分液瓶类型, 分液瓶二维码, 目标配液质量比, 真实配液质量比, 时间
```
- 单个分液瓶时直接写值；多个分液瓶时类型和二维码用 JSON 数组表示
- CSV 编码使用 `utf-8-sig`（兼容 Excel 打开）
- `csv_export_path` 默认为空字符串，不传则不导出（向后兼容）

---

### 2. [bioyond_cell.yaml](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/registry/devices/bioyond_cell.yaml)

为两个 action 注册了 `csv_export_path` 参数：

- `auto-create_orders`: `goal_default` + `schema.properties.goal.properties` 中添加 `csv_export_path`
- `auto-create_orders_formulation`: 同上

---

### 3. [coin_cell_assembly.py](file:///d:/UniLabdev/Uni-Lab-OS/unilabos/devices/workstation/coin_cell_assembly/coin_cell_assembly.py) 的 CSV 改动与全流程追溯

在 `bioyond_cell_workstation.py` 的 `_submit_and_wait_orders` 最后阶段，提取 `prep_bottles`（配液瓶）和 `vial_bottles`（分液瓶）的条码并随 `mass_ratios` 数组一起下发给各下游工站（例如扣电组装站），实现跨站的全流程配方追溯。

并在扣电站生成的 `date_xxx.csv` 中，**替换并新增**了以下列：
- 移除了原有的 `formulation_order_code` 与合并的 `formulation_ratio` 列。
- 新增 `orderName` 导出
- 新增 `prep_bottle_barcode`（奔曜传递的配液瓶二维码）
- 新增 `vial_bottle_barcodes`（奔曜传递的分液瓶二维码，多瓶时存 JSON 数组）
- 新增 `target_mass_ratio` 理论目标质量比
- 新增 `real_mass_ratio` 实际称量真实质量比

*注意：这与操作人员在手套箱内扫码传入扣电站的 `electrolyte_code` 是单独记录的，方便做数据核对。*

## 向后兼容性
- `csv_export_path` 默认值为 `""`（空字符串），现有调用不受影响
- 新增的 `prep_bottles` 和 `vial_bottles` 字段为 `final_result` 和 `mass_ratios` 内部的新增附属字段，不破坏现有数据结构。
