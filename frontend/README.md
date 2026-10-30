# Uni-Lab OS 实验运营控制台

这里是 Uni-Lab-OS 内置的 React + TypeScript 前端源码。页面按四个一级业务项目组织：

- 总监控：实验室态势、活动任务、异常、物料脉搏与运行事件。
- 物料：身份、类型、批次、权威库存位置、未结束任务引用与流转谱系。
- 工作流：定义目录、发布修订、DAG、输入输出合同、Preflight 与版本记录。
- 任务：创建 Task、运行输入、队列、Job 时间线、资源占用、结果与异常操作。

前端不包含独立业务 Backend，也不需要 Nginx。生产构建产物由 Uni-Lab-OS 的
Workspace Backend 直接从 `/console/` 下发，页面与 `/api/v1` 使用同一来源。

## 本地开发

需要 Node.js 22.13 或更新版本：

```bash
cd frontend
npm ci
npm run dev
```

Vite 默认监听 <http://127.0.0.1:4174/console/>。`/api` 代理默认回退到
`http://127.0.0.1:8002`；使用 Workspace 的动态 Backend 端口时，设置
`VITE_EDGE_API_URL`，代理会自动跟随该地址：

```bash
BACKEND_URL=$(unilab workspace status --workspace /path/to/workspace --json \
  | jq -er '.components.backend.address // empty')
VITE_EDGE_API_URL="$BACKEND_URL" npm run dev -- --host 127.0.0.1 --port 4173
```

首次连接 Edge 失败时不会混入演示数据；已成功读取过数据后发生短暂抖动，页面
保留最后一次成功快照并进入显式只读重连状态，所有写操作暂停。只有明确设置
`VITE_ENABLE_DEMO_DATA=true` 时才会启用演示回退，页面会同时显示醒目的演示模式提示。

## 检查与构建

```bash
npm test
npm run build
```

`npm run build` 会把可发布文件直接写入
`../unilabos/app/web/static/console/`。该目录属于 Python 包并随镜像提交；修改前端源码后，
必须重新构建并提交同步后的静态文件。CI 会验证测试、生产构建和构建产物一致性。

## 集成入口

Workspace Backend 的根地址会保留查询参数并跳转到 `/console/`，因此下面两种写法均可用：

- <http://127.0.0.1:4173/?page=tasks>
- <http://127.0.0.1:4173/console/?page=tasks>

其他一级页面的 `page` 参数为 `overview`、`materials` 和 `workflows`。

任务页采用并行任务矩阵：按工作流和冻结执行图分组，每行一个 Task，每列一个真实执行
节点，按 Job 的 `workflow_node_uuid` 精确点亮。同一工作流的不同修订或单节点调试任务
不会串列。页面读取全部 active Task，并保留最近 20 条终态任务，避免轮询全部历史 Jobs。

任务矩阵使用一次 `/api/v1/workflow-task-presentations?view=matrix` 读取全部活动 Task、
需关注项和最近 20 条终态项；重参数、实时反馈和运行结果只在用户点击节点后通过
`/workflow-tasks/{uuid}` 与 `/workflow-tasks/{uuid}/jobs` 按需读取。定时刷新和 SSE
事件经过同一个单飞合并器，同一时刻只执行一个矩阵请求并最多保留一次尾随刷新。

页面还读取 `/api/v1/readiness`、`/workflows`、`/materials` 和工作流 Graph。创建任务由用户明确提交后调用 Edge
`POST /api/v1/workflow-tasks`；物料点击会继续读取 `/materials/{uuid}` 的权威
`current_site`。其余未接入动作会显示明确提示，不会静默无响应。

创建任务表单保留工作流输入的 JSON Schema：整数、数值、字符串约束直接映射为表单校验，
对象和数组使用 JSON 编辑区；标记为 `ResourceSlot` 的输入从 Edge 物料清单中选择，并按
Edge 契约提交为 `{ "uuid": "..." }`，同时遵守
`allowed_resource_template_uuids` 限制。

部署可配置允许的 Host，并对浏览器访问 `/api/` 强制同源校验，以防 DNS rebinding 与
CSRF。无 `Origin` 的本地 CLI 调用保持兼容。正式多用户环境仍应在可信入口配置身份认证
与权限控制。
