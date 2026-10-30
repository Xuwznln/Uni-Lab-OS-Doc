---
orphan: true
---

# Kubernetes 部署与上线

:::{admonition} 阅读角色
- **业务负责人**：确认部署目的、设备包来源、运行环境、安全边界和成功标准。
- **开发或运维人员**：执行安装、配置、启动、升级和故障处理。
- **验收人员**：核对版本、运行状态、失败路径、安全配置和回滚能力。
:::

本页说明如何把 **Uni-Lab OS** 和用户设备包作为一套系统部署。操作页面、工作区服务、任务调度和设备运行能力统一属于 Uni-Lab OS，不作为多个独立产品交付。

:::{important}
部署清单或状态日志中可能出现 `backend`、`scheduler`、`edge` 等内部组件名称。这些名称仅用于进程编排、健康检查和故障定位；业务交付、版本管理和验收对象始终是 Uni-Lab OS 整体。
:::

## 1. 部署输入

上线前准备并锁定：

| 内容 | 要求 |
| --- | --- |
| Uni-Lab OS 镜像 | 唯一版本或 digest，包含已构建的操作页面 |
| 用户设备包 | `pyproject.toml`、`package.yaml`、Python 包目录和部署配置完整 |
| 启动图（Graph JSON） | 与本环境相符，模拟、测试和生产配置分开 |
| Secret | API 密钥、设备凭证和证书通过 Secret 注入 |
| 持久卷 | 保存工作流、库存、任务、设备执行记录和恢复状态 |
| 网络策略 | 只开放获准的页面和 API，设备控制通道保持在受控网络 |
| 验收记录 | 版本、启动图 校验值、测试范围、结果和回滚版本 |

## 2. 构建统一镜像

```bash
export OS_SOURCE="/path/to/unilab-os"
export DEVICE_PACKAGE_SOURCE="/path/to/user-device-package"
export OS_REVISION="$(git -C "$OS_SOURCE" rev-parse HEAD)"
export DEVICE_PACKAGE_REVISION="$(git -C "$DEVICE_PACKAGE_SOURCE" rev-parse HEAD)"
export RUNTIME_IMAGE="unilabos:${OS_REVISION:0:8}-${DEVICE_PACKAGE_REVISION:0:8}"
```

镜像应同时包含经过验证的 Uni-Lab OS 与设备包，并在构建记录中保存两个版本。设备包的 Python 依赖和模型资产必须进入镜像；不要依赖容器启动后临时联网下载。

构建前检查操作页面和设备包：

```bash
test -f "$OS_SOURCE/unilabos/app/web/static/console/index.html"
mamba run -n unilab unilab package inspect \
  --path "$DEVICE_PACKAGE_SOURCE" \
  --out /tmp/device-package-inspect
```

## 3. 配置一套 Workspace

整套 Uni-Lab OS 必须使用同一个：

- 用户设备包版本；
- Package Catalog；
- 启动图 内容；
- `runtimeMode` 和 `/api/v1/readiness` 返回的 `capabilities`；
- 设备连接配置和密钥代次。

启动图 中的 `127.0.0.1` 只表示同一容器内的服务。连接其他 Pod 或现场设备时，应使用受控的 Service DNS 或现场地址。不要让同一次运行混入不同版本的 启动图 或设备包。

## 4. 资源与持久化

至少为以下信息提供可恢复存储：

- 工作流定义、修订和发布状态；
- 物料、试剂、库存和位置台账；
- Task、Job、资源锁和人工确认；
- 设备命令、反馈、结果和未知状态恢复记录。

不要让多个实例同时写同一 SQLite 目录。系统没有完成外部化状态与多副本协议前，使用单副本和适合的更新策略。备份必须与 Uni-Lab OS、设备包和 启动图 版本一起记录。

## 5. 可选外部组件

PLC/OPC UA 仿真器、厂商上位机、数据库和追踪服务都属于按场景选择的外部依赖，不是另一套 Uni-Lab OS。

设备包要求 PLC 仿真器时：

1. 先启动 OPC UA Server；
2. 再启动可选握手代理；
3. 确认协议端点和点位表正确；
4. 最后启动 Uni-Lab OS 并检查设备连接。

详细要求见[PLC / OPC UA 仿真器](plc-sim.md)。

## 6. 健康检查

业务验收以 Uni-Lab OS 整体状态为准：

```bash
unilab workspace status \
  --workspace /workspace/user-device-package \
  --json
```

应同时满足：

- Uni-Lab OS 运行状态为 `ready`；
- 工作流 `loaded` 等于 `total`；
- 在线设备与当前 启动图 一致；
- 系统没有处于排空状态；
- 目标工作流零写入预检通过。

内部探针可以分别报告网页/API、工作流加载或设备连接情况，但任何单项成功都不能代表整套系统已经可用。

## 7. 网络与权限

公网只应通过受控网关到达批准的 Uni-Lab OS 页面和用户 API。部署时至少配置：

- HTTPS 与可信证书；
- 身份认证和按角色授权；
- Host、Origin 和代理来源限制；
- 对管理、恢复、内部调度和设备控制接口的阻断；
- NetworkPolicy、防火墙或安全组；
- Secret 轮换和访问审计。

PLC、OPC UA、数据库、可观测性和设备控制通道应保持在集群或实验室受控网络内。不要直接公开本地开发端口。

## 8. 上线顺序

1. 在测试命名空间安装相同镜像、设备包和 启动图。
2. 运行设备包检查和模拟工作流。
3. 核对持久卷、Secret、Service、入口和网络策略。
4. 启动 Uni-Lab OS，等待整体就绪和设备连接。
5. 执行零写入预检和批准的低风险验收流程。
6. 保存版本、配置和验收记录后开放业务入口。

Pod 为 `Running`、网页可以打开或单个内部组件健康，都不能替代以上验收。

## 9. 升级与回滚

升级前先让 Uni-Lab OS 停止接受新任务，并等待在途动作安全结束。随后：

1. 备份持久数据和当前部署清单；
2. 保存当前镜像 digest、设备包版本和 启动图 校验值；
3. 应用整套新版本，不单独替换某个内部组件；
4. 重复健康检查、设备连接和工作流预检；
5. 验收失败时回滚整套版本，并恢复与之匹配的数据和配置。

升级期间出现执行结果未知时，先按现场事实完成对账，不能通过重复提交任务来判断原动作是否执行。

## 10. 上线清单

- [ ] Uni-Lab OS 镜像和设备包版本已锁定；
- [ ] 启动图 与环境一致，敏感值未写入文件；
- [ ] 用户设备包检查无错误；
- [ ] 持久卷、备份和恢复已验证；
- [ ] Uni-Lab OS 整体就绪，设备数量符合 启动图；
- [ ] 工作流加载完整且预检通过；
- [ ] HTTPS、认证、授权和网络限制已生效；
- [ ] 模拟、单设备和业务验收记录完整；
- [ ] 排空、停止、升级和整套回滚流程已演练。

上线后由业务人员统一从 Uni-Lab OS 页面运行工作流；内部组件名称仅在管理员查看日志和状态时使用。
