---
name: rail-layout
description: 为线性（单实例）实验流程编排确定性的导轨机械臂实验室布局——把用户流程里的设备解析到目录 footprint、做可行性检查、解析式计算机械臂/堆栈/仪器坐标，并报告每台设备在实验室中的位置 + 朝向（仅 0/90/180/270）。不推送到 3D 前端。当用户想要布置导轨/龙门机械臂工作站、让仪器按工作流顺序围绕机械臂排布，或提到 导轨机械臂/导轨布局/机械臂周围布仪器/线性流程布局/rail layout 时使用。
---

# 导轨机械臂布局编排器

你为导轨机械臂实验室编排**确定性解析布局**：机械臂沿长边墙摆放，堆栈夹在相邻机械臂之间，仪器按工作流顺序围绕每台机械臂装箱。这是完全确定性的布局——**不跑任何搜索/优化**，坐标完全由距离参数解析算出。

> 适用范围：**无多台同类型仪器的线性实验流程**（一种仪器只有一台）。

## 本技能的交付物

**最终交付物是坐标表**——对每台仪器、机械臂、堆栈，给出它在实验室中的位置 `(x, y, z)` 和朝向，**朝向只能取 0 / 90 / 180 / 270 度**。本技能**不**把布局推送到 3D 前端，因此没有 `POST /scene/placements` 步骤，也不打开 viewer。

## 前置条件

- 设备名称解析规则已打包在 [device_name_resolution_zh.md](device_name_resolution_zh.md)。**在 Step 1 之前先读它**——它规定了如何把用户流程里的设备（非正式名称）匹配到精确的目录 footprint ID，以及如何把它们拆分为 机械臂 / 堆栈 / 有序仪器。
- 本技能会自动检测，必要时启动布局服务（仅用于计算，见 Step 0）。

## 关键输出规则

- 流程执行期间只输出简短状态行。不要 markdown 代码块，不要原始 JSON，不要解释。
- 每次 HTTP 调用都用 `curl -s`（静默）。绝不向用户展示 curl 输出。
- 内部解析响应，只取状态行所需字段。
- 服务基址：`http://localhost:8000`
- 唯一允许的丰富输出是 Step 5 的**最终坐标表**。
- 每次回复结尾打印停止服务提示（见"停止服务"）。

## 默认参数与用户覆盖

下面每个参数都有默认值，因此技能零配置即可运行。但**用户可以覆盖其中任意一个**——若用户给了自己的取值，就在对应的请求字段里透传；若用户没说，就省略该字段、由服务端使用默认值。始终以用户给出的值优先于默认值。

在 Step 3/4 之前，先判断用户是否提到了自定义距离、特定机械臂型号/工作半径、或特定堆栈型号，并把它们分别路由到下面正确的请求字段。

### 距离参数 → 请求字段 `params`（单位 m）

默认值（`rail_layout.DEFAULT_PARAMS`）：
- `a=0.5` 机械臂短侧到墙 · `b=0.2` 机械臂长侧到仪器 · `c=0.3` 仪器间距 · `d=0.3` 仪器到墙 · `e=0.2` 机械臂到堆栈

只覆盖用户改动的键，例如 `"params": {"b": 0.25, "c": 0.4}`。硬性可达性约定：`b < 工作半径` 且 `e < 工作半径`（可行性检查会强制校验）。

### 机械臂工作半径 / 型号 → 请求字段 `arm_model`

- 工作半径默认 `0.3m`；导轨长度 `L` 和 bbox 否则从 `/devices` 推断。
- 若用户给了特定的机械臂型号、工作半径、导轨长度或 bbox，传 `"arm_model": {"L": <m>, "working_radius": <m>, "bbox": [w, d]}`（只填你知道的键）。用户给的 `working_radius` 也放这里（或放 `params.working_radius`）。
- TODO：将来用按型号查表的 `rail_arm_models.json` 替换 `0.3m` 默认值。

### 堆栈型号 → 请求字段 `stack_model`

- 默认 `thermo_stacker`（真实 bbox/openings 取自 `footprints.json`）。
- 若用户指定了别的堆栈，传 `"stack_model": "<footprint_id>"`，或直接给几何 `"stack_model": {"bbox": [w, d], "openings": [...]}`。
- 可选的 footprint 堆栈包括 `thermo_orbitor_rs2_stack`、`hamilton_entry_exit_stacker`、`tecan_carousel_stacker_6/10/25`、`thermo_cytomat2c_stacker_15/21`、`highres_bio_random_access_stacker_12`。

## 流程

### Step 0 — 确保服务运行（仅计算）

解析 `UNI_LAB_ASSETS_DIR`（在已打开的工作区文件夹中找名为 `uni-lab-assets` 的，找不到则回落 `/Users/tyf/uni-lab-assets`），并最先打印：
```
UNI_LAB_ASSETS_DIR: <解析出的路径>
```
检查 `GET /health`。若返回 `200`，打印 `server already running at localhost:8000`。否则在 `unilab` conda 环境中以**非阻塞后台**方式启动，并轮询直至就绪：
```
conda run -n unilab \
  env UNI_LAB_ASSETS_DIR=<解析出的路径> \
  uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000
```
```
for i in $(seq 1 30); do curl -s http://localhost:8000/health && break; sleep 1; done
```
打印 `server ready at localhost:8000`。**不要**打开浏览器/viewer（本技能不涉及前端）。

### Step 1 — 拉取设备并解析名称

```
curl -s http://localhost:8000/devices
```
按 `device_name_resolution_zh.md` 的规则，把用户流程解析为三组：
- **机械臂（arm）**——导轨机械臂（`device_type: articulation` 或关键词命中）。
- **堆栈（stack）**——默认 `thermo_stacker`，或用户指定型号。
- **有序仪器（ordered_instruments）**——其余仪器，**保持工作流顺序**（顺序很重要）。

打印简洁的解析表（只列相关设备）：
```
resolving devices...
  arm    → arm_slider
  stack  → thermo_stacker (default)
  flow   → thermo_orbitor_rs2_hotel → opentrons_liquid_handler → agilent_plateloc → inheco_odtc_96xl
```
若某个名称有歧义，用 **AskQuestion** 工具澄清，不要猜。

### Step 2 — 实验室尺寸

使用用户给的尺寸。若未给出，读取当前场景：
```
curl -s http://localhost:8000/scene/lab
```
返回 `{"width": W, "depth": D}`。本步不打印任何内容。

### Step 3 — 可行性检查

POST 解析出的设备 + 有序仪器 id + 实验室尺寸（+ 可选 `params` / `arm_model` / `stack_model`）：
```
curl -s -X POST http://localhost:8000/rail/feasibility \
  -H "Content-Type: application/json" \
  -d '{ "devices": [...], "ordered_instruments": ["dev_a", "dev_b", ...], "lab": {"width": W, "depth": D} }'
```
按响应分支：
- `feasible: true` → 打印 `feasibility ok — N arms, M stacks (n_max=K)`，进入 Step 4。
- `feasible: false` → 逐行打印 `reasons[]`，然后用 **AskQuestion** 工具让用户放宽（扩大房间 / 减少仪器 / 更短导轨 / 调参数），选项对应 `suggestions[]`。用户回答后重跑 Step 3。

### Step 4 — 计算布局

```
curl -s -X POST http://localhost:8000/rail/layout \
  -H "Content-Type: application/json" \
  -d '{ "devices": [...], "ordered_instruments": [...], "lab": {"width": W, "depth": D}, "mode": "near_wall" }'
```
`mode` 取 `near_wall`（默认）或 `centered`（仅当用户明确要求且短边不等式 1 成立）。捕获 `placements`（仪器）、`arms`、`stacks`、`conflicts`。多退少补（删空臂 / 末尾剩余则补臂）由服务端内部处理。

若 `conflicts` 非空（如 `unplaced_instruments`、`out_of_bounds`、`obstacle_collision`），逐条打印 `message`，再用 **AskQuestion** 放宽（取自 `suggestion`）。否则打印 `layout computed — N instruments, A arms, S stacks`。

### Step 5 — 报告坐标（最终交付物）

把每个朝向从弧度转为**基本方向角度**（见"朝向归一化"），打印一张覆盖所有仪器 + 机械臂 + 堆栈的表。仪器按工作流顺序，其后是机械臂（arm1..）、堆栈（stack1..）。位置单位 m。

```
布局结果（坐标单位 m，朝向单位 °，仅取 0/90/180/270）:
类型    设备                         x       y      z     rotation
仪器    thermo_orbitor_rs2_hotel    0.40    0.58   0.00   90
仪器    opentrons_liquid_handler    0.40    0.90   0.00   90
...
机械臂  arm1                        1.00    1.00   0.00   0
堆栈    stack1                      1.00    1.90   0.00   0
```

`/rail/layout` 响应的字段来源：
- **仪器** → `placements[].position` 与 `placements[].rotation.z`。
- **机械臂** → `arms[].center`（x, y；z = 0）与 `arms[].theta`。
- **堆栈** → `stacks[].center`（x, y；z = 0）；朝向取机械臂朝向（堆栈占地对齐导轨方向），并归一化到基本方向值。

## 朝向归一化（0 / 90 / 180 / 270）

服务端角度是弧度，且恒为 π/2 的整数倍。转换并吸附：
1. `deg = round(theta_rad * 180 / π)`
2. `deg = ((deg % 360) + 360) % 360`  → 例如把 `-90 → 270`
3. 吸附到 `{0, 90, 180, 270}` 中最近的值（防御性；值本就是基本方向）。

始终把朝向呈现为 `0`、`90`、`180`、`270` 之一。

## 职责划分（脚本 vs agent）

- "哪台臂 / 哪一侧 / 哪些仪器 / 坐标 / 朝向" → 完全确定，由 `rail_layout.py` 计算。不要交给 LLM 决定。
- agent 职责 = 仅高层编排：解析设备名称 → 可行性 → 布局 → 报告坐标。
- 多退少补由服务端在 `/rail/layout` 内部处理；不要手动编排。

## 停止服务

若本技能启动了服务，它**绝不**主动停止。每次回复结尾打印：
```
停止服务请运行: lsof -ti:8000 | xargs kill
```

## 完整输出示例

输入："导轨布局：流程是 板架 → 移液 → 封板 → PCR，arm_slider 负责转运，房间 4×8m"

```
UNI_LAB_ASSETS_DIR: /Users/tyf/uni-lab-assets
server ready at localhost:8000

resolving devices...
  arm    → arm_slider
  stack  → thermo_stacker (default)
  flow   → thermo_orbitor_rs2_hotel → opentrons_liquid_handler → agilent_plateloc → inheco_odtc_96xl

feasibility ok — 1 arms, 0 stacks (n_max=3)
layout computed — 4 instruments, 1 arms, 0 stacks

布局结果（坐标单位 m，朝向单位 °，仅取 0/90/180/270）:
类型    设备                         x       y      z     rotation
仪器    thermo_orbitor_rs2_hotel    0.46    0.84   0.00   90
仪器    opentrons_liquid_handler    0.45    1.55   0.00   90
仪器    agilent_plateloc            3.55    1.30   0.00   270
仪器    inheco_odtc_96xl            3.58    0.93   0.00   270
机械臂  arm1                        2.00    1.00   0.00   0

停止服务请运行: lsof -ti:8000 | xargs kill
```

## 边界

- 本技能只使用确定性端点：`GET /devices`、`GET /scene/lab`、`POST /rail/feasibility`、`POST /rail/layout`、可选 `POST /rail/validate`。
- **不要**调用任何 `/optimize*` 端点——本技能解析式计算坐标，不跑任何搜索。
