---
name: edit-experiment-graph
description: Guide for creating and editing experiment graph files in Uni-Lab-OS (创建/编辑实验组态图). Covers node types, link types, parent-child relationships, deck configuration, and common graph patterns. Use when the user wants to create a graph file, edit an experiment configuration, or mentions 图文件/graph/组态/拓扑/实验图/experiment JSON.
---

# 创建/编辑实验组态图

实验图（Graph File）定义设备拓扑、物理连接和物料配置。系统启动时加载图文件，初始化所有设备和连接关系。

路径：`unilabos/test/experiments/<name>.json`

> 图文件中的 `class` 字段对应设备/资源的注册表 ID。使用 `@device(id=...)` 或 `@resource(id=...)` 装饰器注册的设备/资源，`class` 填写装饰器中指定的 `id`。

---

## JSON 顶层结构

```json
{
    "nodes": [],
    "links": []
}
```

---

## 节点定义

### 字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `id` | string | **是** | 节点唯一标识 |
| `class` | string | **是** | `@device(id=...)` 中的 id，或 YAML 注册表 key |
| `type` | string | 否 | `"device"` / `"deck"` / `"container"` |
| `name` | string | 否 | 显示名称 |
| `children` | string[] | 否 | 子节点 ID 列表 |
| `parent` | string\|null | 否 | 父节点 ID |
| `config` | object | 否 | 传给 `__init__` 的 `config` 参数 |
| `data` | object | 否 | 初始运行状态 |

### 设备节点

```json
{
    "id": "my_device",
    "name": "我的设备",
    "type": "device",
    "class": "my_device_id",
    "config": {"port": "/dev/ttyUSB0", "baudrate": 115200},
    "data": {"status": "Idle"}
}
```

### 容器节点

```json
{
    "id": "flask_DMF",
    "name": "DMF试剂瓶",
    "type": "container",
    "class": null,
    "parent": "my_station",
    "config": {"max_volume": 1000.0},
    "data": {"liquid": [{"liquid_type": "DMF", "liquid_volume": 800.0}]}
}
```

---

## 连接定义

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | string | 源节点 ID |
| `target` | string | 目标节点 ID |
| `type` | string | `"physical"` / `"fluid"` / `"communication"` |
| `port` | object | 端口映射 |

---

## 常见图模式

### 模式 A：单设备调试

```json
{
    "nodes": [
        {"id": "my_device", "class": "my_device_id", "type": "device",
         "config": {"port": "/dev/ttyUSB0"}}
    ],
    "links": []
}
```

### 模式 B：Protocol 工作站

```json
{
    "nodes": [
        {"id": "station", "class": "workstation", "type": "device",
         "children": ["pump", "valve", "flask"],
         "config": {"protocol_type": ["PumpTransferProtocol"]}},
        {"id": "pump", "class": "virtual_transfer_pump", "parent": "station"},
        {"id": "valve", "class": "virtual_multiway_valve", "parent": "station"},
        {"id": "flask", "type": "container", "class": null, "parent": "station"}
    ],
    "links": [
        {"source": "pump", "target": "valve", "type": "fluid",
         "port": {"pump": "transferpump", "valve": "transferpump"}}
    ]
}
```

### 模式 C：工作站 + Deck

```json
{
    "nodes": [
        {"id": "my_station", "class": "my_workstation",
         "children": ["my_deck"],
         "deck": {"data": {"_resource_child_name": "my_deck",
                           "_resource_type": "unilabos.resources.module:MyDeck"}}},
        {"id": "my_deck", "class": "MyDeck", "parent": "my_station",
         "type": "deck", "config": {"type": "MyDeck", "setup": true}}
    ]
}
```

---

## 父子关系规则

- `children` 和 `parent` 必须双向一致
- 子设备的 `parent` 必须指向工作站节点的 `id`
- Deck 节点的 `_resource_child_name` 必须与 Deck 节点 `id` 一致

---

## 验证

```bash
unilab -g unilabos/test/experiments/<name>.json
```

---

## 常见错误

| 错误 | 修复 |
|------|------|
| `class` 找不到 | 确认 `@device(id=...)` / `@resource(id=...)` 中的 id 或 YAML key |
| children/parent 不一致 | 确保双向一致 |
| `_resource_child_name` 不匹配 | 必须与 Deck 节点 `id` 一致 |

详见 [reference.md](reference.md)：ResourceDict schema、Pose 标准化、Handle 验证、GraphML 格式。
