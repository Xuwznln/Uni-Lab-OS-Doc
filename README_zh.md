<div align="center">
  <img src="docs/logo.png" alt="Uni-Lab Logo" width="200"/>
</div>

# Uni-Lab-OS

<!-- Language switcher -->

[English](README.md) | **中文**

[![GitHub Stars](https://img.shields.io/github/stars/deepmodeling/Uni-Lab-OS.svg)](https://github.com/deepmodeling/Uni-Lab-OS/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/deepmodeling/Uni-Lab-OS.svg)](https://github.com/deepmodeling/Uni-Lab-OS/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/deepmodeling/Uni-Lab-OS.svg)](https://github.com/deepmodeling/Uni-Lab-OS/issues)
[![GitHub License](https://img.shields.io/github/license/deepmodeling/Uni-Lab-OS.svg)](https://github.com/deepmodeling/Uni-Lab-OS/blob/main/LICENSE)

Uni-Lab-OS 是一个用于实验室自动化的综合平台，旨在连接和控制各种实验设备，实现实验流程的自动化和标准化。

## 核心特点

- 多设备集成管理
- 自动化实验流程
- 云端连接能力
- 灵活的配置系统
- 支持多种实验协议

## 文档

详细文档可在以下位置找到:

- [在线文档](https://deepmodeling.github.io/Uni-Lab-OS/)

## 支持的运行时

当前二进制包和开发环境统一使用 **Python 3.12.13（`cp312`）+ NumPy 2**。
ROS 2 Jazzy 是默认发行版（`robostack-jazzy`，mutex `0.15.*`），同时支持
ROS 2 Humble（`robostack-humble`，mutex `0.9.*`）。两个发行版必须使用独立
Conda 环境，不能混用 RoboStack channel。迁移和版本核对方法见
[运行时与 ABI 基线](docs/user_guide/runtime_baseline.md)。

## 快速开始

### 1. 配置 Conda 环境

Uni-Lab-OS 建议使用 `mamba` 管理环境。根据您的需求选择合适的安装包：

| 安装包 | 适用场景 | 包含内容 |
|--------|----------|----------|
| `unilabos` | **推荐大多数用户** | 完整安装包，开箱即用 |
| `unilabos-env` | 开发者（可编辑安装） | 仅环境依赖，通过 pip 安装 unilabos |
| `unilabos-full` | 仿真/可视化 | unilabos + ROS2 桌面版 + Gazebo + MoveIt |

```bash
# 创建新环境
mamba create -n unilab python=3.12.13
mamba activate unilab

# 方案 A：标准安装（推荐大多数用户）
mamba install uni-lab::unilabos -c uni-lab -c conda-forge -c robostack-jazzy

# 方案 B：开发者环境（可编辑模式开发）
mamba install uni-lab::unilabos-env -c uni-lab -c conda-forge -c robostack-jazzy
# 然后安装 unilabos 和依赖：
git clone https://github.com/deepmodeling/Uni-Lab-OS.git && cd Uni-Lab-OS
pip install -e .
uv pip install -r unilabos/utils/requirements.txt

# 方案 C：完整安装（仿真/可视化）
mamba install uni-lab::unilabos-full -c uni-lab -c conda-forge -c robostack-jazzy
```

如需 ROS 2 Humble，请新建独立环境，并将上述所有 `-c robostack-jazzy` 替换为
`-c robostack-humble`；Conda 会自动选择对应的 `humble_1` 构建。

**如何选择？**
- **unilabos**：标准安装，适用于生产部署和日常使用（推荐）
- **unilabos-env**：开发者使用，支持 `pip install -e .` 可编辑模式，可修改源代码
- **unilabos-full**：需要仿真（Gazebo）、可视化（rviz2）或 Jupyter Notebook

### 2. 克隆仓库（可选，供开发者使用）

```bash
# 克隆仓库（仅开发或查看示例时需要）
git clone https://github.com/deepmodeling/Uni-Lab-OS.git
cd Uni-Lab-OS
```

3. 启动 Uni-Lab 系统

请见[文档-启动样例](https://deepmodeling.github.io/Uni-Lab-OS/boot_examples/index.html)

4. 最佳实践

请见[最佳实践指南](https://deepmodeling.github.io/Uni-Lab-OS/user_guide/best_practice.html)

## 参考驱动实现

我们提供了四个可直接运行的示例设备包，均作为独立 GitHub 仓库维护（由
[LabDeviceTemplate](https://github.com/Xuwznln/LabDeviceTemplate) fork 生成）。克隆任一仓库，用
`--devices <包目录> --external_devices_only` 加载，编写自己的驱动时可启动运行、对照学习：

| 示例仓库 | 演示要点 |
|----------|----------|
| [LabDeviceLanDemo](https://github.com/Xuwznln/LabDeviceLanDemo) | 跨设备 `@subscribe` 订阅 + `call_device_action` 远程调用的局域网闭环（hub/sub 双进程） |
| [LabDeviceWorkstationDemo](https://github.com/Xuwznln/LabDeviceWorkstationDemo) | `hardware_interface` 代理——同一工作站内多个子设备共享同一通信端点：共享串口（默认 IO 方法名）与 Modbus `extra_info`（按设备注入各自 `slave_id`） |
| [LabDeviceExceptionDemo](https://github.com/Xuwznln/LabDeviceExceptionDemo) | 两条异常传播路径——点对点 `call_device_action` 的异常直接回到调用方 `try/except`；工作流任务失败则进入 Backend 错误决策链（`/api/v1/error-decisions`：重试 / 终止 / 人工介入）；另有业务级守卫返回与故障后可用性验证 |
| [LabDeviceSiteDemo](https://github.com/Xuwznln/LabDeviceSiteDemo) | host/slave 双进程——`@device(available_sites=...)` 固定位点（声明 → 注册表模板 → 权威位点实例 → 占用流转）、`@resource` 物料配合 `materials.*` 门面跨 HostLink 做物料 CRUD、`SiteSlot` 动作参数（前端 Site 选择器 uuid 或 label 便捷形态） |

每个仓库的 README 都附带分步启动教程及实测输出，并自带可终止的双运行时 smoke
（`python -m <包名>.smoke --backend hostlink|ros2`），由各仓库 CI 固定在指定 Uni-Lab-OS 提交上运行；
主仓库则在 `tests/e2e/readme_demos.py` 固定引用这四个包的已验证提交，并在 CI 里逐个端到端跑通：
注册表 `--check_mode`、`unilab graph create` 建图、真实 `unilab -g` 起微后端（双进程 demo 含 slave）、
`unilab graph list/download/upload` 读写 Graph Authority、管理 HTTP API 运行上报的 `@workflow`。底层的通信共享机制见
[最佳实践指南 §11.5](https://deepmodeling.github.io/Uni-Lab-OS/user_guide/best_practice.html)；
从零编写新驱动见[添加设备](https://deepmodeling.github.io/Uni-Lab-OS/developer_guide/add_device.html)。

## 消息格式

Uni-Lab-OS 使用预构建的 `unilabos_msgs` 进行系统通信。您可以在 [GitHub Releases](https://github.com/deepmodeling/Uni-Lab-OS/releases) 页面找到已构建的版本。

## 引用

如果您在学术研究中使用 [Uni-Lab-OS](https://arxiv.org/abs/2512.21766)，请引用：

```bibtex
@article{gao2025unilabos,
    title = {UniLabOS: An AI-Native Operating System for Autonomous Laboratories},
    doi = {10.48550/arXiv.2512.21766},
    publisher = {arXiv},
    author = {Gao, Jing and Chang, Junhan and Que, Haohui and Xiong, Yanfei and
              Zhang, Shixiang and Qi, Xianwei and Liu, Zhen and Wang, Jun-Jie and
              Ding, Qianjun and Li, Xinyu and Pan, Ziwei and Xie, Qiming and
              Yan, Zhuang and Yan, Junchi and Zhang, Linfeng},
    year = {2025}
}
```

## 许可证

本项目采用双许可证结构：

- **主框架**：GPL-3.0 - 详见 [LICENSE](LICENSE)
- **设备驱动** (`unilabos/devices/`)：深势科技专有许可证

完整许可证说明请参阅 [NOTICE](NOTICE)。

## 项目统计

### Stars 趋势

<a href="https://star-history.com/#deepmodeling/Uni-Lab-OS&Date">
  <img src="https://api.star-history.com/svg?repos=deepmodeling/Uni-Lab-OS&type=Date" alt="Star History Chart" width="600">
</a>

## 联系我们

- GitHub Issues: [https://github.com/deepmodeling/Uni-Lab-OS/issues](https://github.com/deepmodeling/Uni-Lab-OS/issues)
