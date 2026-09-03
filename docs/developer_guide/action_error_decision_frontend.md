# 动作失败决策与调度恢复契约

## 1. 权威边界

| 组件 | 职责 |
|---|---|
| 设备 | 执行动作并返回原始成功或失败结果 |
| Host | 暂存有 `error_policy` 的失败；只在收到后端 release 后发布终态 |
| 调度后端 | 持久化失败，询问前端，更新调度图/attempt，并向 Host release |
| 前端 | 展示注册表声明的选项并把选择提交给调度后端 |

Host 不执行 retry、skip 或 fallback，也不自行应用超时默认策略。人工干预是唯一可以在
Host result boundary 替换有效结果的选择。

## 2. 时序

```text
Device             Host                 Scheduler Backend          Frontend
  |-- raw failed -->|                            |                    |
  |                 |-- decision_required ----->|                    |
  |                 |     (暂不报 failed)        |-- ask ----------->|
  |                 |                            |<-- choice ---------|
  |                 |                            | update schedule     |
  |                 |<-- decision release ------|                    |
  |                 |-- job_status failed ----->|                    |
```

`retry` 时，后端在 release 前为同一 `node_id` 创建新的 attempt/job；旧 job 仍由 Host
如实上报 failed。`skip`、`abort`、fallback/补偿也由后端更新调度，Host 不创建新 goal。
本机调度（默认 profile）下"后端"就是同进程的 Workflow Authority：它在同一事务里把失败
attempt 记 failed、为同一节点运行追加 `attempt_no+1` 的新 job，并保持节点运行为
pending；节点运行（`node_run_uuid`）是画布节点的稳定身份，attempt 是它的历史。

## 3. Host → Backend

消息：`job_error_decision_required`

```json
{
  "action": "job_error_decision_required",
  "data": {
    "decision_id": "decision-uuid",
    "task_id": "workflow-run-id",
    "node_id": "logical-node-id",
    "node_run_uuid": "node-run-uuid（≡ attempt_group_uuid，本机调度时给出）",
    "job_id": "attempt-job-id",
    "device_id": "pump-1",
    "action_name": "transfer",
    "exception_type": "CommunicationError",
    "error_message": "serial port closed",
    "options": [
      {"action": "retry", "label": "重试"},
      {"action": "operator_intervention", "label": "人工替代结果"},
      {"action": "abort", "label": "终止"}
    ],
    "retry_count": 0,
    "max_retries": 2,
    "decision_timeout_seconds": 300,
    "default_on_decision_timeout": "abort"
  }
}
```

Host 断线期间保留 pending，WebSocket 重连后按同一个 `decision_id` 重放。后端必须幂等
upsert。没有 `error_policy` 或没有后端 bridge 时，Host 直接上报原始 failed。

## 4. Backend → Host

消息：`job_error_decision`

```json
{
  "action": "job_error_decision",
  "data": {
    "decision_id": "decision-uuid",
    "job_id": "attempt-job-id",
    "device_id": "pump-1",
    "action": "retry",
    "reason": "operator confirmed",
    "scheduler_updated": true
  }
}
```

`scheduler_updated` 必须严格为 `true`。缺失或为 false 时 Host 保持 pending，不得提前
发布 failed。`decision_id + job_id + device_id` 必须与 pending 完全一致；第一次合法 release
获胜，重复 release 只命中短期 tombstone，不重复发布终态。

除人工干预外，无论选择 retry、skip、abort 还是 fallback，Host 都发布原始 failed，并在
`return_info.error_resolution` 中附加：

```json
{
  "decision_id": "decision-uuid",
  "selected_action": "retry",
  "reason": "operator confirmed",
  "scheduler_updated": true
}
```

## 5. 人工干预

人工替代必须显式选择 `operator_intervention` 并携带 `result` 或 `return_value`：

```json
{
  "decision_id": "decision-uuid",
  "job_id": "attempt-job-id",
  "device_id": "pump-1",
  "action": "operator_intervention",
  "result": {"confirmed": true},
  "scheduler_updated": true
}
```

Host 将 effective result 上报为 success，`suc_type=operator_intervention`，同时在
`result_data.raw_return_info` 保留不可变的设备原始失败。人工操作如果还需要真实设备动作，
后端必须另建 attempt 并正常调度，不能利用本消息让 Host 偷跑动作。

## 6. Edge REST/SSE

- `POST /api/v1/job/add` 固定返回 HTTP 409；动作只接受调度后端 WebSocket `job_start`。
- `GET /api/v1/error-decisions` 仅用于只读诊断 Host pending。
- `POST /api/v1/error-decisions/{decision_id}` 固定返回 HTTP 409；前端必须向调度后端提交。
- Edge SSE 可观测 `job_error_decision_required`、`job_error_decision_resolved` 和最终
  `job_status`，但不是决策写入口。

## 7. 注册表

每个 action 的 completion 固定包含 `error_policy`；未配置时为 `{}`。策略由后端用于前端
展示、retry 上限、超时默认动作和 fallback 调度，Host 只负责按异常 MRO 选择并上报对应
option 列表。
