# 需求规格: [FEATURE_NAME]

> **Author: HUMAN** | Claude 只读，按此实现，不修改。
> 工作流程见 docs/agent-workflow.md

## 背景

<!-- 为什么做？解决什么问题？涉及哪类硬件/协议/调度？ -->

## 用户故事

```
As a [用户角色],
I want to [做什么],
So that [达到什么目的].
```

## 详细描述

<!-- happy path / 异常与边界 / 与现有设备驱动/协议/调度的交互 -->

## 验收标准（Given/When/Then，Claude 逐条验证）

### AC-1: [标准名称]
```
Given [前置条件],
When [操作],
Then [期望结果].
```

### AC-2: [标准名称]
```
Given ...
When ...
Then ...
```

## 涉及模块

- **设备驱动**: `unilabos/devices/[...]`
- **协议编译**: `unilabos/compile/[...]`
- **调度/后端**: `unilabos/[...]`
- **通信桥**: `[OPC-UA / Modbus / RS485 / WebSocket / FastAPI]`

## 正确性关注点（OS 特有）

<!-- 本需求有无需要 property-based 覆盖的数学/不变量？
     例：协议编译往返一致、坐标变换恒等、调度无资源冲突、超时逻辑 -->

## 依赖关系

- 前置功能:
- 外部依赖（真实硬件/服务）: <!-- 测试须 fake 掉 -->

## 验证方法

- [ ] `python -c "import unilabos"` 通过
- [ ] `pytest tests/[相关]` 通过
- [ ] 关键逻辑有 hermetic 测试（fake 硬件 + 可控时钟）
- [ ] 数学/不变量逻辑有 Hypothesis 覆盖（如适用）

## 不做什么（Out of Scope）

-
