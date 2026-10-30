# GitLab CI/CD

项目 `lab-os/uni-lab-os` 的 CI/CD 配置与全部辅助脚本统一维护在 `main`。
在 Settings → CI/CD → General pipelines 中，将 CI/CD configuration file 设为：

```text
.gitlab-ci.yml@lab-os/uni-lab-os:main
```

原有 `codex/gitlab-tag-release` CI 入口已改为 include `main` 上的配置；已有项目设置无需修改即可使用主分支逻辑。

## Merge Request 单元测试（目标分支 `main`）

目标为 `main` 的 Merge Request 会并行跑 ruff 静态检查与两套无 ROS Python 单测（对齐 GitHub CI Check）：

- `lint_ruff`：`ci/ruff.sh`（`E9`/`F821`/`E722`/`W605`/`I`；不卡行宽 `E501`）
- `unit_tests_scheduler_core`：`ci/unit-tests-scheduler-core.sh`（`tests/scheduler_core` + 覆盖率门槛）
- `unit_tests_python_suite`：`ci/unit-tests-python-suite.sh`（先构建 embedded console，再按 `tests/ci_python_suite.txt` 跑 pytest，忽略 `tests/ci_ros_required.txt`，再卡覆盖率门槛）

其它目标分支的 MR、普通 push、以及 schedule 流水线不会创建流水线。
因配置文件固定取自 `main`，本能力在对应改动合入 `main` 之后才对后续 MR 生效。

## Release tag 自动打包

创建 `vMAJOR.MINOR.PATCH` 格式的 release tag（允许预发布后缀）后，自动并行构建：

- Linux x64：Constructor `.sh` 安装器，Runner tag `shared-cpu-abroad`；由 `k8s-runner-ack` 上传。
- macOS Apple Silicon：Constructor `.sh` 安装器，Runner tag `mac-runner`。
- Windows x64：Constructor `.exe` 安装器，Runner tag `win-runner-915`。

构建源码来自 tag 对应提交，CI 辅助脚本来自 `main`；不再限制 tag 必须属于旧产品 v2 分支。
源码需要包含对应 Conda recipes、Constructor 配置、frontend 及各平台烟测依赖。
安装器和 SHA256 校验文件保留为 GitLab artifacts 30 天，并沿用旧项目的 OSS 上传与版本防覆盖检查。
必需项目变量：`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_ENDPOINT`、`OSS_PREFIX`。
可选 `FEISHU_WEBHOOK_URL`：仅配置时才发送旧流程的发布通知。
若使用 Protected 变量，release tag 也需要设为 Protected，以便 Runner 获得这些变量。
Windows Runner 需要旧流程的 `D:\ub\build-tools`，macOS Runner 需要 `constructor-build` Conda 环境。

不包含 GitHub 同步 job，不需要 GitHub/GitLab 同步 token，也不创建 30 分钟定时任务。
`workflow` 仅允许 release tag 流水线，以及目标为 `main` 的 Merge Request 流水线。
已有 tags 不会因为本次配置迁移而自动重新发布。
