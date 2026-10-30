# 启动图（Graph JSON）编写指南

:::{admonition} 阅读角色
- **业务负责人**：确认本次环境需要加载的设备、物料、库位和使用目的。
- **开发人员**：编写并检查模拟、联调和生产启动图及其连接配置。
- **验收人员**：核对实例、父子关系、环境隔离、启动结果和版本记录。
:::

启动图 是 Uni-Lab OS 的启动图。它描述“本次加载哪些实例”，不描述实验步骤。最外层必须是 JSON 对象，`nodes` 必须是数组。

## 1. 节点字段

| 字段 | 是否必须 | 写法与约束 |
| --- | --- | --- |
| `id` | 必须 | 启动图 内唯一、长期稳定，只用字母、数字和下划线更易维护 |
| `name` | 建议 | 给业务人员看的名称；不填时通常回退到 `id` |
| `type` | 建议明确 | 设备写 `device`；工作台、仓库、容器等资源使用相应资源类型 |
| `class` | 必须 | 从 `package inspect` 结果复制完整类型标识 |
| `parent` | 资源通常必须 | 填父节点的 `id`；根设备和根工作台可以为空 |
| `position` | 按需 | 相对父级的 `x/y/z`；单位由设备包约定，通常为 mm |
| `config` | 必须为对象 | 构造实例所需参数；字段必须与类型定义一致 |
| `data` | 建议为对象 | 非构造参数的初始业务数据；没有时写 `{}` |
| `children` | 可选兼容 | 可以帮助阅读，但父子关系以每个子节点的 `parent` 为准 |

不要给 启动图 节点自行添加工作流节点 UUID。启动图的稳定身份是 `id`；工作流节点 UUID 属于工作流源码。

## 2. 创建最小模拟 启动图

创建 `deployment/graphs/simulation.json`：

```json
{
  "metadata": {
    "purpose": "simulation",
    "owner": "project-team",
    "version": "1.0.0"
  },
  "nodes": [
    {
      "id": "main_deck",
      "name": "主工作台",
      "type": "deck",
      "class": "community.example_lab.main_deck",
      "parent": null,
      "position": {"x": 0, "y": 0, "z": 0},
      "config": {
        "size_x": 2000,
        "size_y": 1000,
        "size_z": 20
      },
      "data": {}
    },
    {
      "id": "source_rack",
      "name": "原料载架",
      "type": "warehouse",
      "class": "community.example_lab.sample_rack",
      "parent": "main_deck",
      "position": {"x": 100, "y": 100, "z": 20},
      "config": {
        "sites": [
          {
            "label": "SRC_01",
            "name": "原料位 1",
            "position": {"x": 10, "y": 10, "z": 0},
            "size": {"width": 90, "height": 90, "depth": 120},
            "content_type": ["sample_container"],
            "visible": true,
            "occupied_by": "sample_001"
          }
        ]
      },
      "data": {}
    },
    {
      "id": "sample_001",
      "name": "测试样品容器 001",
      "type": "container",
      "class": "community.example_lab.sample_container",
      "parent": "source_rack",
      "barcode": "TEST-SAMPLE-001",
      "config": {},
      "data": {"lifecycle_state": "available"}
    },
    {
      "id": "transport_robot_01",
      "name": "搬运机器人 1",
      "type": "device",
      "class": "community.example_lab.transport_robot_sim",
      "parent": null,
      "config": {
        "endpoint": "sim://local",
        "command_timeout_seconds": 60
      },
      "data": {}
    },
    {
      "id": "mixing_station_01",
      "name": "混合工站 1",
      "type": "device",
      "class": "community.example_lab.mixing_station_sim",
      "parent": null,
      "config": {
        "endpoint": "sim://local",
        "command_timeout_seconds": 120
      },
      "data": {}
    },
    {
      "id": "mixing_process_rack",
      "name": "混合工艺位",
      "type": "warehouse",
      "class": "community.example_lab.process_rack",
      "parent": "mixing_station_01",
      "config": {
        "logical_mount": true,
        "sites": [
          {
            "label": "MIX_01",
            "name": "混合位 1",
            "position": {"x": 0, "y": 0, "z": 0},
            "size": {"width": 100, "height": 100, "depth": 150},
            "content_type": ["sample_container"],
            "visible": true,
            "occupied_by": null
          }
        ]
      },
      "data": {}
    }
  ]
}
```

示例的 `class`、尺寸、动作超时和 `config` 字段只用于说明结构。必须替换为用户设备包检查报告与现场确认值。

## 3. 配置设备节点

设备节点的 `config` 会传给设备构造器，因此字段名、类型和默认值必须完全一致。

| 连接方式 | 常见配置 | 必须补充的约束 |
| --- | --- | --- |
| 模拟设备 | `endpoint: sim://local`、故障注入 | 不得调用真实传输 |
| 串口 | `port`、`baudrate`、`timeout` | 端口权限、帧格式、结束符和重连规则 |
| 网络/API | `base_url`、`connect_timeout`、`request_timeout` | TLS、认证、幂等和重试边界 |
| PLC/OPC UA | `endpoint`、命名空间、点位配置 | 点位类型、握手、故障和复位规则 |
| 上位机 | 服务地址或受控自动化配置 | 软件版本、窗口状态和人工接管 |

实例条码写在节点顶层 `barcode`；不要把它误写进设备构造参数 `config`。密码、Token、证书私钥和生产配方不写入 启动图。启动图 只保存 Secret 的引用方式或环境变量占位符。

## 4. 配置仓库、库位和物料

资源关系从根到叶通常是：

```text
工作台或设备
└── 仓库/载架
    └── 容器、板、吸头盒或其他物料
```

必须满足：

- 每个非根资源的 `parent` 指向 启动图 中存在的节点；
- 放置位（Site）的 `occupied_by` 为空，或指向实际占位物料的 `id`；
- 占位物料的 `parent` 与 放置位 所属仓库一致；
- `content_type` 与允许放入的物料模板一致；
- 位置和尺寸来自正式图纸或测量记录，单位统一；
- 同一物料不能同时占用两个库位。

## 5. 分离模拟、联调和生产配置

| 配置 | 允许的连接 | 禁止事项 |
| --- | --- | --- |
| `simulation.json` | 纯模拟类或隔离仿真器 | 生产 IP、真实串口、真实凭证 |
| `integration.json` | 获准测试的真实设备 | 未纳入测试的设备和生产物料 |
| `production.json` | 审批后的正式设备 | 临时地址、调试开关和模拟类 |

三份文件可以共享节点命名，但不能靠运行前手工改值切换环境。生产文件应纳入版本和变更审批。

## 6. 本地静态检查

```bash
export GRAPH_FILE="deployment/graphs/simulation.json"

jq empty "$GRAPH_FILE"
jq -e '.nodes | type == "array"' "$GRAPH_FILE"

# 有输出表示存在重复 ID
jq -r '.nodes[].id' "$GRAPH_FILE" | sort | uniq -d

# 有输出表示 parent 找不到
jq -r '
  (.nodes | map(.id) | INDEX(.)) as $ids
  | .nodes[]
  | select(.parent != null and .parent != "" and ($ids[.parent] == null))
  | "\(.id) -> \(.parent)"
' "$GRAPH_FILE"

# 人工核对本次引用的全部模板类
jq -r '.nodes[] | [.type, .id, .class] | @tsv' "$GRAPH_FILE" | sort
```

所有检查都无异常后，再运行：

```bash
unilab package inspect \
  --path /absolute/path/to/device-package \
  --out /tmp/device-package-inspect
```

`package inspect` 负责检查设备包定义；真正加载 启动图 还必须通过 Workspace 启动验证。

## 7. 使用模拟配置启动

```bash
unilab workspace start \
  --workspace /absolute/path/to/device-package \
  --graph deployment/graphs/simulation.json \
  --runtime-mode dry-run \
  --wait 300 \
  --json
```

启动后逐项核对：

1. Uni-Lab OS 整体为 `ready`；
2. 设备实例 ID、名称和数量与 启动图 一致；
3. 物料树中的父级、库位和条码正确；
4. 工作流引用的设备 ID 和资源 ID 都能找到；
5. 日志没有类型解析、重复 ID、缺失父级或构造参数错误。

## 8. 常见错误

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `class` 找不到 | 使用了文件名、类名或不完整短名 | 从检查报告复制完整类型标识 |
| 设备未出现 | 节点没有进入本次 启动图，或类型解析失败 | 检查 `nodes`、`type` 和日志 |
| 资源成为根节点 | `parent` 拼写错误或父节点缺失 | 修正子节点的 `parent` |
| 放置位 显示空闲但实际有物料 | `occupied_by` 与物料 `parent` 不一致 | 重新盘点并同步两处关系 |
| 模拟启动访问真实设备 | 启动图 中仍有生产端点或真实类 | 立即停止，使用独立模拟 启动图 |
| 修改后旧任务无法解释 | 覆盖了运行任务使用的 启动图 | 恢复旧版本，为新配置创建新版本 |

## 9. 提交前清单

- [ ] `nodes` 是数组，每个节点都有唯一 `id` 和有效 `class`；
- [ ] 设备和资源 `type` 清楚；
- [ ] 所有 `parent`、放置位 和 `occupied_by` 能互相核对；
- [ ] `config` 与设备或资源构造参数一致；
- [ ] 模拟、联调和生产 启动图 物理分离；
- [ ] 文件中没有明文凭证和敏感业务数据；
- [ ] JSON、重复 ID、父级、Catalog 和启动验证全部通过；
- [ ] 启动图 版本、校验值、审批记录和回滚文件已保存。
