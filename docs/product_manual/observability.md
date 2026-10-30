# 监控、Trace 与日志（可选）

SigNoz 是一个“运行情况查看器”。安装并配置后，可以看到工作流有没有成功、哪一步变慢或报错，以及设备和服务产生的日志。

这部分不是 Uni-Lab OS 的必装项。需要监控时，用户需要自己安装 SigNoz；不安装也不影响设备包和工作流运行。

## 1. 安装 SigNoz

需要监控时，用户需要自己部署 SigNoz。源码仓库是：

[https://github.com/Uni-Lab-OS/signoz](https://github.com/Uni-Lab-OS/signoz)

### 本机 Docker 安装（最简单）

如果只是想在自己的电脑上使用 SigNoz，建议用 Docker 安装，不需要 Kubernetes。

先安装并启动 Docker Desktop，并给 Docker 分配至少 4 GB 内存。然后安装 SigNoz 的安装工具：

```bash
curl -fsSL https://signoz.io/foundry.sh | bash
```

在任意空文件夹新建 `casting.yaml`，内容如下：

```yaml
apiVersion: v1alpha1
kind: Installation
metadata:
  name: signoz
spec:
  deployment:
    flavor: compose
    mode: docker
```

执行安装：

```bash
foundryctl cast -f casting.yaml
```

看到容器都在运行后，打开 <http://localhost:8080/>。这就是 SigNoz 页面；Uni-Lab OS 后续要发送的地址通常是 `localhost:4317`（gRPC）或 `localhost:4318`（HTTP）。

如果 `foundryctl` 提示 Docker 不可用，先打开 Docker Desktop，再重新执行命令。

### 使用本仓库的 Kubernetes 部署

只有需要 Kubernetes 或需要使用仓库内定制配置时，才使用下面的方式。

安装前需要准备：

- Kubernetes 集群（也可以使用 Docker Desktop 的 Kubernetes）；
- `kubectl`、Kustomize 和 Docker；
- 可保存数据的磁盘（StorageClass）；
- PostgreSQL 和镜像仓库。

获取源码：

```bash
git clone https://github.com/Uni-Lab-OS/signoz.git
cd signoz
```

按下面顺序安装：

1. 进入下载好的 `signoz` 文件夹，再打开里面的 `deploy` 文件夹；
2. 在 `signoz/deploy` 中选择与你的环境对应的文件夹（本地 Docker Desktop 一般选择 `kubernetes-docker-desktop`）；
3. 打开所选文件夹里的 `README.md`，按照里面的说明填写存储位置和名称；
4. 按说明准备 SigNoz 的运行文件（第一次使用通常需要 Docker 帮你打包）；
5. 执行安装命令：

```bash
kubectl apply -k <部署目录>
```

如果 `signoz/deploy` 下的 `README.md` 要求先运行初始化脚本，就先运行脚本，再执行上面的安装命令。不要跳过这一步。

这里几个词的意思是：

- **`signoz/deploy`**：`signoz` 文件夹里放安装文件的文件夹；
- **README.md**：这个文件夹里的使用说明；
- **运行文件（镜像）**：SigNoz 启动所需的程序包；
- **命名空间**：Kubernetes 用来给这套 SigNoz 单独划出的名称。

部署完成后，确认以下内容都正常：

```bash
kubectl get pods -n <SigNoz 所在命名空间>
kubectl get svc -n <SigNoz 所在命名空间>
```

所有 Pod 进入 `Running` 或 `Completed` 后，记下 SigNoz 网页地址和数据接收地址，下一节会用到。

不要直接把没有登录保护的管理页面或数据接收端口暴露到公网。建议使用 HTTPS、登录认证和内网访问。

## 2. 配置 Uni-Lab OS

SigNoz 安装完成后，准备以下信息：

- SigNoz 网页地址：用浏览器打开自己安装的页面；
- 数据接收地址：让 Uni-Lab OS 把运行记录发送过去；
- 登录账号或 API Key：用于身份验证；
- 要查看的项目或环境名称。

准备好这些信息后，按下面方式启动 Uni-Lab OS：

```bash
export UNILABOS_OTEL_ENABLED=true
export UNILABOS_OTEL_LOGS_ENABLED=true
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="<你安装的 SigNoz 数据接收地址>"
export OTEL_EXPORTER_OTLP_PROTOCOL="grpc"
export OTEL_EXPORTER_OTLP_TRACES_INSECURE="false"
export OTEL_SERVICE_NAME="<你的设备包名称>"
export OTEL_DEPLOYMENT_ENVIRONMENT="<dev 或 production>"
```

这些设置的意思很简单：打开记录功能，告诉 Uni-Lab OS 把记录发到哪里，并给这台设备包起一个名称。

然后：

1. 启动 Uni-Lab OS；
2. 执行一次安全的测试工作流；
3. 打开你安装的 SigNoz 网页；
4. 按设备包名称搜索；
5. 点开记录，查看哪一步成功、变慢或失败。

如果使用 HTTP 地址，把协议改成 `http/protobuf`，并使用自己的 HTTP 接收地址。账号、API Key 等信息应由环境变量或 Kubernetes Secret 提供，不要写进代码。

本机 Docker 安装时，如果 Uni-Lab OS 也直接运行在本机，可使用 `localhost:4317` 或 `localhost:4318`。如果 Uni-Lab OS 运行在容器中，应使用 `host.docker.internal` 替代 `localhost`。

## 3. 使用 SigNoz

### 工作流过程（Trace）

Trace 可以理解为“一次工作流的完整过程”。它会把每个步骤串起来，方便回答：

- 哪一步最慢；
- 哪一步报错；
- 一个设备调用之后发生了什么。

### 日志

日志是程序运行时留下的文字记录。可以按时间、设备包名称和错误级别搜索，也可以从日志跳到对应的工作流过程。

日志中不要写入密码、Token、API Key 或完整设备凭证。

### 仪表盘和告警

管理员可以制作仪表盘，查看工作流成功率、平均耗时、设备连接错误、服务重启次数，以及 CPU、内存和磁盘使用情况。

## 4. 暂停或删除

暂时不想采集数据时，关闭下面的开关并重新启动 Uni-Lab OS：

```bash
export UNILABOS_OTEL_ENABLED=false
```

这只会停止记录上报，不会停止 Uni-Lab OS、设备或工作流。

删除自己部署的 SigNoz 前，先确认是否要保留历史数据。删除 ClickHouse 的存储盘后，之前的 Trace、日志和指标将无法恢复。

## 5. 常见问题

| 现象 | 先检查什么 |
| --- | --- |
| 网页能打开，但没有数据 | 接收地址、协议、账号/API Key，以及 Uni-Lab OS 是否已重启 |
| 有工作流过程，但没有日志 | 是否打开了 `UNILABOS_OTEL_LOGS_ENABLED` |
| 数据接收地址无法访问 | 网络、HTTPS 证书、防火墙和网关端口 |
| Kubernetes 一直启动不了 | 节点资源、存储盘和镜像仓库权限 |
