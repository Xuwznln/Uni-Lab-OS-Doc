# 系统安装

:::{admonition} 阅读角色
- **首次使用者**：先安装并验证 Uni-Lab OS，再创建自己的第一个设备包。
- **开发或运维人员**：选择安装方式，维护环境、源码版本和启动配置。
- **验收人员**：确认安装来源、版本、初始 `dry-run` 和停止流程均可追溯。
:::

本页按“**安装 Uni-Lab OS → 下载示例设备包 → 启动示例**”的顺序操作。安装系统时不要求用户提前准备设备包；OS 命令可用后，再下载带有示例驱动、示例工作流和安全启动图的 `demo-lab` 设备包。

:::{important}
第一次启动固定使用示例设备包自带的 `dry-run` 启动图。它只用于验证 OS、设备包发现和工作流加载，不连接真实设备，也不能代替真机安全验收。
:::

## 1. 选择安装路径

按使用方式选择下面一条安装路径：

| 路径 | 适合谁 | 得到什么 |
| --- | --- | --- |
| **一键安装包** | 首次体验、演示和不修改 OS 源码的用户 | 已打包的 Uni-Lab OS 运行环境和命令行；本页提供 macOS Apple 芯片版和 Windows x64 版安装步骤 |
| **Conda/Mamba + `unilabos-env` + 源码** | 需要创建设备包、开发驱动或修改 OS 的用户 | ROS/Python 依赖和可编辑的 OS 源码 |

`unilabos-full` 等仿真和可视化环境不是默认安装路径。确实需要 Gazebo、RViz 或 MoveIt 时，再按[环境与运行配置](environment.md)选择。

## 2. 安装前检查

| 项目 | 要求 |
| --- | --- |
| 操作系统 | macOS 安装包适用于 macOS 11.0 及以上、Apple 芯片（M 系列）；Windows 安装包适用于 Windows x64（Intel/AMD 64 位）。请按操作系统和 CPU 架构选择对应安装包 |
| Python | 安装路径会准备兼容的 Python 3.11 环境 |
| 磁盘空间 | 至少 10 GB 可用空间 |
| 网络 | 源码安装时能访问 Conda 软件源和 Git 仓库 |
| Conda/Mamba | 一键安装包已包含环境管理工具，无需提前安装；源码安装需先准备，推荐 Miniforge |
| 设备包 | **不需要提前准备**，安装 OS 后再创建 |

使用一键安装包时，直接继续第 3 节。选择源码安装时，如果终端中还不能执行 `conda --version` 或 `mamba --version`，请先安装 Miniforge，并重新打开终端。

## 3. 路径一：使用一键安装包

一键安装包已包含 Python 运行环境和 Uni-Lab OS，不需要提前安装 Conda/Mamba，也不需要克隆 OS 源码。实验室设备包在 OS 安装完成后再准备。

### 3.1 下载安装包（macOS Apple 芯片）

下载 [Uni-Lab OS 0.11.4 · macOS ARM64 安装包](https://uni-lab.oss-cn-zhangjiakou.aliyuncs.com/uni-lab-installer/v0.3.0/macOS-ARM64/Uni-Lab-OS-0.11.4-osx-arm64.sh)，保存到系统的“下载”文件夹。

此安装包适用于 Apple 芯片（M 系列），不适用于 Intel Mac、Windows 或 Linux。Windows x64 用户请直接阅读第 3.3 节。

### 3.2 安装（macOS）

打开 macOS 的“终端”，执行以下命令，无需解压安装包：

```bash
cd "$HOME/Downloads"
bash Uni-Lab-OS-0.11.4-osx-arm64.sh
```

按终端提示阅读许可协议，确认同意后输入 `yes`；选择安装位置时，直接按回车使用默认目录 `$HOME/uni-lab-os`（即当前用户主目录下的 `uni-lab-os`）。若询问是否初始化 Conda，可以选择 `no`，后续按第 5 节手动激活环境即可。

如果默认目录已经存在，请选择另一个尚不存在、且不含空格的目录，不要覆盖或删除仍在使用的环境。若安装包保存在其他位置，请先进入实际下载目录再执行安装命令。

完成后继续执行第 5 节“验证 OS 安装”。

### 3.3 下载安装包（Windows x64）

下载 [Uni-Lab OS 0.11.4 · Windows x64 安装包](https://uni-lab.oss-cn-zhangjiakou.aliyuncs.com/uni-lab-installer/v0.3.0/Windows-X64/Uni-Lab-OS-0.11.4-win-64.exe)，保存到系统的“下载”文件夹。

此安装包适用于 Intel/AMD x64 架构的 Windows 电脑，已包含 Python 运行环境、Uni-Lab OS 和前端，无需提前安装 Python、Conda/Mamba 或克隆 OS 源码。

### 3.4 安装（Windows x64）

1. 双击下载的 `Uni-Lab-OS-0.11.4-win-64.exe`，无需解压。
2. 按向导阅读并同意许可协议，选择 `Just Me`（仅当前用户）安装。
3. 选择一个尚不存在、当前用户可写、路径较短且不含空格和中文的目录。下文以 `C:\UniLabOS` 为例；它是示例路径，请以实际选择的安装位置为准。不要覆盖或删除仍在使用的旧环境。
4. 保持“添加到 PATH”和“注册为默认 Python”等选项不勾选，点击安装，等待向导提示完成。安装时需要解压并配置运行环境，可能需要数分钟；完成前不要关闭安装程序。
5. 安装完成后，打开 Windows 的“命令提示符”（CMD），继续执行第 5 节“验证 OS 安装”。

## 4. 路径二：安装 `unilabos-env` 后拉取源码

这条路径适合设备包和驱动开发。`unilabos-env` 只提供 ROS2、Python 依赖和 `uv` 等开发环境；Uni-Lab OS 本身随后从仓库以可编辑模式安装。

### 4.1 安装来源与源码分支

| 内容 | 安装或拉取地址 | 版本或分支 |
| --- | --- | --- |
| **Conda/Mamba** | [Miniforge 官方安装包](https://github.com/conda-forge/miniforge/releases/latest) | 按操作系统和 CPU 架构选择最新的 `Miniforge3` 安装包；不对应 Uni-Lab OS 分支 |
| **`unilabos-env`** | [Anaconda.org：`uni-lab/unilabos-env`](https://anaconda.org/uni-lab/unilabos-env) | 从 `uni-lab` channel 安装；不对应 Git 分支 |
| **Uni-Lab OS 源码** | [GitHub：`Uni-Lab-OS/Uni-Lab-OS`](https://github.com/Uni-Lab-OS/Uni-Lab-OS) | `product/durable-scheduler-kernel-v2` |

Conda/Mamba 是环境管理工具，`unilabos-env` 是 Conda 环境依赖包，Uni-Lab OS 才是需要指定 Git 分支的源码仓库。三者不是同一个安装包，也不共用一个“分支”概念。

### 4.2 创建并激活环境

下面的 `unilabos-env` 安装方式与仓库根目录的 `README_zh.md` 保持一致：

```bash
mamba create -n unilab python=3.11.14
mamba activate unilab
mamba install uni-lab::unilabos-env -c robostack-staging -c conda-forge
```

如果使用 Conda 激活环境，也可以执行 `conda activate unilab`。后续所有命令必须在同一个 `unilab` 环境中运行。

### 4.3 克隆 `kernel-v2` 分支并安装 Uni-Lab OS

```bash
git clone \
  --branch product/durable-scheduler-kernel-v2 \
  --single-branch \
  https://github.com/Uni-Lab-OS/Uni-Lab-OS.git
cd Uni-Lab-OS
pip install -e .
uv pip install -r unilabos/utils/requirements.txt
```

已经克隆过该仓库时，在仓库目录内更新同一分支：

```bash
git switch product/durable-scheduler-kernel-v2
git pull --ff-only origin product/durable-scheduler-kernel-v2
```

:::{note}
`pip install -e .` 让本地源码修改立即生效；`uv pip install` 安装运行所需的 pip 依赖。以后切换仓库分支或拉取新代码时，应重新验证依赖和命令版本。
:::

## 5. 验证 OS 安装

先按安装方式激活环境。每次新打开终端时，都需要重新激活。

**一键安装包（macOS）：**

```bash
source "$HOME/uni-lab-os/bin/activate" "$HOME/uni-lab-os"
```

如果安装时修改了目录，请将命令中的两处 `$HOME/uni-lab-os` 都替换为实际安装路径。

**一键安装包（Windows x64，CMD）：**

```bat
call "C:\UniLabOS\Scripts\activate.bat" "C:\UniLabOS"
```

请将命令中的两处 `C:\UniLabOS` 都替换为实际安装路径。此命令激活安装包自带的环境，不需要提前执行 `conda init`。

**源码安装：**

```bash
conda activate unilab
```

激活后，两种安装方式都执行以下命令（macOS 终端和 Windows CMD 通用）：

```bash
python -c "import sys; print(sys.executable)"
python -c "import unilabos; print(unilabos.__version__)"
unilab --help
unilab workspace --help
```

确认 Python 路径指向刚安装的环境、版本号与所装版本一致，且两条帮助命令能正常显示。此时 OS 安装验证完成，即使还没有准备设备包，也可以继续下一节。

## 6. 准备并安装设备包

安装 OS 后，按用途选择下面一种方式准备设备包，再执行本节末尾的安装和检查命令。

### 方式一：命令创建最小设备包

准备开发自己的设备包时，先确认当前环境包含初始化命令，再创建一个新目录：

```bash
unilab workspace init --help
unilab workspace init --output ./sample-lab
export DEVICE_PACKAGE_ROOT="$(pwd)/sample-lab"
cd "$DEVICE_PACKAGE_ROOT"
```

生成内容包括示例设备、`echo` 动作、普通工作流、启动图和测试，共 15 个文件。目标目录必须尚不存在。`--name`、`--json` 参数和完整文件清单见[初始化工作区](workspace.md#初始化工作区)。

若当前版本没有 `init` 子命令，使用包含该功能的版本，或选择下面的示例包下载方式。

### 方式二：下载完整演示设备包

希望体验“标准样品称量分装”流程时，下载已准备好的示例设备包：

:::{admonition} 示例文件下载
:class: note

- {download}`下载 demo-lab.zip <_static/example-package/demo-lab.zip>`
- [查看示例设备包说明](demo-lab.md)
:::

将下载的压缩包放到准备使用的目录，解压后进入设备包：

```bash
unzip demo-lab.zip
export DEVICE_PACKAGE_ROOT="$(pwd)/demo-lab"
cd "$DEVICE_PACKAGE_ROOT"
```

### 安装并检查所选设备包

完成任意一种准备方式后，在对应设备包根目录运行：

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
unilab package inspect --path . --out dist/inspect
```

这些检查通过后，只能说明教学设备包可以被发现和静态解析，不表示任何真实设备已经接入。

## 7. 以安全模式启动

第一次启动显式指定所选设备包自带的启动图。以上两种方式都提供 `deployment/graphs/dry-run.json`：

```bash
unilab workspace start \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component all \
  --graph "$DEVICE_PACKAGE_ROOT/deployment/graphs/dry-run.json" \
  --runtime-mode dry-run \
  --json
```

查看状态：

```bash
unilab workspace status \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --json
```

如果启动失败，先查看组件日志，不要改用真实设备配置反复尝试：

```bash
unilab workspace logs \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component backend \
  --json
```

结束体验后停止工作区：

```bash
unilab workspace stop \
  --workspace "$DEVICE_PACKAGE_ROOT" \
  --component all \
  --json
```

## 8. 从示例转为真实设备包

确认 OS、设备包示例和 `dry-run` 都能正常工作后，再按以下顺序替换示例内容：

1. 填写 `DEVICE_PACKAGE_REQUIREMENTS.md`，记录设备型号、协议、动作、单位和安全边界；
2. 按[设备接入模板](device-template.md)实现真实驱动；
3. 按[物料定义模板](material-template.md)登记物料与位置；
4. 新建独立的联调或生产启动图，不修改教学 `dry-run.json` 来承载生产地址；
5. 按[工作流运行](workflow.md)编写和验证工作流；
6. 完成[设备包验收](scenario-guide.md)后，才进入受控真机运行。

安装和初次启动的最短顺序始终是：**先安装 OS，再创建设备包，最后启动设备包**。用户不需要为了安装 Uni-Lab OS 预先取得一个现场设备包。
