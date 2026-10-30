# 工作流数量结算与人工更正

数量型工作流复用现有库存余额、预留、台账和发件箱。设备执行成功后，先保存可重放的消费意图，再提交库存事务，最后完成作业投影；稳定的消费事件身份保证重放不会重复扣量。正常消费按实际回执结算，未上报实例沿用其冻结计划量。已提交的无效回执不能因重试时省略数量而退回计划量。

同维单位支持 `µL`、`μL`、`uL`、`mL`、`L` 和 `mg`、`g`、`kg`。跨质量和体积的换算仍受物态、密度和浓度合同限制。余额扣减使用统一浮点容差处理十进制耗尽，例如 `0.3 - 0.1 - 0.2` 收敛为零；超出容差的负余额会被拒绝。

## 结算失败时保留什么

单位无法安全换算、重复实例回执、超出预留或其他确定性数量错误会保留 `consume_pending` 意图及错误原因。作业设置 `uncertainty_reason=workflow_quantity_reconciliation_required`，任务进入 `control_status=waiting_reconciliation`；数量预留继续有效，执行锁标记为 `uncertain`，不能通过普通终态清理提前释放。

系统保存原始设备成功结果，人工更正只调整库存消费并重放该结果，不重新下发设备动作。原结果必须为 `succeeded` 且没有 `unknown_command_ids`；缺少原结果时，应先恢复结果投递，不能用数量更正声明设备已成功。物理执行仍不确定的作业继续走原有物理对账流程。

恢复扫描逐任务隔离确定性业务错误：有坏回执的任务继续等待核对，其他任务照常恢复。数据库连接、磁盘等基础设施错误仍向上传播。进程重启后可完成已有成功结果的库存和作业投影；不会自动重发此前设备命令，也不会自动续跑已经丢失运行时的后续步骤，任务按原有重启恢复规则收敛。

## 更正接口

`POST /api/v1/workflow-node-jobs/{job_uuid}/reconcile-quantity`

请求只包含 `reason` 和 `consumptions`。`reason` 去除首尾空白后必须非空；`consumptions` 为 1–500 个实例，`inventory_type` 为 `reagent` 或 `current_substance`，UUID 必须有效，`actual_quantity` 必须为有限非负数，单位必须非空且可安全换算。额外字段不被接受。示例 UUID 需替换为该作业实际预留的库存实例 UUID：

```json
{
  "reason": "核对泵日志，原回执 1.5 g 应为 1500 µL，设备已完成",
  "consumptions": [
    {
      "inventory_type": "reagent",
      "inventory_uuid": "60000000-0000-4000-8000-000000000001",
      "actual_quantity": 1500,
      "quantity_unit": "uL"
    }
  ]
}
```

普通单位或数量错误允许仅更正部分实例，未列出的实例保留原始用量。重复、非法回执已标记为 `needs_correction` 时，必须明确该作业全部已预留实例的实际用量。人工可据实记录超出本作业预留的用量，但不能产生负余额或侵占其他作业的活动预留。

成功返回 HTTP 200，业务包络为 `code=0`，`data` 是更新后的完整作业对象。以下只节选 `data` 中的身份和状态字段：

```json
{
  "code": 0,
  "data": {
    "uuid": "40000000-0000-4000-8000-000000000001",
    "workflow_task_uuid": "30000000-0000-4000-8000-000000000001",
    "status": "succeeded"
  }
}
```

业务错误同样使用 HTTP 200，必须检查响应 `code`：`1000` 表示输入或数量校验失败，`3002` 表示作业不存在，`3003` 表示当前状态或已提交事实冲突。错误包络形如 `{"code":3003,"error":{"msg":"…"}}`，不返回成功的 `data`。更正仍不满足库存约束时，预留和待结算状态继续保留。

## 幂等与审计

响应丢失或重启后，可重试相同请求。比较的是 DTO 规范化后的完整 `consumptions` 列表及去除首尾空白后的 `reason`；请保持列表顺序、实例范围、数值、单位和原因一致。库存已提交后，即使换算后数量相同，另一份请求也不能替换已落账事实。不同更正竞争时，以库存台账中实际接受的请求为准，单独保存了请求草稿不代表已经结算。

每条消费的 `meta_data.reconciliation` 保存原用量、原单位、更正原因、时间及完整 `request`。部分更正中未改动的实例同样保存该完整请求，证明整个作业消费接受了哪次更正；重放保留原审计时间和记录。工作流 Saga 保存前后记录历史。超耗事实另带 `overrun_quantity` 和 `overrun_authority`。

可用以下接口读取已落账消费，均沿用 `{"code":0,"data":...}` 包络：

- `GET /api/v1/workflow-node-jobs/{job_uuid}/inventory-consumptions`
- `GET /api/v1/workflow-tasks/{task_uuid}/inventory-consumptions`
- `GET /api/v1/reagents/{reagent_uuid}/inventory-consumptions`

## 分装已扣量时

`current_substance` 物理分装已在同一库存事务扣源瓶、加目标瓶后，通用工作流消费根据作业、源物质和源容器身份查找已提交的 `current_substance.aliquot_source` 台账。匹配后只补充消费归属记录，其 `quantity_delta=0`，并以 `physical_settlement_entry_uuid` 关联原扣量事实，不再次扣源瓶或增加其数量修订。

消费展示的实际量和前后余额来自原分装事实；该关联记录的增量为零并不表示实际消耗为零。明确上报的实际量必须与原分装事实一致，无法匹配、重复或损坏的物理事实继续要求核对。设备请求不能自行声明“已经扣量”来跳过库存权威。
