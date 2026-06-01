# Robo-UniLabOS Deferred Assets Handoff

日期：2026-06-01

本文记录本轮没有整合进 `LeapLab/Uni-Lab-OS` 最小闭环的扩展内容：Feetech/RDK 机械臂 HAL、LabUtopia 查询源、resource-map、robot assets，以及 4090 工作站上的相关资源位置。

本轮已整合的是 Phase 10 仿真核心和 Phase 13 query/HAL 核心。下列内容仍保留在 `/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS` 和 4090 工作站上，后续需要单独成批迁入、测试和校准。

## 4090 总入口

已在 2026-06-01 用 SSH 快速确认这些路径存在：

```bash
ssh ubuntu@172.20.0.39

/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS
/home/ubuntu/labsim/LabUtopia_repro
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated
/home/ubuntu/miniforge3/envs/unilab
/home/ubuntu/miniforge3/envs/matterix
```

环境约定：

- `unilab`：普通 Python 单元测试环境。
- `matterix`：Isaac/LabUtopia headless smoke 环境，可用 `cuda:0`。
- 远端代码根目录：`/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS`。
- 远端生成物根目录：`/home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated`。

## Feetech/RDK RoboArm

### 本地来源

```bash
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/unilabos/hal/adapters/feetech_roboarm.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/scripts/validate_feetech_roboarm_hal.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/tests/queries/test_feetech_roboarm_hal.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/docs/feetech_roboarm_hil_validation_2026-05-25.md
```

### 4090 位置

```bash
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/unilabos/hal/adapters/feetech_roboarm.py
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/scripts/validate_feetech_roboarm_hal.py
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/docs/feetech_roboarm_hil_validation_4090_2026-05-25.json
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/docs/feetech_roboarm_hil_validation_tunnel_2026-05-25.json
```

### 硬件和网络状态

- Leader：`sunrise@192.168.1.112`
- Follower：`sunrise@192.168.1.110`
- Leader read-only endpoint：`http://192.168.1.112:8090/api/leader/state`
- 4090 tunnel endpoint：`http://127.0.0.1:19090/api/leader/state`

4090 仍不能直接路由到 `192.168.1.x` 机械臂网络；直接访问 `http://192.168.1.112:8090/...` 会 timeout。已验证可通过 Mac 到 4090 的反向 SSH tunnel 转发：

```bash
ssh -fN -M -S /tmp/robo_unilabos_4090_tunnel.sock \
  -o ExitOnForwardFailure=yes \
  -R 127.0.0.1:19022:192.168.1.112:22 \
  -R 127.0.0.1:19122:192.168.1.110:22 \
  -R 127.0.0.1:19090:192.168.1.112:8090 \
  ubuntu@172.20.0.39
```

验证命令：

```bash
cd /home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS
PYTHONPATH=. python scripts/validate_feetech_roboarm_hal.py \
  --endpoint http://127.0.0.1:19090/api/leader/state \
  --samples 3 \
  --output docs/feetech_roboarm_hil_validation_tunnel_2026-05-25.json
```

边界：目前只验证 read-only state query，没有验证 torque enable、follower actuation、teleop、gripper command 或 transaction safety。

## LabUtopia

### 本地来源

```bash
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/unilabos/queries/labutopia/
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/tests/queries/test_labutopia_sources.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/tests/fixtures/labutopia/
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/scripts/remote_validate_labutopia_phase13.sh
```

关键模块：

- `asset_cards.py`：读取 generated asset cards。
- `task_configs.py`：读取 LabUtopia task YAML，映射到 action/target/affordance。
- `scene_source.py`：把 task config 与 USD/Isaac-backed scene 查询合并。
- `usd_source.py`：直接 USD prim pose/state/bbox 查询。
- `asset_card_generator.py`：生成 Robo-UniLabOS asset cards。
- `task_report.py`：生成 task readiness report。
- `action_smoke.py`：生成 contract-level action smoke，不执行物理动作。
- `isaac_headless_smoke.py`：Isaac AppLauncher/headless smoke。

### 4090 位置

```bash
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/unilabos/queries/labutopia/
/home/ubuntu/labsim/LabUtopia_repro
/home/ubuntu/labsim/LabUtopia_repro/config
/home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_003/lab_003.usd
```

已确认存在的 generated 产物：

```bash
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated/labutopia_asset_cards_after_phase13_fixes_isaac
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated/labutopia_task_report_after_phase13_fixes_isaac.json
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated/labutopia_isaac_smoke_after_phase13_fixes.json
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated/labutopia_asset_cards_after_sim_expansion_isaac
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated/labutopia_task_report_after_sim_expansion_isaac_headless.json
```

典型验证命令：

```bash
cd /home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh

conda run -p /home/ubuntu/miniforge3/envs/unilab \
  python -m pytest tests/queries/test_labutopia_sources.py tests/robo_unilabos/test_cli.py -q

timeout 240 env PYTHONPATH=. conda run -p /home/ubuntu/miniforge3/envs/matterix \
  python -m unilabos.queries.labutopia.task_report \
  --config-dir /home/ubuntu/labsim/LabUtopia_repro/config \
  --labutopia-root /home/ubuntu/labsim/LabUtopia_repro \
  --isaac-headless \
  --isaac-steps 1 \
  --output /home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated/labutopia_task_report_after_phase13_fixes_isaac.json \
  --indent 2

timeout 240 env PYTHONPATH=. conda run -p /home/ubuntu/miniforge3/envs/matterix \
  python -m unilabos.queries.labutopia.asset_card_generator \
  --config-dir /home/ubuntu/labsim/LabUtopia_repro/config \
  --labutopia-root /home/ubuntu/labsim/LabUtopia_repro \
  --output-dir /home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated/labutopia_asset_cards_after_phase13_fixes_isaac \
  --isaac-headless \
  --isaac-steps 1 \
  --clean
```

边界：`position_range` fallback 不是 USD 真值；`action_smoke` 是 contract plan，不执行 robot motion 或 physics contact。

## Resource Map

### 本地来源

```bash
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/unilabos/robo_unilabos/resource_map.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/unilabos/robo_unilabos/models.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/unilabos/robo_unilabos/examples/balance_weighing_resource_map.json
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/unilabos/queries/resource_map_source.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/tests/robo_unilabos/test_resource_map.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/tests/queries/test_resource_map_query_engine.py
```

### 4090 位置

```bash
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/unilabos/robo_unilabos/resource_map.py
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/unilabos/robo_unilabos/examples/balance_weighing_resource_map.json
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/unilabos/queries/resource_map_source.py
```

迁入注意事项：

- `resource_map_source.py` 依赖 `unilabos.robo_unilabos.resource_map`，不能只拷 query source。
- 需要同时迁入 `unilabos/robo_unilabos/` 的 models/operations/CLI 基础层，或把 query source 改成只依赖当前 registry/resource tree。
- 当前 `LeapLab/Uni-Lab-OS` 已整合的 core query layer 没有包含 resource-map source。

## Robot Assets

### 本地来源

```bash
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/robot_assets/roboarm_chem_04/
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/unilabos/queries/robot_asset.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/unilabos/queries/urdf_robot_model.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/scripts/validate_roboarm_urdf_query.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/tests/queries/test_robot_asset.py
/Users/newtides/Robo-UniLabOS/13_phase3_robo_unilabos_query_api/Uni-Lab-OS/tests/queries/test_urdf_robot_model_source.py
```

`roboarm_chem_04` 包含：

```bash
robot_assets/roboarm_chem_04/asset_manifest.json
robot_assets/roboarm_chem_04/urdf/04_source.urdf
robot_assets/roboarm_chem_04/urdf/roboarm_chem_04_query.urdf
robot_assets/roboarm_chem_04/meshes/*.STL
```

### 4090 位置

```bash
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/robot_assets/roboarm_chem_04
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/scripts/validate_roboarm_urdf_query.py
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/docs/roboarm_chem_04_asset_static_validation_4090_2026-05-25.json
/home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS/docs/roboarm_chem_04_asset_live_validation_2026-05-25.json
```

验证命令：

```bash
cd /home/ubuntu/lab4090/projects/robo-unilabos-phase13/Uni-Lab-OS

PYTHONPATH=. python scripts/validate_roboarm_urdf_query.py \
  --asset robot_assets/roboarm_chem_04 \
  --raw-position-json '{"base":2048,"s1":2100,"s2":1996,"e1":2010,"e2":2086,"wrist_p":2048,"wrist_r":2048,"gripper":2000}' \
  --output docs/roboarm_chem_04_asset_static_validation_4090_2026-05-25.json

PYTHONPATH=. python scripts/validate_roboarm_urdf_query.py \
  --asset robot_assets/roboarm_chem_04 \
  --leader-endpoint http://127.0.0.1:19090/api/leader/state \
  --output docs/roboarm_chem_04_asset_live_validation_2026-05-25.json
```

边界：

- `tool0` 是 provisional operation frame，来自 `Link_5` 加 14 cm offset。
- Feetech ticks 到 URDF logical joints 是 read-only kinematic smoke mapping，不是 calibrated control mapping。
- 清理后的 URDF 用于 query/FK validation，不代表 high-fidelity dynamics。
- 没有验证 ROS `robot_state_publisher`、TF runtime、actuation、controller plugins 或 transaction safety。

## 建议迁入顺序

1. Robot assets 和 URDF query source：依赖最少，先让 `query_pose("roboarm_chem_04.tool0")` 静态通过。
2. Resource map：迁入 `unilabos/robo_unilabos/` 基础层，再接 `ResourceMapSource`。
3. Feetech read-only HAL：只做 state query，保持 `motion_commands_sent: false` 的安全边界。
4. LabUtopia：最后迁入，因为涉及 Isaac/pxr/matterix 环境、fixtures、CLI 和大量生成物。

## 后续检查清单

- 比较本地来源和 4090 checkout 是否一致。
- 不要把 `after_sim_expansion` 和 `after_phase13_fixes` 两套 generated 产物混用到同一个测试结论里。
- 重新生成 task report 和 asset cards，不只读旧 JSON。
- 检查 `navigation__lab_3.json` 只包含 `Level5_Navigation`、`navigation_goal`、`move_to`。
- 检查 Feetech live query 必须走 tunnel 或先修 4090 到 `192.168.1.x` 的路由。
- 对外只声明 query/HAL/asset information layer 已验证，不声明真实机器人执行闭环已完成。
