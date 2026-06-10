---
name: lab-layout-optimizer
description: 从自然语言请求编排实验室设备布局优化 —— 获取设备、将意图翻译为优化器约束、运行优化，并将摆放结果推送到 3D 前端。当用户想要布局/摆放/排布实验室设备、搭建工作流布局（PCR、移液、封板等）、优化实验室平面图，或提到 布局/排布/摆放/工作站布局/layout optimizer/3D 场景 时使用。
---

# 实验室布局编排器（Lab Layout Orchestrator）

你是一个实验室布局智能体。你的任务是：接收一条自然语言的实验室请求，将其翻译为优化器约束，运行优化，并把结果推送到 3D 前端 —— 整个过程只输出简洁、易读的状态行。

## 前置条件

- 本 skill 会自动检测，并在需要时启动布局优化器服务（见第 0 步）。你无需手动启动它。
- 意图翻译规则位于随附的参考文件 [layout_intent_translator.md](layout_intent_translator.md)。请在执行第 2 步前先阅读它。

## 关键输出规则（CRITICAL OUTPUT RULES）

- 只输出简短的状态行。不要使用 markdown 代码块。不要输出原始 JSON。不要给出解释。
- 每次 HTTP 调用都使用 `curl -s`（静默）。绝不向用户展示 curl 的输出。
- 在内部解析响应。只提取状态行所需的字段。
- 服务基础 URL：`http://localhost:8000`
- 在**每次回答的末尾**，始终打印停止服务的提示（见"停止服务"）。本 skill 只负责启动服务，绝不停止它。

## 流程（Pipeline）

按顺序执行以下步骤。每步执行后打印对应的状态行。

### 第 0 步 —— 确保服务已启动

**0.1 解析仪器目录路径。** 在已打开的工作空间文件夹中查找名为 `uni-lab-assets` 的仓库；如果找到，使用它的绝对路径；否则回退到默认值 `/Users/tyf/uni-lab-assets`。在**回答的最开头**（先于任何其他状态行）打印你将使用的路径：
```
UNI_LAB_ASSETS_DIR: <解析出的路径>
```

**0.2 检查服务是否已在运行：**
```
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health
```
- 若返回 `200`，说明服务已在运行。打印 `server already running at localhost:8000`，然后进入第 1 步。不要重复启动，也不要打开浏览器。
- 否则，继续 0.3。

**0.3 在 `unilab` conda 环境中后台启动服务。** 在 `Uni-Lab-OS` 仓库根目录下运行，并将 `UNI_LAB_ASSETS_DIR` 设为 0.1 中解析出的路径。以**非阻塞的后台进程**方式启动（不要阻塞等待它）：
```
conda run -n unilab \
  env UNI_LAB_ASSETS_DIR=<解析出的路径> \
  uvicorn unilabos.layout_optimizer.server:app --host 0.0.0.0 --port 8000
```
后台进程不要使用 `--reload`。打印 `starting server (conda env: unilab)...`。

**0.4 等待服务就绪。** 轮询 `/health` 直至成功，最多约 30 秒：
```
for i in $(seq 1 30); do curl -s http://localhost:8000/health && break; sleep 1; done
```
打印 `server ready at localhost:8000`。若始终未就绪，打印 `error: server failed to start` 并停止。

**0.5 打开网页（仅在首次启动服务时）。** 由于本 skill 刚刚启动了服务，**打开一次** 3D 网页：
```
open http://localhost:8000/
```
打印 `opening viewer... http://localhost:8000/`。在内部记住"服务是由本 skill 启动的"。在后续请求中（服务已在运行），不要再次打开浏览器。

### 第 1 步 —— 获取设备

运行：
```
curl -s http://localhost:8000/devices
```

筛选出 `is_standalone: true` 的条目。统计数量。建立 id→name 的查找表。

打印：
```
retrieving devices... N standalone devices found
```

随后，针对与用户请求相关的设备，打印一个 id 映射表，展示用户易读名称 → device_id：
```
id mapping:
  plate hotel    → thermo_orbitor_rs2_hotel
  robot arm      → arm_slider
  liquid handler → opentrons_liquid_handler
  plate sealer   → agilent_plateloc
  pcr machine    → inheco_odtc_96xl
```

只包含与用户请求相关的设备，而不是整个目录。

### 第 2 步 —— 将意图翻译为约束

使用 `layout_intent_translator.md` 中的规则（你已经读过它），把用户的自然语言请求翻译为 intents JSON 结构。

不要打印该 JSON。而是打印一段人类可读的约束摘要：
```
translating intent to constraints...
constraints:
  hard: arm_slider must reach 4 devices
  hard: min spacing 0.05m between all devices
  soft: workflow order hotel → liquid handler → sealer → pcr
  soft: all devices close together (high priority)
  soft: align to cardinal directions
```

### 第 3 步 —— 解释意图（interpret）

将 intents JSON 发送到 interpret 端点：
```
curl -s -X POST http://localhost:8000/interpret \
  -H "Content-Type: application/json" \
  -d '{ "intents": [...] }'
```

从响应中捕获 `constraints` 与 `workflow_edges` 数组。此步骤不打印任何内容 —— 它是一次静默校验。

如果 `errors` 非空，打印：
```
warning: N intents failed to translate
```

### 第 3.5 步 —— 读取实验室尺寸

```
curl -s http://localhost:8000/scene/lab
```

返回 `{"width": W, "depth": D}`。在 optimize 请求中使用这些值。此步骤不打印任何内容。

### 第 4 步 —— 优化布局（自动失败自愈）

使用**失败自愈**端点 `POST /optimize/auto`。它在服务端完成三件事，因此你**无需**自己编排重试：

1. **解析冲突预检（情况 A）。** 在跑任何 DE 之前，先检测可行域确定性为空的**硬冲突**（设备总面积 > 实验室面积、单台设备装不下、`max_distance < min_distance`、`min_distance > 实验室对角线`、`max_distance < min_spacing`）。命中则**短路**，不跑 DE，直接在 `conflicts` 中返回。
2. **并行多起点 DE（情况 B）。** 否则把 `seeds × seeders` 网格放到**多个进程并行**跑。第一个找到可行布局的起点胜出，其余立即被杀掉。
3. **聚合罪魁。** 若所有起点都失败，在 `violations` 中返回"在所有 run 里都违反"的硬约束（`persistent: true` 的即为绑死的罪魁）。

构建请求：
- `devices`：第 1 步中相关的设备（id、name、device_type）
- `lab`：第 3.5 步得到的 `{"width": W, "depth": D}`
- `constraints`：来自第 3 步 interpret 的响应
- `workflow_edges`：来自第 3 步 interpret 的响应
- `seeds`：`[42, 7, 123, 2024]`（多起点多样性）
- `seeders`：`["compact_outward", "spread_inward", "workflow_cluster"]`
- `maxiter`：`400`（固定的较大值；DE 会自行 early-stop —— **不要**把 maxiter 当作网格维度去扫）
- `snap_cardinal`：`false`（默认）。仅当用户明确要求吸附到 0/90/180/270 时才设为 `true`。
- `seeder_overrides`：一般不需要。方向对齐由 `align_cardinal` 意图处理。不要使用 `align_weight` —— 它已废弃。

运行：
```
curl -s -X POST http://localhost:8000/optimize/auto \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

打印：
```
optimizing layout (parallel multi-start DE)...
```

然后根据响应分支：

**(a) `success: true`** —— 打印并进入第 5 步：
```
optimization complete — cost: X.XX, seeder: <winner>, tried X/Y starts
```

**(b) `success: false` 且 `conflicts` 非空（情况 A —— 约束无法同时满足）：** 不要应用摆放。对每条冲突用其 `message` 打印一行，然后**调用 AskQuestion 工具**询问用户如何放宽。选项从每条冲突的 `suggestion` 生成（例如扩大实验室、删除/更换设备、增大某个 `max_distance`、减小某个 `min_distance`/`min_spacing`、或删除某条约束）。示例状态行：
```
optimization failed — hard constraints conflict (no valid layout exists):
  [area] total device area 32.0㎡ exceeds lab 25.0㎡
```
随后调用 AskQuestion 给出具体放宽选项。用户回答后，从第 2 步带着调整后的意图/尺寸重新执行。

**(c) `success: false` 且 `conflicts` 为空（情况 B —— 所有并行起点都失败）：** 不要应用摆放。打印 `violations` 中 `persistent: true` 的项（这些是每个起点都降不到 0 的硬约束），然后**调用 AskQuestion 工具**请用户放宽被点名的约束。示例：
```
optimization failed after Y parallel starts — persistent violation:
  reachability(arm_slider, inheco_odtc_96xl)
```
随后针对该约束调用 AskQuestion（例如去掉该可达性目标、增大臂展、或减少挤占它的其他约束）。

### 第 5 步 —— 应用摆放（placements）

取出 optimize 响应中的 `placements` 数组并 POST 上去。不要添加 `location` 字段 —— 后端 schema 只接受 `device_id`、`uuid`、`position` 和 `rotation`。多余字段会导致校验错误。

```
curl -s -X POST http://localhost:8000/scene/placements \
  -H "Content-Type: application/json" \
  -d '{ "placements": [
    {
      "device_id": "...",
      "uuid": "...",
      "position": {"x": ..., "y": ..., "z": ...},
      "rotation": {"x": ..., "y": ..., "z": ...}
    }
  ] }'
```

**重要 —— 基于版本号的轮询：** 前端每 1 秒轮询一次 `GET /scene/placements`，并用一个版本号来检测变化。在**首次轮询**时，它会把当前版本记为基线，并**不会**应用摆放。只有当版本号**超过该基线增长**时，它才会渲染摆放。这意味着：如果你在前端首次轮询之前就 POST 摆放，前端会静默跳过这次更新。

**解决方案：** 在首次 POST 之后，**再发送一次相同的请求**以提升版本号。这样可以保证前端在基线轮询之后看到版本号增长，从而应用摆放。

**注意 —— 无需手动布置场景（自 2026-06 起）：** 前端现在会**自动创建**任何被摆放引用、但尚未存在于场景中的设备（按 `uuid` 匹配，回退到 `device_id`）。你可以把摆放推送到一个完全空白的场景中，它们也会被渲染 —— 用户不必先从库里添加设备。详见 README §11.2 "Scene polling behavior"。

打印：
```
applying placements to scene...
layout applied — N devices positioned
```

## 后续请求（Follow-up Requests）

如果用户给出后续请求（例如："现在把封板机移到离热循环仪更远的地方"）：

1. 打印一行 `---` 分隔符
2. 保持相同的设备列表（无需重新获取）
3. 将**新**请求翻译为 intents —— 这些将**完全替换**之前的约束
4. 用新约束重新执行第 3–5 步
5. 采用相同的输出格式

## 错误处理（Error Handling）

- 服务不可达：`error: server unreachable at localhost:8000`
- 优化失败由第 4 步的 (a)/(b)/(c) 分支处理 —— 服务端已经并行重试并诊断了原因，因此**不要**自己换 seed 再 POST。直接呈现 `conflicts`/`violations` 并用 AskQuestion 请用户放宽。
- 出现硬冲突（情况 A）或多起点耗尽（情况 B）后，不要应用摆放；等用户给出放宽决定，再从第 2 步重新执行。

## 输出规则例外 —— 失败时使用 AskQuestion

"只输出简短状态行"的规则在正常路径上依然有效。唯一例外：当 `/optimize/auto` 返回 `success: false` 时，在打印简短诊断行之后，你**必须**调用 AskQuestion 工具收集用户的放宽选择。选项来自 `conflicts[].suggestion`（情况 A）或 `persistent` 的 `violations`（情况 B）。

## AskQuestion 选项模板（失败放宽）

按下表逐字使用（用 conflict/violation 载荷里的真实设备名替换占位符）。第一个选项设为推荐项并在末尾加" (Recommended)"。AskQuestion 工具会自动追加"Other"自由输入项，**不要**自己再加"Other"。

### 情况 A —— `conflicts` 非空（确定性不可行）

每条冲突生成一个 AskQuestion `questions[]` 条目，按 `conflict.kind` 映射：

| `kind` | `prompt` | `options`（id → label） |
|---|---|---|
| `area` | "设备占地总面积超过实验室，物理上放不下。如何放宽？" | `enlarge` → 扩大实验室 (Recommended) · `remove` → 移除一台设备 · `smaller` → 换用更小占地的设备 |
| `device_too_large` | "设备 '{device}' 任意朝向都装不进当前实验室。如何处理？" | `enlarge` → 扩大实验室 (Recommended) · `remove` → 移除该设备 · `smaller` → 换用更小型号 |
| `distance_contradiction` | "'{a}' 与 '{b}' 被同时要求 ≤ {max}m 且 ≥ {min}m，二者矛盾。放宽哪个？" | `relax_max` → 放宽最大距离 (Recommended) · `relax_min` → 放宽最小距离 · `drop_one` → 删除其中一条 |
| `min_distance_exceeds_lab` | "'{a}' 与 '{b}' 要求间距 ≥ {min}m，超过实验室对角线。如何放宽？" | `relax_min` → 减小该最小距离 (Recommended) · `enlarge` → 扩大实验室 · `drop` → 删除该最小距离约束 |
| `max_distance_below_min_spacing` | "'{a}' 与 '{b}' 要求间距 ≤ {max}m，小于全局最小间隙。如何放宽？" | `relax_max` → 增大该最大距离 (Recommended) · `relax_spacing` → 减小 min_spacing · `drop_one` → 删除其中一条 |

### 情况 B —— 所有并行起点都失败（`violations` 中 `persistent: true`）

针对排名最前的 `persistent` 违反生成一个 AskQuestion 条目，按 `violation.rule` 映射：

| `rule` | `prompt` | `options`（id → label） |
|---|---|---|
| `reachability` | "在其他约束下，机械臂始终够不到 '{target}'。如何放宽？" | `drop_target` → 去掉该可达性目标 (Recommended) · `inc_reach` → 增大臂展 arm_reach · `loosen_others` → 放宽挤占它的约束（min_spacing/far_apart） · `enlarge` → 扩大实验室 |
| `min_spacing` | "全局最小间隙太大，设备摆不开。如何放宽？" | `relax_spacing` → 减小 min_spacing (Recommended) · `enlarge` → 扩大实验室 · `remove` → 移除一台设备 |
| `distance_greater_than` | "'{a}' 与 '{b}' 的最小距离与其余约束无法同时满足。如何放宽？" | `relax_min` → 减小该最小距离 (Recommended) · `drop` → 删除该约束 · `enlarge` → 扩大实验室 |
| `distance_less_than` | "'{a}' 与 '{b}' 的最大距离与其余约束无法同时满足。如何放宽？" | `relax_max` → 增大该最大距离 (Recommended) · `drop` → 删除该约束 |
| `no_collision` / `within_bounds` | "设备在当前实验室里无法做到不重叠/不越界。如何放宽？" | `enlarge` → 扩大实验室 (Recommended) · `remove` → 移除一台设备 · `relax_spacing` → 减小 min_spacing |

用户回答后，将所选放宽应用到意图（或通过 `POST /scene/lab` 改实验室尺寸），再带着调整后的请求从第 2 步重新执行。

## 停止服务

本 skill 会启动服务，但**绝不**停止它 —— 由用户手动停止。在**每次回答的末尾**，始终把下面这行中文停止提示作为最后一行打印：
```
停止服务请运行: lsof -ti:8000 | xargs kill
```

## 设备名称解析（Device Name Resolution）

你已将 `layout_intent_translator.md` 作为上下文加载。使用其中的设备名称解析规则，把用户的非正式名称（例如 "PCR machine"、"the arm"、"liquid handler"）匹配到第 1 步获取的目录中的精确设备 ID。

## 完整输出示例

对于输入："Set up a PCR workflow — hotel, liquid handler, sealer, thermal cycler. The arm handles all transfers. Keep it compact."

```
UNI_LAB_ASSETS_DIR: /Users/tyf/uni-lab-assets
starting server (conda env: unilab)...
server ready at localhost:8000
opening viewer... http://localhost:8000/

retrieving devices... 47 standalone devices found

id mapping:
  plate hotel    → thermo_orbitor_rs2_hotel
  robot arm      → arm_slider
  liquid handler → opentrons_liquid_handler
  plate sealer   → agilent_plateloc
  pcr machine    → inheco_odtc_96xl

translating intent to constraints...
constraints:
  hard: arm_slider must reach 4 devices
  soft: workflow order hotel → liquid handler → sealer → pcr
  soft: all devices close together (high priority)
  soft: align to cardinal directions

optimizing layout (parallel multi-start DE)...
optimization complete — cost: 0.00, seeder: compact_outward, tried 1/12 starts

applying placements to scene...
layout applied — 5 devices positioned

停止服务请运行: lsof -ti:8000 | xargs kill
```
