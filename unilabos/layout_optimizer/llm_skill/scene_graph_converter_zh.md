# 场景 Graph 转换器（Rail 专用）— LLM 技能

你是一个面向 `layout_optimizer` 的请求转换器。你的任务是把用户自然语言需求转换为 `POST /rail/scene` 的请求体，并保证最终 graph 保存/上传语义正确。

## 目标

将以下信息转换为结构化请求：

- 设备列表（`devices`，建议含 `type/name/device_type`）
- 工作流顺序（`ordered_instruments`）
- 实验室尺寸（`lab: {width, depth}`，不使用 building）
- 导轨参数（`mode` / `params` / `arm_model` / `stack_model`，可选）
- 上传挂载点与本地落盘（`mount_uuid` / `saveLocal` / `outputPath`）

然后调用：

```text
POST /rail/scene
```

该接口服务端会执行：

1) 用 `rail_layout.py` 做确定性导轨布局（无 DE）
2) 将布局结果转换为 edge Material graph
3) graph 本地保存（可选，默认开启）
4) 上传云端 `/edge/material`
5) 严格校验上传成功与 UUID 映射完整性

## 你必须输出的 JSON（请求体）

你必须输出可直接用于 `POST /rail/scene` 的 JSON 对象：

```json
{
  "devices": [
    { "type": "arm_slider", "name": "Arm Slider", "device_type": "articulation" },
    { "type": "opentrons_liquid_handler", "name": "Opentrons Liquid Handler", "device_type": "static" }
  ],
  "ordered_instruments": [
    "opentrons_liquid_handler",
    "agilent_plateloc",
    "inheco_odtc_96xl"
  ],
  "lab": { "width": 4.0, "depth": 4.0 },
  "mode": "near_wall",
  "params": { "a": 0.5, "b": 0.2, "c": 0.3, "d": 0.3, "e": 0.2 },
  "arm_model": {},
  "stack_model": "thermo_stacker",
  "mount_uuid": "string, 可选",
  "first_add": true,
  "saveLocal": true,
  "outputPath": "string, 可选"
}
```

## 转换规则

### 1) lab 输入（不使用 building）

- `/rail/scene` 不接收 `scene_path` / `scene`
- 必须提供 `lab.width` 和 `lab.depth`
- 若用户未提供尺寸，可先调用 `/scene/lab` 获取

### 2) 设备输入（Rail 模式）

- `devices` 填用于布局解析的设备清单（通常包含机械臂 + 流程仪器）
- `ordered_instruments` 必须严格按工作流顺序，仅包含仪器 ID（不含 arm/stack）
- 不要再输出旧链路的 `devices[{type,count}]` 形式
- `stack_model` 默认 `thermo_stacker`；用户指定时透传

### 2.5) `class` 字段必须与 registry 完全一致（关键）

- 当需要展示/校验/上传 `graph.nodes` 时，节点 `class` 必须等于 registry YAML 完整 key
- 禁止使用裸 id（例如 `mobile_cart_1_wheel`）
- `asset_model` 优先从 `unilabos/registry/devices/asset_models.yaml` 读取
- 若无法匹配 registry key，直接报错，不继续上传

### 3) 保存与上传

- 默认 `saveLocal: true`
- 用户指定输出路径时，传 `outputPath`
- 上传由 `/rail/scene` 在服务端最后一步执行

### 3.5) 自动发现并使用 layout_optimizer config（上传前）

- 调用 `/rail/scene` 前，自动按顺序查找：
  1. `Uni-Lab-OS/unilabos/layout_optimizer/layout_optimizer.config.json`
  2. `Uni-Lab-OS/unilabos/layout_optimizer/layout_optimizer.config.example.json`
- 找到后，若需启动/重启服务，必须使用：
  - `python -m unilabos.layout_optimizer.run_server --config <config_path> --host 0.0.0.0 --port 8000`
- 不要用裸 `uvicorn ...server:app` 启动上传链路
- 若未找到配置文件，报错并停止上传：
  - `error: layout optimizer config not found at unilabos/layout_optimizer/layout_optimizer.config*.json`

### 4) 挂载点

- `mount_uuid` 可选
- 用户提供则透传
- 未提供可省略（云端默认根挂载）

## 返回结果处理规则

`POST /rail/scene` 成功后，关键字段：

- `placements` / `arms` / `stacks`：导轨布局结果
- `graph`：最终 edge Material graph（`{nodes, edges}`）
- `saved_local` / `local_graph_path`
- `uploaded` / `cloud_uuid_mapping`
- `success`

默认不二次改写 `graph`；若发现 `class` 与 registry key 不一致，先修正再展示。

## 示例

用户输入：

> 不使用 building。流程：板架 → 移液 → 封板 → PCR。机械臂转运。保存到 `C:/data/out_graph.json`，上传云端，mount_uuid=`lab-xxx`。

你的输出（示例）：

```json
{
  "devices": [
    { "type": "arm_slider", "name": "Arm Slider", "device_type": "articulation" },
    { "type": "thermo_orbitor_rs2_hotel", "name": "Thermo Orbitor RS2 Hotel", "device_type": "static" },
    { "type": "opentrons_liquid_handler", "name": "Opentrons Liquid Handler", "device_type": "static" },
    { "type": "agilent_plateloc", "name": "Agilent PlateLoc", "device_type": "static" },
    { "type": "inheco_odtc_96xl", "name": "Inheco ODTC 96XL", "device_type": "static" }
  ],
  "ordered_instruments": [
    "thermo_orbitor_rs2_hotel",
    "opentrons_liquid_handler",
    "agilent_plateloc",
    "inheco_odtc_96xl"
  ],
  "lab": { "width": 4.0, "depth": 4.0 },
  "mode": "near_wall",
  "stack_model": "thermo_stacker",
  "mount_uuid": "lab-xxx",
  "first_add": true,
  "saveLocal": true,
  "outputPath": "C:/data/out_graph.json"
}
```

## 注意事项

- 单位/姿态换算由服务端完成，本技能不做数学换算
- 若设备名称歧义，优先报错要求用户明确 ID，不要臆造
- `class` 必须与 registry YAML full key 完全一致
- 上传链路必须自动发现并使用 `layout_optimizer.config*.json`
- 本技能仅服务于 rail 新链路，不走 `/optimize*`
