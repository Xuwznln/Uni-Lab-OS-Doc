# 需求模板目录（Uni-Lab-OS）

> 基于 Harness Engineering 方法论，为 OS（Python/ROS2）定制。
> 每次新需求，复制本目录到 `docs/features/FXXX-功能名/` 填写。
>
> Agent 行为宪法: `docs/agent-workflow.md`
> 团队协作 SOP: 主仓 `product_designs/team_collaboration/`

## 使用方式

```bash
cp -r docs/templates docs/features/F001-your-feature
# 人类填写 requirement.md / interface-design.md / feature-list.json
# 启动 agent：/spec F001-your-feature
```

## 文件职责分工

| 文件 | 谁写 | 说明 |
|------|------|------|
| `requirement.md` | **HUMAN** | 需求规格：用户故事、验收标准 |
| `feature-list.json` | **HUMAN** 定义 / **CLAUDE** 改状态 | 子任务拆分与进度 |
| `interface-design.md` | **HUMAN** 定义 / **CLAUDE** 补充 | 设备/协议/接口设计 |
| `progress.md` | **CLAUDE** | 实现进度记录 |
| `checklist.md` | **CLAUDE** | 验证检查清单 |
