# 片状物料与组装成品

:::{admonition} 阅读角色
- **业务负责人**：确认物料名称、规格、容量、放置规则、条码和现场状态。
- **开发人员**：实现物料模板、放置位约束、启动图实例和工作流资源合同。
- **验收人员**：核对实物兼容性、位置关系、数量变化、搬运和异常恢复。
:::

本页适用于极片、隔膜、垫片、弹片、金属箔等片状物料，以及由多件物料组装形成的电池或其他成品。当前代码提供 `ElectrodeSheet` 和 `Battery` 两类基础实现，自己的设备包应在此基础上登记本项目的具体规格。

:::{admonition} 本页完成条件
每一种片材和成品都有稳定类型编号；固定规格与现场状态分开保存；厚度、质量、直径和用量单位明确；片材装入弹夹、取出和组装后，系统中的数量与归属保持正确。
:::

## 1. 区分固定规格和现场状态

| 数据 | 放在模板中 | 放在现场实例状态中 |
| --- | --- | --- |
| 物料类别、标称直径、标称厚度 | 是 | 否 |
| 材质和页面颜色 | 是 | 只有批次差异时才放实例 |
| 实际质量、检测结果 | 否 | 是 |
| 条码、批次、当前位置 | 否 | 是 |
| 组装压力、电解液信息、开路电压 | 否 | 是 |

同一类型的外形或用途已经不兼容时，应新增类型编号。仅批次、实测质量或检测结果不同，不需要创建新模板。

## 2. 登记一种片状物料

```python
from unilabos.registry.decorators import resource
from unilabos.resources.battery.electrode_sheet import ElectrodeSheet


@resource(
    id="my_lab_positive_electrode_14mm",
    category=["battery_material", "electrode_sheet"],
    displayname="14 mm 正极片",
    description="用于扣式电池装配的圆形正极片。",
    metadata={
        "diameter_mm": 14.0,
        "nominal_thickness_mm": 0.10,
        "material_type": "positive_electrode",
    },
)
def positive_electrode_14mm(name: str) -> ElectrodeSheet:
    sheet = ElectrodeSheet(
        name=name,
        size_x=14.0,
        size_y=14.0,
        size_z=0.10,
        category="electrode_sheet",
        model="positive_electrode_14mm",
    )
    sheet.load_state({
        "diameter": 14.0,
        "thickness": 0.10,
        "mass": 0.0,
        "material_type": "positive_electrode",
        "color": "#cc3333",
        "info": None,
    })
    return sheet
```

| 字段 | 单位 | 填写规则 |
| --- | --- | --- |
| `size_x/size_y` | mm | 圆片通常填写直径；异形片填写实际最大长宽 |
| `size_z` | mm | 填写厚度，不能用层数代替 |
| `diameter` | mm | 只适用于圆形片材；与外形定义保持一致 |
| `thickness` | mm | 明确是标称值还是实测值 |
| `mass` | g | 未测量时使用约定的空值策略，不能填写猜测值 |
| `material_type` | 固定英文值 | 同一种材料在所有批次中保持一致 |
| `color` | 十六进制颜色 | 只用于页面识别，不代表真实检测结果 |
| `info` | 文字或空值 | 只写必要补充说明，不堆放结构化业务数据 |

## 3. 登记组装完成的成品

`Battery` 适合表示已经完成组装、需要继续追踪和检测的电池实例。当前实现可保存电解液名称、外部电解液编码、开路电压、组装压力和电解液用量。

这些字段属于现场实例状态，不应写成所有电池共用的模板默认事实：

| 状态字段 | 建议单位 | 业务含义 |
| --- | --- | --- |
| `electrolyte_name` | 文字 | 实际使用的电解液名称 |
| `data_electrolyte_code` | 文字 | 外部系统或批次编码 |
| `open_circuit_voltage` | V | 实测开路电压 |
| `assembly_pressure` | 项目统一单位 | 组装压力；单位必须在项目规范中固定 |
| `electrolyte_volume` | µL | 实际加入的电解液体积 |
| `info` | 文字或空值 | 异常或必要备注 |

如果项目无法确认 `assembly_pressure` 的统一单位，禁止直接上线该字段；应先与工艺和设备团队确定单位并补充到字段名称或说明中。

## 4. 与弹夹和工作流配合

- 每个弹夹洞位保存片材堆栈，不把多层片材压成一个数量字段；
- 工作流取片成功后再从来源堆栈移除，并加入目标位置；
- 结果不确定时不减少库存，进入人工盘点；
- 组装完成后创建或更新成品实例，并保留所用批次的追溯关系；
- 页面颜色只帮助辨认物料，不能替代条码、批次或视觉检测。

## 5. 交付前必须检查

- 类型编号、外形、材质和单位有正式来源；
- 片材厚度不会被错误写成毫米以外的单位；
- 弹夹最大层数与实际深度、片材厚度和安全余量相符；
- 空洞、满洞、粘片、重复取片和结果不确定都有处理路径；
- 成品状态可以保存、重新加载并保持不变；
- 所有状态值都能保存为标准 JSON 数据。
