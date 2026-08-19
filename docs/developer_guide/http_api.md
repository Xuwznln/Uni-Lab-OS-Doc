# HTTP API 指南

本文档介绍如何通过 Edge HTTP API 查询 Uni-Lab-OS 的设备、任务和动作异常状态。任务创建由调度后端负责，Edge HTTP API 不接受本地执行请求。

## 概述

Uni-Lab-OS 提供基于 FastAPI 的 RESTful HTTP API，默认运行在 `http://localhost:8002`。该接口用于只读查询和监控；动作由调度后端下发给 Host。

### 基础信息

- **Base URL**: `http://localhost:8002/api/v1`
- **Content-Type**: `application/json`
- **响应格式**: JSON

### 通用响应结构

```json
{
    "code": 0,
    "data": { ... },
    "message": "success"
}
```

| 字段      | 类型   | 说明               |
| --------- | ------ | ------------------ |
| `code`    | int    | 状态码，0 表示成功 |
| `data`    | object | 响应数据           |
| `message` | string | 响应消息           |

## 快速开始

以下是一个完整的查询工作流示例：查询设备 → 获取动作 → 等待调度后端下发任务 → 获取结果。

### 步骤 1: 获取在线设备

```bash
curl -X GET "http://localhost:8002/api/v1/online-devices"
```

**响应示例**:

```json
{
  "code": 0,
  "data": {
    "online_devices": {
      "host_node": {
        "device_key": "/host_node",
        "namespace": "",
        "machine_name": "本地",
        "uuid": "xxx-xxx-xxx",
        "node_name": "host_node"
      }
    },
    "total_count": 1,
    "timestamp": 1732612345.123
  },
  "message": "success"
}
```

### 步骤 2: 获取设备可用动作

```bash
curl -X GET "http://localhost:8002/api/v1/devices/host_node/actions"
```

**响应示例**:

```json
{
  "code": 0,
  "data": {
    "device_id": "host_node",
    "actions": {
      "test_latency": {
        "type_name": "unilabos_msgs.action._empty_in.EmptyIn",
        "type_name_convert": "unilabos_msgs/action/_empty_in/EmptyIn",
        "action_path": "/devices/host_node/test_latency",
        "goal_info": "{}",
        "is_busy": false,
        "current_job_id": null
      },
      "create_resource": {
        "type_name": "unilabos_msgs.action._resource_create_from_outer_easy.ResourceCreateFromOuterEasy",
        "action_path": "/devices/host_node/create_resource",
        "goal_info": "{res_id: '', device_id: '', class_name: '', ...}",
        "is_busy": false,
        "current_job_id": null
      }
    },
    "action_count": 5
  },
  "message": "success"
}
```

**动作状态字段说明**:

| 字段             | 说明                          |
| ---------------- | ----------------------------- |
| `type_name`      | 动作类型的完整名称            |
| `action_path`    | ROS2 动作路径                 |
| `goal_info`      | 动作参数模板                  |
| `is_busy`        | 动作是否正在执行              |
| `current_job_id` | 当前执行的任务 ID（如果繁忙） |

### 步骤 3: 由调度后端下发任务

`POST /api/v1/job/add` 已停用并固定返回 HTTP 409。前端必须把执行请求提交给调度后端；调度后端完成排程后，通过 HostLink 或 ROS2 下发带有 `job_id`、`node_id` 和 `task_id` 的执行命令。

**停用接口响应示例**:

```json
{
  "detail": "Local job submission is disabled; submit the action to the scheduler backend"
}
```

**任务状态码**:

| 状态码 | 含义      | 说明                           |
| ------ | --------- | ------------------------------ |
| 0      | UNKNOWN   | 未知状态                       |
| 1      | ACCEPTED  | 任务已接受，等待执行           |
| 2      | EXECUTING | 任务执行中                     |
| 3      | CANCELING | 任务取消中                     |
| 4      | SUCCEEDED | 任务成功完成                   |
| 5      | CANCELED  | 任务已取消                     |
| 6      | ABORTED   | 任务中止（设备繁忙或执行失败） |

### 步骤 4: 查询任务状态和结果

```bash
curl -X GET "http://localhost:8002/api/v1/job/b6acb586-733a-42ab-9f73-55c9a52aa8bd/status"
```

**响应示例（执行中）**:

```json
{
  "code": 0,
  "data": {
    "jobId": "b6acb586-733a-42ab-9f73-55c9a52aa8bd",
    "status": 2,
    "result": {}
  },
  "message": "success"
}
```

**响应示例（执行完成）**:

```json
{
  "code": 0,
  "data": {
    "jobId": "b6acb586-733a-42ab-9f73-55c9a52aa8bd",
    "status": 4,
    "result": {
      "error": "",
      "suc": true,
      "return_value": {
        "avg_rtt_ms": 103.99,
        "avg_time_diff_ms": 7181.55,
        "max_time_error_ms": 7210.57,
        "task_delay_ms": -1,
        "raw_delay_ms": 33.19,
        "test_count": 5,
        "status": "success"
      }
    }
  },
  "message": "success"
}
```

> **注意**: 任务状态和结果可重复查询，不会因前端第一次读取而删除。微后端仍会按任务结果存储的清理策略回收过期记录。

## API 端点列表

### 设备相关

| 端点                                                       | 方法 | 说明                   |
| ---------------------------------------------------------- | ---- | ---------------------- |
| `/api/v1/online-devices`                                   | GET  | 获取在线设备列表       |
| `/api/v1/devices`                                          | GET  | 获取设备配置           |
| `/api/v1/devices/{device_id}/actions`                      | GET  | 获取指定设备的可用动作 |
| `/api/v1/devices/{device_id}/actions/{action_name}/schema` | GET  | 获取动作参数 Schema    |
| `/api/v1/actions`                                          | GET  | 获取所有设备的可用动作 |

### 任务相关

| 端点                          | 方法 | 说明               |
| ----------------------------- | ---- | ------------------ |
| `/api/v1/job/add`             | POST | 已停用，固定返回 409；任务由调度后端下发 |
| `/api/v1/job/{job_id}/status` | GET  | 查询任务状态和结果 |

### 动作异常决策相关

| 端点                                                | 方法 | 说明                             |
| --------------------------------------------------- | ---- | -------------------------------- |
| `/api/v1/error-decisions`                           | GET  | 获取尚未处理的动作异常决策       |
| `/api/v1/error-decisions/{decision_id}`             | POST | 已停用，固定返回 409；决策由调度后端下发 |
| `/api/v1/monitor/events`                             | GET  | 订阅动作状态与异常决策 SSE 事件  |
| `/api/v1/monitor/snapshot`                           | GET  | 获取异常决策及近期事件权威快照   |

这组接口直接返回业务 JSON，HTTP 错误使用 FastAPI 的 `detail` 结构，不套用本页其他接口的 `code/data/message` 外层。Edge 只读展示待决策项；后端完成前端询问和调度更新后，才通过 transport 释放 Host 上暂存的失败结果。完整协议见[动作异常决策：前端接入协议](action_error_decision_frontend.md)。

### 资源相关

| 端点                | 方法 | 说明         |
| ------------------- | ---- | ------------ |
| `/api/v1/resources` | GET  | 获取资源列表 |

## 错误处理

### 动作执行失败

Host 先暂存设备原始失败并通知后端。后端负责询问前端、更新调度，再带 `scheduler_updated: true` 释放失败上报。除 `operator_intervention` 外，Host 不会在本地重试、跳过或执行 fallback；调度后端如需重试，应创建并关联新的调度节点或执行尝试。

### 参数错误

```json
{
    "code": 2002,
    "data": { ... },
    "message": "device_id is required"
}
```

## 轮询策略

推荐的任务状态轮询策略：

```python
import requests
import time

def wait_for_job(job_id, timeout=60, interval=0.5):
    """等待任务完成并返回结果"""
    start_time = time.time()

    while time.time() - start_time < timeout:
        response = requests.get(f"http://localhost:8002/api/v1/job/{job_id}/status")
        data = response.json()["data"]

        status = data["status"]
        if status in (4, 5, 6):  # SUCCEEDED, CANCELED, ABORTED
            return data

        time.sleep(interval)

    raise TimeoutError(f"Job {job_id} did not complete within {timeout} seconds")

# job_id 来自调度后端创建的任务
job_id = "b6acb586-733a-42ab-9f73-55c9a52aa8bd"
result = wait_for_job(job_id)
print(result)
```

## 相关文档

- [设备注册指南](add_device.md)
- [动作定义指南](add_action.md)
- [网络架构概述](networking_overview.md)
