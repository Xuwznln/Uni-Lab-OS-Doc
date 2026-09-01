# 设备图文件

设备图描述实验室中的设备、资源层级和连接关系。节点使用
`unilabos.resources.objects.resource.ResourceDict` 作为规范快照模型，
`unilabos.resources.resource_tracker.ResourceDictInstance` 提供运行时树行为。

## 支持的文件格式

Uni-Lab-OS 支持 JSON 和 GraphML。JSON 由 Python 标准库严格解析，因此不能包含
注释或尾随逗号。GraphML 适合通过 yEd 等图形化工具编辑。

启动时通过 `-g` 指定设备图：

```bash
unilab -g path/to/device_graph.json
```

## JSON 结构

JSON 顶层包含 `nodes` 和可选的 `links`：

```json
{
  "nodes": [
    {
      "id": "workstation_1",
      "uuid": "79a4cbaa-f053-4f0f-b186-9dca9f5fbd58",
      "name": "主工作站",
      "type": "device",
      "class": "workstation",
      "config": {},
      "data": {},
      "extra": {},
      "pose": {
        "position": {"x": 0, "y": 0, "z": 0}
      }
    },
    {
      "id": "reactor_1",
      "uuid": "f2d9082c-c4d9-46d9-bb92-4dc8614158c0",
      "name": "反应器",
      "type": "device",
      "class": "reactor",
      "parent": "workstation_1",
      "parent_uuid": "79a4cbaa-f053-4f0f-b186-9dca9f5fbd58",
      "config": {},
      "data": {},
      "extra": {},
      "pose": {
        "position": {"x": 120, "y": 0, "z": 0}
      }
    }
  ],
  "links": []
}
```

### 节点字段

| 字段 | 说明 |
| --- | --- |
| `id` | 图内唯一的稳定标识。缺省时使用 `name` |
| `uuid` | 微后端分配的资源 UUID；严格导入时必须提供 |
| `name` | 资源名称。缺省时使用 `id` |
| `display_name` | 面向界面的显示名称 |
| `type` | 节点类型；图导入缺省值为 `device` |
| `class` | Registry 中的设备或资源类名 |
| `parent` | 父节点的 `id` |
| `parent_uuid` | 父节点 UUID；同时提供时优先按 UUID 建立关系 |
| `config` | 驱动构造参数和设备配置 |
| `data` | 运行时状态数据 |
| `extra` | Uni-Lab-OS 通信与转换元数据 |
| `pose` | 相对父资源的位置、几何尺寸和可视化布局 |
| `schema` | 资源 schema |
| `model` | 资源模型信息 |
| `icon` | 资源图标 |
| `meta_data` | 规范化业务元数据 |
| `template_name` | 资源模板名称 |
| `resource_template_uuid` | 微后端资源模板 UUID |
| `sites` | 载架 Site 实例 |

图文件应显式提供 `uuid`。传输输入也可通过 `data.unilabos_uuid` 携带同一 UUID；
若两者同时存在，则值必须一致。加载器不会为严格导入的数据随机生成 UUID。

`children` 是资源树的递归容器，不属于 `ResourceDict` 领域字段。平铺 JSON 图应在
子节点上使用 `parent_uuid`，必要时同时提供便于阅读的 `parent`。

### host_node

host node 是运行时内置的服务设备，按 `class: host_node` 判别，且全图只能有
一个；动作定义始终来自内置 Registry 条目。设备图可以省略它：Host 启动时会
自动创建（ROS2 经通用设备管线初始化 `HostServices`，HostLink 在本地运行时
注册同一服务）。

设备图也可以显式声明 host node 节点（`class` 填 `host_node`），并且与其他
节点一样必须携带 `uuid`。声明后运行时复用该身份而不重复创建，图导出也会
保留该节点。实例 `id` 支持通过 `--host_node_id` 重命名（如
`host_node_8523`）；图中 `id` 与配置不一致时以配置为准。

### Pose

根级 `position` 字段无效；资源相对父节点的位置写入 `pose.position`：

```json
{
  "pose": {
    "size": {"width": 127.76, "height": 85.48, "depth": 10},
    "scale": {"x": 1, "y": 1, "z": 1},
    "layout": "x-y",
    "position": {"x": 100, "y": 200, "z": 0},
    "position3d": {"x": 100, "y": 200, "z": 0},
    "rotation": {"x": 0, "y": 0, "z": 0},
    "cross_section_type": "rectangle"
  }
}
```

嵌套的向量必须完整包含 `x`、`y`、`z`。可用布局为 `2d`、`x-y`、`z-y`
和 `x-z`；截面类型可用 `rectangle`、`circle` 和 `rounded_rectangle`。

GraphML 节点可使用顶层 `x`、`y`、`z`，加载器会将其规范化到
`pose.position`。若这些坐标与已有 `pose.position` 冲突，导入会失败。

### Links

连接使用节点 `id` 或 UUID 标识端点：

```json
{
  "source": "pump_1",
  "target": "reactor_1",
  "type": "fluid",
  "port": {
    "pump_1": "out",
    "reactor_1": "in"
  }
}
```

加载器会补充 `source_uuid` 和 `target_uuid`。`communication` 连接用于串口、
I/O 和 PLC 等通信适配器；物理流路使用 `fluid`。

## 规范化与校验

图加载过程执行以下处理：

1. 校验节点 UUID，并拒绝 UUID 缺失或冲突。
2. 为缺失的 `type`、`name`、`class`、`config`、`data` 和 `extra` 填充模型允许的默认值。
3. 将 GraphML 的标签、父子关系和顶层坐标转换为规范字段。
4. 按 `parent_uuid` 或 `parent` 建立资源树，并校验 UUID 唯一性。
5. 规范化连接端点及端口。
6. 为设备树补充本机 `machine_name`。

节点中的未知根字段会移入 `config`，但图文件应直接把驱动参数写在 `config`
中，以便模型审查和前端编辑。

## Python 加载接口

```python
from unilabos.resources.graphio import read_graphml, read_node_link_json

graph, resources, links = read_node_link_json("device_graph.json")
graph, resources, links = read_graphml("device_graph.graphml")
```

返回值依次为 NetworkX 图、`ResourceTreeSet` 和规范化连接列表。

## 示例与排查

设备图示例位于 `unilabos/test/experiments/`。修改示例时应保留已有 UUID；
需要创建新实例时，应先通过微后端创建或严格导入流程分配 UUID。

可使用完整 Registry 检查设备类和动作定义：

```bash
unilab --check_mode --complete_registry --skip_env_check -g path/to/device_graph.json
```

导入失败时优先检查：

- JSON 是否为严格语法；
- 每个节点是否具有唯一 UUID；
- `parent_uuid` 是否指向同一图中的节点；
- `pose` 的嵌套向量是否完整；
- `class` 是否存在于当前 Registry；
- 驱动构造参数是否位于 `config`。

源码参考：

- `unilabos/resources/objects/resource.py`
- `unilabos/resources/objects/pose.py`
- `unilabos/resources/resource_tracker.py`
- `unilabos/resources/graphio.py`
