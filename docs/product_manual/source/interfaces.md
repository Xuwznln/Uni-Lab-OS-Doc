---
orphan: true
---

# 开发工具与接口

:::{admonition} 阅读角色
- **业务负责人**：提供使用场景和预期结果，不直接操作调试接口或恢复命令。
- **开发或运维人员**：使用开发工具、接口和日志定位并处理问题。
- **验收人员**：确认权限、输入输出、异常处理和操作记录符合要求。
:::

本页面向设备包开发、系统集成和技术运维人员，集中说明 Workbench、命令行、MCP 和协议联调工具。业务人员的网页入口、页面导航和第一次安全操作统一见[Uni-Lab OS 快速上手](console.md)。这些工具属于同一个 Uni-Lab OS 产品，不是独立产品。

## Theia Workbench

Theia Workbench 是桌面/Workbench 产品，Activity Bar 包含：

- 设备；
- 机器人动作调试；
- 机器人点位；
- 实验台；
- 试剂；
- 物料；
- 工作流；
- Agent；
- 环境管理和设置。

实际功能由连接 Profile 决定：

| 能力 | Go Backend Profile | Python Local Profile |
| --- | --- | --- |
| 设备目录与动作运行 | 支持 | 支持 |
| 强制解锁 | 不支持 | 支持 |
| 工作流读写、运行和 SSE | 支持 | 支持 |
| Python 源码编辑 | 不支持 | 支持 |
| 物料读取 | 支持 | 支持 |
| 物料写入 | 不支持 | 不支持 |
| 试剂身份 CRUD | 支持 | 不支持 |
| 试剂库存 | CRUD + 历史 | 只读 |

Local Profile 以 Python 源码/编译结果为权威；Backend Profile 以画布启动图（Graph JSON）为权威。切换 Profile 只影响之后的任务，不迁移已有任务，且未保存修改会阻止切换。

### 本地安装与启动

Workbench 是可选的开发入口，不是运行 Uni-Lab OS 网页或 CLI 的前置。它要求 Node 20/22、pnpm `10.13.1`、有效设备包工作区，以及同时含 Python 与 `unilab` 的环境。

Workbench 的定位和入口说明见本节；实际安装应以 `uni-lab-fe` 仓库 README 为准。服务器、TLS 和公网入口仍以[Kubernetes 部署与上线](deployment.md)为准。

### Agent 与设备包生成器

本地 Workbench 的 Activity Bar 提供“Agent”入口，环境管理器也能启动、停止和查看当前 Workspace Agent。打开所选 Editable Package 并启动 Agent 后，右侧面板会显示当前 Workspace 会话。

Workbench 随应用打包一组托管 Skill。Agent 启动时会把它们播种到当前 Workspace 的 `.agents/skills/`；其中 `unilab-domain-repo-builder` 可辅助新建、迁移和诊断实验室设备包。用户修改过的 Skill 会保留，不会被更新静默覆盖。

Agent 和设备包生成器都是可选开发能力；不用它们也能手写仓库并运行产品。它们不在 Uni-Lab OS 业务页面中，也不代表生产验收。使用条件与操作步骤见[使用 AI 设备包生成器（实验性）](repository-builder-skill.md)。

Workbench 的实验台只读展示逻辑站点占用，不是传感器事实；机器人点位当前不可用；Catalog 的新建、删除、变更日志和状态筛选被隐藏。旧 breakpoint debugger 与当前退役 Debug API 不兼容。

### Workbench 远程共享

桌面环境管理器可以启动一个秘密链接进行远程共享。代码要求非 loopback 监听必须配置 TLS 证书、HTTPS public origin、Host/Origin 校验，并用一次性 token 换取 HttpOnly cookie；默认 capability 有效期为 12 小时，停止或重启会撤销旧 generation。

同一个远程 Workbench 是共享会话，不支持多人协同编辑协议。不要让两个人同时保存同一文件。

## 产品口径

操作页面、工作区服务、任务调度和设备运行能力统一称为 **Uni-Lab OS**。文档不再将它们列为不同产品。只有在查看日志、状态 JSON、部署清单或内部 API 时，才可能看到 `backend`、`scheduler`、`edge` 等组件名称；这些名称只用于技术定位。

## 命令行

安装 Uni-Lab OS 后，代码注册三个主要入口：`unilab`、`unilab-supervisor` 和 `unilab-mcp`。

常用命令族：

```text
unilab workspace status|start|stop|restart|reset-local
unilab workflow list|inspect|run|watch|command
unilab material scene|layout|template
unilab package inspect|build|upload|add|update|remove
unilab login|logout|whoami
unilab template-sync
unilab instance-sync
unilab doctor net|talker|listener|fake-device
```

从零创建一个能被这些命令检查、构建并由 Uni-Lab OS 加载的设备包，见[工作区](workspace.md)。

`reset-local` 需要显式确认；`whoami` 只显示凭证来源，不会向服务端验证身份。旧 `workflow debug` 走退役 API，不应使用。

## MCP Agent 工具

安装可选 `mcp` 依赖后，`unilab-mcp --workspace <设备包工作区绝对路径>` 暴露：

- Workspace 状态、单组件启停、日志和重置；
- 工作流列表、检查、Task 运行/观察/命令，以及等待 Authoring 修订；
- 物料 scene 捕获与检查；
- 布局检查、预览和应用；
- 可视回归比较与批准；
- 模板校验。

MCP 适合为 AI 提供当前工作区和运行状态，但当前没有工作流源码生成、写入、导入、发布或运行前预检工具。`wait_authoring` 只在 revision 增长或存在非空诊断时可靠返回，不能用它判断同一 revision 下的无诊断 candidate 已经生成；这时应读取 Authoring REST 状态。推荐的 AI 创作组合与安装命令见[用 AI 编写工作流](ai-workflow-authoring.md)。

MCP 是由客户端拉起的本地命令型 Server，不是公网服务，也不是人类权限系统。`run_workflow` 会直接创建 Task，不能提交完整库存绑定和优先级字段，且它的 `operation_id` 不是创建幂等键；必须放在人工审查、发布和产品预检之后。重置仍需显式确认。

`debug_workflow` 和带 `hold_uuid` 的旧调试命令受 HTTP 410 限制；请改用 `run_mode="step"` 的标准 Task 和普通 `step`/`resume`/`cancel` 命令。

已注册的 `switch_workspace_authority` 当前会被 Workspace Host 以 `backend_mode_removed` 拒绝，不能当作可用能力。真机环境也不要用单组件 MCP stop 代替 `unilab workspace stop` 的完整排空流程。

## PLC-Sim 与 Modbus-Sim

Uni-Lab-Sim 仓库提供：

- PLC/OPC UA 仿真器：用于模拟动作、传感器、握手、超时和故障；只有用户设备包明确需要并提供配置时才部署，完整步骤见[PLC / OPC UA 仿真器](plc-sim.md)；
- Modbus-Sim：Modbus TCP、RTU RS-485、RTU RS-232、ASCII、多从站、CSV 寄存器和报文监视。

两者是设备/工业协议联调工具，不承担 Uni-Lab OS 的任务调度、库存权威或运动安全。默认 GUI 面向本机/可信实验室网络，没有公网用户鉴权。

## 不应作为正式入口的代码

- `unilab-os-task-console-prototype` 是历史任务台原型，多个按钮是占位，所谓 SSE 实际为 15 秒轮询。
- `kernel-web` 中有设备广场等实验页面，但没有挂到正式 Theia Activity Bar。
- `apps/cloud-web` 目前只是未来占位。
- VS Code 扩展负责打开/高亮工作流和资源、地图选择、诊断与发布快照，不会启动或控制 Runtime。
