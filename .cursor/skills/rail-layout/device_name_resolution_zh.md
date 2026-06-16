# 设备名称解析（导轨布局专用）

> 本文件摘自 `layout_intent_translator_zh.md` 的「设备名称解析」一节，并针对导轨机械臂布局做了裁剪。
> 它规定了 agent 如何把**用户给出的实验流程里的设备**（通常是非正式名称）匹配到 `GET /devices`
> 目录中**具体的 footprint 设备 ID**。这是产出正确坐标的前提：`/rail/feasibility` 与 `/rail/layout`
> 都以解析出的设备 ID 列表为输入。

你将收到 `GET /devices` 返回的当前设备目录作为上下文。这是有效设备 ID 的**唯一**来源。
用户会用非正式名称指代设备——你必须把它们匹配到目录中的精确 ID。

## 输入上下文格式

`GET /devices` 每个设备至少给出 `id`、`name`、`device_type`、`bbox`（和 `openings`）。可读成：

```
Devices in catalog:
- thermo_orbitor_rs2_hotel: Thermo Orbitor RS2 Hotel (type: static, bbox: 0.68×0.52m)
- arm_slider: Arm Slider (type: articulation, bbox: 1.20×0.30m)
- opentrons_liquid_handler: Opentrons Liquid Handler (type: static, bbox: 0.65×0.60m)
- agilent_plateloc: Agilent PlateLoc (type: static, bbox: 0.35×0.40m)
- inheco_odtc_96xl: Inheco ODTC 96XL (type: static, bbox: 0.30×0.35m)
- thermo_stacker: Thermo Stacker (type: static, bbox: 0.14×0.66m)
```

## 匹配规则

1. **优先精确匹配**：用户直接给出目录里的 ID（如 `arm_slider`）→ 直接匹配。
2. **名称/品牌匹配**："opentrons" → `opentrons_liquid_handler`，"plateloc" → `agilent_plateloc`。
3. **功能匹配**："PCR machine" / "thermal cycler" → `inheco_odtc_96xl`；"liquid handler" / "pipetting robot"
   → `opentrons_liquid_handler`；"plate hotel" / "storage" → `thermo_orbitor_rs2_hotel`；
   "plate sealer" → `agilent_plateloc`。
4. **类型匹配**："robot arm" / "the arm" / "导轨机械臂" → 查找 `device_type: articulation`
   （或 id/name 命中 arm/slider/rail/gantry/导轨/机械臂 等关键词）。
5. **歧义**：若多个设备都可能匹配，在解析说明里列出候选并选最可能的一个；确实无法判断时，
   停下用 **AskQuestion** 让用户澄清，不要猜。

## 导轨布局的角色划分（解析时同时分三类）

- **机械臂（arm）**：导轨主体。按规则 4 识别。可由用户用 `arm_model={L, working_radius, bbox}` 覆盖。
- **堆栈（stack）**：相邻机械臂间的转运交接点。默认 `thermo_stacker`；用户显式指定时用对应型号；
  也可在目录里按关键词（stack/hotel/buffer/堆栈/缓存/转运）识别。
- **仪器（instruments）**：实验流程里围绕机械臂布置的其余设备，**必须保持工作流顺序**
  （`ordered_instruments` 是有序数组，顺序决定装箱与坐标）。

## 重复设备约定（导轨线性流程暂不涉及，仅作兼容说明）

当同一目录设备在场景中出现多次时：第一个实例用裸 ID（如 `plate_reader`），第二个及之后用
`#N`（`plate_reader#2`）。**本导轨技能的适用范围是「无多台同类型仪器的线性实验流程」**，
因此正常情况下每种仪器只有一台、用裸 ID 即可。

## 解析示例

用户说："流程是 板架 → 移液 → 封板 → PCR，arm_slider 负责转运"

目录设备：`arm_slider`（articulation）、`thermo_orbitor_rs2_hotel`、`opentrons_liquid_handler`、
`agilent_plateloc`、`inheco_odtc_96xl`、`thermo_stacker`

解析结果：
- 机械臂 → `arm_slider`（唯一 articulation）
- 堆栈 → `thermo_stacker`（默认，用户未指定）
- 有序仪器 `ordered_instruments` →
  `["thermo_orbitor_rs2_hotel", "opentrons_liquid_handler", "agilent_plateloc", "inheco_odtc_96xl"]`
