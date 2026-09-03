# 微后端 HTTP API

Uni-Lab-OS 的 `8002` 端口提供微后端 API 和入口导航页。用户界面由独立前端
项目提供，可部署到 GitHub Pages，并通过 HTTP 和 SSE 读取本机微后端。

## 入口

- `http://localhost:8002/`：极简前端与文档导航页
- `http://localhost:8002/api/docs`：Swagger/OpenAPI Explorer
- `http://localhost:8002/api/redoc`：ReDoc
- `http://localhost:8002/api/openapi.json`：机器可读 OpenAPI
- `https://deepmodeling.github.io/Uni-Lab-OS/`：官方 GitHub Pages 文档

实际可用接口以当前进程的 OpenAPI 为准。数据库 API 按所有权分组：

| 命名空间 | 所有权 |
| --- | --- |
| `/api/v1/runtime/*` | Job、执行尝试及运行时状态 |
| `/api/v1/materials/*` | 物料模板、实例、位置和液体数据 |
| `/api/v1/telemetry/*` | 高频设备状态与遥测事件 |
| `/api/v1/history/*` | 可长期保留的运行历史 |
| `/api/v1/graphs/*` | 设备图快照与实时拓扑 |
| `/api/v1/workflows/*` | 本地调度模式的工作流定义与任务 |
| `/api/v1/error-decisions/*` | 后端协调后的错误决策观测/释放协议 |
| `/api/v1/status-incidents/*` | 设备状态异常与调度联锁 |

当物料权威配置为外部微后端时，本进程不会挂载本地 `/materials/*` writer，避免
出现两个可写物料中心。

前端不应直接向 Host 创建或重试 Job。执行命令由调度后端下发；Host 报错后，后端
负责询问前端、更新调度图或 attempt，再释放最终结果。

## Backend 控制面

Backend WebSocket 发送 `runtime.v1` 控制面的轻量通知；业务命令、结果与完整
状态通过微后端 HTTP API 交换。
