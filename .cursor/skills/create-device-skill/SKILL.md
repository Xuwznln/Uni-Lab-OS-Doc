---
name: create-device-skill
description: Create a skill for any Uni-Lab device by extracting action schemas from the device registry. Use when the user wants to create a new device skill, add device API documentation, or set up action schemas for a device.
---

# 创建设备 Skill 指南

本 meta-skill 教你如何为任意 Uni-Lab-OS 设备创建完整的 API 操作技能（参考 `unilab-device-api` 的成功案例）。

## 数据源

- **设备注册表**: `unilabos_data/req_device_registry_upload.json`
- **结构**: `{ "resources": [{ "id": "<device_id>", "class": { "module": "<python_module:ClassName>", "action_value_mappings": { ... } } }] }`
- **生成时机**: `unilab` 启动并完成注册表上传后自动生成
- **module 字段**: 格式 `unilabos.devices.xxx.yyy:ClassName`，可转为源码路径 `unilabos/devices/xxx/yyy.py`，阅读源码可了解参数含义和设备行为

## 创建流程

### Step 0 — 收集必备信息（缺一不可，否则询问后终止）

开始前**必须**确认以下 4 项信息全部就绪。如果用户未提供任何一项，**立即询问并终止当前流程**，等用户补齐后再继续。

向用户提问：「请提供你的 unilab 启动参数，我需要以下信息：」

#### 必备项 ①：ak / sk（认证凭据）

来源：启动命令的 `--ak` `--sk` 参数，或 config.py 中的 `ak = "..."` `sk = "..."`。

获取后立即生成 AUTH token：

```bash
python ./scripts/gen_auth.py <ak> <sk>
# 或从 config.py 提取
python ./scripts/gen_auth.py --config <config.py>
```

认证算法：`base64(ak:sk)` → `Authorization: Lab <token>`

#### 必备项 ②：--addr（目标环境）

决定 API 请求发往哪个服务器。从启动命令的 `--addr` 参数获取：

| `--addr` 值 | BASE URL |
|-------------|----------|
| `test` | `https://uni-lab.test.bohrium.com` |
| `uat` | `https://uni-lab.uat.bohrium.com` |
| `local` | `http://127.0.0.1:48197` |
| 不传（默认） | `https://uni-lab.bohrium.com` |
| 其他自定义 URL | 直接使用该 URL |

#### 必备项 ③：req_device_registry_upload.json（设备注册表）

数据文件由 `unilab` 启动时自动生成，需要定位它：

**推断 working_dir**（即 `unilabos_data` 所在目录）：

| 条件 | working_dir 取值 |
|------|------------------|
| 传了 `--working_dir` | `<working_dir>/unilabos_data/`（若子目录已存在则直接用） |
| 仅传了 `--config` | `<config 文件所在目录>/unilabos_data/` |
| 都没传 | `<当前工作目录>/unilabos_data/` |

**按优先级搜索文件**：

```
<推断的 working_dir>/unilabos_data/req_device_registry_upload.json
<推断的 working_dir>/req_device_registry_upload.json
<workspace 根目录>/unilabos_data/req_device_registry_upload.json
```

也可以直接 Glob 搜索：`**/req_device_registry_upload.json`

找到后**必须检查文件修改时间**并告知用户：「找到注册表文件 `<路径>`，生成于 `<时间>`。请确认这是最近一次启动生成的。」超过 1 天提醒用户是否需要重新启动 `unilab`。

**如果文件不存在** → 告知用户先运行 `unilab` 启动命令，等日志出现 `注册表响应数据已保存` 后再执行本流程。**终止。**

#### 必备项 ④：目标设备

用户需要明确要为哪个设备创建 skill。可以是设备名称（如「PRCXI 移液站」）或 device_id（如 `liquid_handler.prcxi`）。

如果用户不确定，运行提取脚本列出所有设备供选择：

```bash
python ./scripts/extract_device_actions.py --registry <找到的文件路径>
```

#### 完整示例

用户提供：

```
--ak a1fd9d4e-xxxx-xxxx-xxxx-d9a69c09f0fd
--sk 136ff5c6-xxxx-xxxx-xxxx-a03e301f827b
--addr test
--port 8003
--disable_browser
```

从中提取：
- ✅ ak/sk → 运行 `gen_auth.py` 得到 `AUTH="Authorization: Lab YTFmZDlk..."`
- ✅ addr=test → `BASE=https://uni-lab.test.bohrium.com`
- ✅ 搜索 `unilabos_data/req_device_registry_upload.json` → 找到并确认时间
- ✅ 用户指明目标设备 → 如 `liquid_handler.prcxi`

**四项全部就绪后才进入 Step 1。**

### Step 1 — 列出可用设备

运行提取脚本，列出所有设备及 action 数量和 Python 源码路径，让用户选择：

```bash
# 自动搜索（默认在 unilabos_data/ 和当前目录查找）
python ./scripts/extract_device_actions.py

# 指定注册表文件路径
python ./scripts/extract_device_actions.py --registry <path/to/req_device_registry_upload.json>
```

脚本输出包含每个设备的 **Python 源码路径**（从 `class.module` 转换），可用于后续阅读源码理解参数含义。

### Step 2 — 提取 Action Schema

用户选择设备后，运行提取脚本：

```bash
python ./scripts/extract_device_actions.py [--registry <path>] <device_id> ./skills/<skill-name>/actions/
```

脚本会显示设备的 Python 源码路径和类名，方便阅读源码了解参数含义。

每个 action 生成一个 JSON 文件，包含：
- `type` — 作为 API 调用的 `action_type`
- `schema` — 完整 JSON Schema（含 `properties.goal.properties` 参数定义）
- `goal` — goal 字段映射（含占位符 `$placeholder`）
- `goal_default` — 默认值

### Step 3 — 写 action-index.md

按模板为每个 action 写条目：

```markdown
### `<action_name>`

<用途描述（一句话）>

- **Schema**: [`actions/<filename>.json`](actions/<filename>.json)
- **核心参数**: `param1`, `param2`（从 schema.required 获取）
- **可选参数**: `param3`, `param4`
- **占位符字段**: `field`（需填入物料信息，值以 `$` 开头）
```

描述规则：
- 从 `schema.properties` 读参数列表（schema 已提升为 goal 内容）
- 从 `schema.required` 区分核心/可选参数
- 按功能分类（移液、枪头、外设等）
- 标注 `placeholder_keys` 中的字段类型：
  - `unilabos_resources` → **ResourceSlot**，填入 `{id, name, uuid}`（id 是路径格式，从资源树取物料节点）
  - `unilabos_devices` → **DeviceSlot**，填入路径字符串如 `"/host_node"`（从资源树筛选 type=device）
  - `unilabos_nodes` → **NodeSlot**，填入路径字符串如 `"/PRCXI/PRCXI_Deck"`（资源树中任意节点）
  - `unilabos_class` → **ClassSlot**，填入类名字符串如 `"container"`（从注册表查找）
- array 类型字段 → `[{id, name, uuid}, ...]`
- 特殊：`create_resource` 的 `res_id`（ResourceSlot）可填不存在的路径

### Step 4 — 写 SKILL.md

直接复用 `unilab-device-api` 的 API 模板（10 个 endpoint），修改：
- 设备名称
- Action 数量
- 目录列表
- Session state 中的 `device_name`
- **AUTH 头** — 使用 Step 0 中 `gen_auth.py` 生成的 `Authorization: Lab <token>`（不要硬编码 `Api` 类型的 key）
- **Python 源码路径** — 在 SKILL.md 开头注明设备对应的源码文件，方便参考参数含义
- **Slot 字段表** — 列出本设备哪些 action 的哪些字段需要填入 Slot（物料/设备/节点/类名）

API 模板结构：

```markdown
## 设备信息
- device_id, Python 源码路径, 设备类名

## 前置条件（缺一不可）
- ak/sk → AUTH, --addr → BASE URL

## Session State
- lab_uuid（通过 API #1 自动匹配，不要问用户）, device_name

## API Endpoints (10 个)
# 注意：
# - #1 获取 lab 列表 + 自动匹配 lab_uuid（遍历 is_admin 的 lab，
#   调用 /lab/info/{uuid} 比对 access_key == ak）
# - #2 创建工作流用 POST /lab/workflow
# - #10 获取资源树路径含 lab_uuid: /lab/material/download/{lab_uuid}

## Placeholder Slot 填写规则
- unilabos_resources → ResourceSlot → {"id":"/path/name","name":"name","uuid":"xxx"}
- unilabos_devices → DeviceSlot → "/parent/device" 路径字符串
- unilabos_nodes → NodeSlot → "/parent/node" 路径字符串
- unilabos_class → ClassSlot → "class_name" 字符串
- 特例：create_resource 的 res_id 允许填不存在的路径
- 列出本设备所有 Slot 字段、类型及含义

## 渐进加载策略
## 完整工作流 Checklist
```

### Step 5 — 验证

检查文件完整性：
- [ ] `SKILL.md` 包含 10 个 API endpoint
- [ ] `SKILL.md` 包含 Placeholder Slot 填写规则（ResourceSlot / DeviceSlot / NodeSlot / ClassSlot + create_resource 特例）和本设备的 Slot 字段表
- [ ] `action-index.md` 列出所有 action 并有描述
- [ ] `actions/` 目录中每个 action 有对应 JSON 文件
- [ ] JSON 文件包含 `type`, `schema`（已提升为 goal 内容）, `goal`, `goal_default`, `placeholder_keys` 字段
- [ ] 描述能让 agent 判断该用哪个 action

## Action JSON 文件结构

```json
{
  "type": "LiquidHandlerTransfer",    // → API 的 action_type
  "goal": {                           // goal 字段映射
    "sources": "sources",
    "targets": "targets",
    "tip_racks": "tip_racks",
    "asp_vols": "asp_vols"
  },
  "schema": {                         // ← 直接是 goal 的 schema（已提升）
    "type": "object",
    "properties": {                   // 参数定义（即请求中 goal 的字段）
      "sources": { "type": "array", "items": { "type": "object" } },
      "targets": { "type": "array", "items": { "type": "object" } },
      "asp_vols": { "type": "array", "items": { "type": "number" } }
    },
    "required": [...],
    "_unilabos_placeholder_info": {   // ← Slot 类型标记
      "sources": "unilabos_resources",
      "targets": "unilabos_resources",
      "tip_racks": "unilabos_resources"
    }
  },
  "goal_default": { ... },            // 默认值
  "placeholder_keys": {               // ← 汇总所有 Slot 字段
    "sources": "unilabos_resources",  //    ResourceSlot
    "targets": "unilabos_resources",
    "tip_racks": "unilabos_resources",
    "target_device_id": "unilabos_devices"  // DeviceSlot
  }
}
```

> **注意**：`schema` 已由脚本从原始 `schema.properties.goal` 提升为顶层，直接包含参数定义。
> `schema.properties` 中的字段即为 API 请求 `param.goal` 中的字段。

## Placeholder Slot 类型体系

`placeholder_keys` / `_unilabos_placeholder_info` 中有 4 种值，对应不同的填写方式：

| placeholder 值 | Slot 类型 | 填写格式 | 选取范围 |
|---------------|-----------|---------|---------|
| `unilabos_resources` | ResourceSlot | `{"id": "/path/name", "name": "name", "uuid": "xxx"}` | 仅**物料**节点（不含设备） |
| `unilabos_devices` | DeviceSlot | `"/parent/device_name"` | 仅**设备**节点（type=device），路径字符串 |
| `unilabos_nodes` | NodeSlot | `"/parent/node_name"` | **设备 + 物料**，即所有节点，路径字符串 |
| `unilabos_class` | ClassSlot | `"class_name"` | 注册表中已上报的资源类 name |

### ResourceSlot（`unilabos_resources`）

最常见的类型。从资源树中选取**物料**节点（孔板、枪头盒、试剂槽等）：

```json
{"id": "/workstation/container1", "name": "container1", "uuid": "ff149a9a-2cb8-419d-8db5-d3ba056fb3c2"}
```

- 单个（schema type=object）：`{"id": "/path/name", "name": "name", "uuid": "xxx"}`
- 数组（schema type=array）：`[{"id": "/path/a", "name": "a", "uuid": "xxx"}, ...]`
- `id` 本身是从 parent 计算的路径格式
- 根据 action 语义选择正确的物料（如 `sources` = 液体来源，`targets` = 目标位置）

> **特例**：`create_resource` 的 `res_id` 字段，目标物料可能**尚不存在**，此时直接填写期望的路径（如 `"/workstation/container1"`），不需要 uuid。

### DeviceSlot（`unilabos_devices`）

填写**设备路径字符串**。从资源树中筛选 type=device 的节点，从 parent 计算路径：

```
"/host_node"
"/bioyond_cell/reaction_station"
```

- 只填路径字符串，不需要 `{id, uuid}` 对象
- 根据 action 语义选择正确的设备（如 `target_device_id` = 目标设备）

### NodeSlot（`unilabos_nodes`）

范围 = 设备 + 物料。即资源树中**所有节点**都可以选，填写**路径字符串**：

```
"/PRCXI/PRCXI_Deck"
```

- 使用场景：当参数既可能指向物料也可能指向设备时（如 `PumpTransferProtocol` 的 `from_vessel`/`to_vessel`，`create_resource` 的 `parent`）

### ClassSlot（`unilabos_class`）

填写注册表中已上报的**资源类 name**。从本地 `req_resource_registry_upload.json` 中查找：

```
"container"
```

### 通过 API #10 获取资源树

```bash
curl -s -X GET "$BASE/api/v1/lab/material/download/$lab_uuid" -H "$AUTH"
```

注意 `lab_uuid` 在路径中（不是查询参数）。资源树返回所有节点，每个节点包含 `id`（路径格式）、`name`、`uuid`、`type`、`parent` 等字段。填写 Slot 时需根据 placeholder 类型筛选正确的节点。

## 最终目录结构

```
./<skill-name>/
├── SKILL.md              # API 端点 + 渐进加载指引
├── action-index.md       # 动作索引：描述/用途/核心参数
└── actions/              # 每个 action 的完整 JSON Schema
    ├── action1.json
    ├── action2.json
    └── ...
```
