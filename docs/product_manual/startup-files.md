# 3. 启动文件

:::{admonition} 阅读角色
- **业务负责人**：确认本次环境需要加载的设备、物料、库位和使用目的。
- **开发人员**：编写并检查模拟、联调和生产启动图及其连接配置。
- **验收人员**：核对实例、父子关系、环境隔离、启动结果和版本记录。
:::

```{toctree}
:maxdepth: 1

graph-json
```

启动文件是设备包交给 Uni-Lab OS 的“现场清单”。它说明本次启动有哪些设备、物料、仓库和库位，以及这些实例使用什么连接参数。设备包可以定义很多类型，但只有写进本次启动图的实例才会被加载。

## 完成本节后你将得到什么

- 一份只连接模拟设备的 `simulation.json`；
- 一份经过审批后才使用的 `production.json`；
- 一张设备实例与连接参数确认表；
- 一次能够复现的启动检查记录。

## 先分清四类文件

| 文件 | 回答的问题 | 不应该放什么 |
| --- | --- | --- |
| 设备源码 | 这类设备有哪些动作、参数和结果？ | 现场实例地址 |
| 物料源码 | 这类物料有什么尺寸、容量和库位约束？ | 某批物料的临时位置 |
| 启动图（Graph JSON） | 本次启动哪些实例，它们在哪里、怎样连接？ | 完整实验步骤和明文密钥 |
| 工作流源码 | 实验按什么顺序执行？ | 驱动通信实现 |

## 推荐目录

```text
device-package/
└── deployment/
    ├── graphs/
    │   ├── simulation.json
    │   ├── integration.json
    │   └── production.json
    └── README.md
```

三份文件分别用于模拟验证、隔离设备联调和生产运行。不能只维护一份 启动图，然后在启动前临时修改地址。

## 业务人员先填写实例表

在写 JSON 前，先由设备、工艺和现场负责人共同确认：

| 实例 ID | 业务名称 | 类型 | 模板类 | 父级/库位 | 连接方式 | 使用环境 |
| --- | --- | --- | --- | --- | --- | --- |
| `transport_robot_01` | 搬运机器人 1 | 设备 | 从检查报告复制 | 无 | 模拟或厂商接口 | 模拟/生产 |
| `mixing_station_01` | 混合工站 1 | 设备 | 从检查报告复制 | 无 | 模拟或网络地址 | 模拟/生产 |
| `main_deck` | 主工作台 | 资源 | 从检查报告复制 | 无 | 不适用 | 全部 |
| `source_rack` | 原料载架 | 资源 | 从检查报告复制 | `main_deck` | 不适用 | 全部 |
| `sample_001` | 测试样品容器 | 资源 | 从检查报告复制 | `source_rack` | 不适用 | 模拟 |

实例 ID 一旦被工作流、库存或运行记录引用，就不要随意修改。生产地址、串口、证书和安全参数必须由对应负责人确认，开发人员不能自行猜测。

## 按步骤操作

1. 安装用户设备包并运行 `unilab package inspect`。
2. 从检查报告复制设备和物料的完整 `class`，不要根据文件名手写。
3. 按上表确定每个实例的稳定 `id`、业务名称和父级。
4. 先创建 `simulation.json`，所有设备使用模拟实现或隔离端点。
5. 加入工作台、仓库、工艺位和测试物料，并核对父子关系。
6. 执行 JSON、重复 ID、父级和类型检查。
7. 使用 `dry-run` 启动 Uni-Lab OS。
8. 页面中核对设备、物料和库位，再运行安全测试工作流。
9. 模拟配置通过后，复制为 `integration.json`，只接入获准测试的真实设备。
10. 生产 启动图 由项目负责人审批并锁定版本，不能从测试文件直接临时改出。

## 启动与验收

```bash
export DEVICE_PACKAGE_ROOT="/absolute/path/to/device-package"
export GRAPH_FILE="$DEVICE_PACKAGE_ROOT/deployment/graphs/simulation.json"

unilab workspace start \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --graph "$GRAPH_FILE" \
  --runtime-mode dry-run \
  --wait 300 \
  --json

unilab workspace status \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --json
```

成功标准：Uni-Lab OS 整体就绪、工作流完整加载、设备数量与 启动图 一致、物料父子关系正确，而且模拟配置没有访问生产地址。

停止时使用整套系统命令：

```bash
unilab workspace stop \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --wait 300 \
  --json
```

下一页将逐字段编写一份可检查的 [启动图](graph-json.md)。
