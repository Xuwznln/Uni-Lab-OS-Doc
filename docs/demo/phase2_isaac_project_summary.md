# Phase 2 Isaac Bridge 项目改动与实现总览

更新时间：2026-06-02

本文档汇总本轮 Phase 2 Isaac Bridge 的整体目标、代码改动、已实现能力、4090 验收结果和演示命令。对应计划文档：

- `docs/superpowers/plans/2026-06-02-phase2-isaac-c1-c2.md`
- `docs/superpowers/plans/2026-06-02-phase2-isaac-c3-worker.md`
- `docs/superpowers/plans/2026-06-02-phase2-isaac-c4-edge-integration.md`
- `docs/superpowers/plans/2026-06-02-phase2-isaac-c5-e2e-render.md`
- `docs/demo/phase2_isaac_e2e_4090.md`

## 一句话结果

我们已经完成 Route A 链路：Uni-Lab-OS 可以通过 CLI 接入 Isaac worker，edge 运行时持有 physics backend，Query API 能从 `physics_live:isaac` 查询物理状态，并且能在 4090 上从 LabUtopia Isaac 场景生成真实 PNG 画面。

## 4090 运行环境

- Host：`ubuntu@172.20.0.39`
- GPU：NVIDIA GeForce RTX 4090，约 24GB 显存
- Edge 环境：`/home/ubuntu/miniforge3/envs/unilab`
- Isaac 环境：`/home/ubuntu/miniforge3/envs/matterix`
- 远端测试副本：`/tmp/Uni-Lab-OS-phase2-c3c5`
- Isaac worker：`http://127.0.0.1:8092`
- Query gRPC：`127.0.0.1:50052`
- FastAPI：`0.0.0.0:8002`
- LabUtopia scene：`/home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd`

注意：4090 上 `8091` 可能已有其他 Isaac demo 占用，本轮验收使用 `8092`。

## 已实现能力

### C1/C2：Physics Contract 与 Isaac Bridge 基础

实现内容：

- 扩展 `PhysicsBackend` 协议，新增 `load_scene(scene_path)` 和 `render(camera, width, height)`。
- 新增 fake physics backend，用于本地测试、runtime 接线测试和虚拟设备测试。
- 新增 Isaac HTTP bridge，把 edge 侧 physics 调用转成 worker RPC。
- 新增 JSON RPC 协议编码/解码，支持 success/error 响应。

关键文件：

- `unilabos/sim/physics_backend.py`
- `unilabos/sim/context.py`
- `unilabos/sim/runtime.py`
- `unilabos/sim/backends/fake_physics.py`
- `unilabos/sim/backends/isaac_bridge.py`
- `unilabos/sim/backends/isaac/protocol.py`

### C3：Isaac Worker 独立运行

实现内容：

- 新增独立 Isaac worker，可在 `matterix` 环境中运行。
- Worker 暴露 HTTP `/health` 和 `/rpc`。
- 支持 `reset`、`step`、`load_scene`、`get_observation`、`set_command`、`attach_rigid_body`、`get_joint_states`、`apply_wrench`、`render`。
- 真实 Isaac API 调用通过主线程队列执行，避免 HTTP worker 线程直接调用 Kit/Usd 导致卡死。
- 使用 replicator RGB annotator 生成真实 PNG，不再把 fallback 当作验收通过。
- `load_scene/reset` 会清理 render cache，避免旧 stage 的 render product 失效。

关键文件：

- `unilabos/sim/backends/isaac/worker.py`
- `unilabos/sim/backends/isaac/worker_http.py`
- `scripts/smoke_isaac_worker.py`
- `tests/sim/backends/test_isaac_worker_cli.py`
- `tests/sim/backends/test_isaac_worker_http.py`
- `tests/sim/backends/test_isaac_worker_protocol.py`
- `tests/sim/backends/test_isaac_worker_smoke_script.py`

### C4：CLI、RuntimeContext、HAL/虚拟设备接线

实现内容：

- CLI 新增：
  - `--physics none|fake|isaac`
  - `--physics_endpoint`
  - `--physics_scene`
  - `--physics_timeout`
- Backend 启动时自动构建 physics backend，并写入 `RuntimeContext`。
- `RuntimeContext` 记录 physics 名称、endpoint、scene、timeout。
- `build_physics_backend()` 统一创建 fake/isaac backend。
- UR HAL sim 模式默认读取 `RuntimeContext.physics`。
- 虚拟多通阀动作会 dispatch 到 physics backend。
- 支持本地 graph + `fastapi` only 的离线启动，不再强制要求 AK/SK；如果启用 websocket 或远程资源，仍需要云端凭证。

关键文件：

- `unilabos/app/main.py`
- `unilabos/app/backend.py`
- `unilabos/sim/backends/factory.py`
- `unilabos/sim/device_physics.py`
- `unilabos/hal/adapters/ur_adapter.py`
- `unilabos/devices/virtual/virtual_multiway_valve.py`
- `tests/sim/test_cli_runtime.py`
- `tests/sim/test_backend_physics_configuration.py`
- `tests/sim/test_device_physics.py`
- `tests/sim/test_virtual_device_clock.py`
- `tests/queries/test_ur_adapter.py`

### C5：端到端 Query 与真画面验收

实现内容：

- 新增 `PhysicsLiveSource`，从 `RuntimeContext.physics.get_observation(target)` 转成 Query API 的 `State` 和 `Pose`。
- 支持 observation 中的 `pose` dict，也支持 UR 风格 `tcp_pose/tool_pose`。
- Query startup 中接入 physics source，顺序为：
  1. ROS live source
  2. Physics live source
  3. LabUtopia static source
- 新增 C5 smoke 脚本，通过 gRPC query state/pose，再通过 Isaac bridge render PNG。
- Smoke 脚本要求完整 PNG signature + IEND，fallback 数据不能通过验收。

关键文件：

- `unilabos/queries/physics_live_source.py`
- `unilabos/queries/__init__.py`
- `unilabos/ros/main_slave_run.py`
- `scripts/smoke_sim_isaac_edge.py`
- `tests/queries/test_physics_live_source.py`
- `tests/integration/test_edge_query_wiring.py`
- `tests/integration/test_smoke_sim_isaac_edge_script.py`

## 4090 验收结果

### 目标测试

命令：

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-phase2-c3c5 \
   python -m pytest \
     tests/sim/backends/test_isaac_worker_protocol.py \
     tests/sim/backends/test_isaac_worker_http.py \
     tests/sim/backends/test_isaac_worker_cli.py \
     tests/sim/backends/test_isaac_worker_smoke_script.py \
     tests/sim/backends/test_factory.py \
     tests/sim/test_cli_runtime.py \
     tests/sim/test_runtime_configuration.py \
     tests/sim/test_backend_physics_configuration.py \
     tests/sim/test_device_physics.py \
     tests/sim/test_virtual_device_clock.py \
     tests/queries/test_ur_adapter.py \
     tests/queries/test_physics_live_source.py \
     tests/integration/test_smoke_sim_isaac_edge_script.py \
     tests/integration/test_edge_query_wiring.py \
     -q'
```

结果：

```text
49 passed, 1 warning
```

### C5 端到端 smoke

命令：

```bash
ssh ubuntu@172.20.0.39 '
  cd /tmp/Uni-Lab-OS-phase2-c3c5
  /home/ubuntu/miniforge3/bin/conda run -n unilab env PYTHONPATH=. \
    python scripts/smoke_sim_isaac_edge.py \
      --grpc 127.0.0.1:50052 \
      --physics-endpoint http://127.0.0.1:8092 \
      --state-target /World \
      --pose-target /World \
      --camera /World/Camera \
      --physics-timeout-s 300 \
      --out /tmp/labutopia-c5-e2e.png
  file /tmp/labutopia-c5-e2e.png
  ls -lh /tmp/labutopia-c5-e2e.png
'
```

结果要点：

- `state.source`：`physics_live:isaac`
- `pose.source`：`physics_live:isaac`
- PNG：`/tmp/labutopia-c5-e2e.png`
- 文件类型：`PNG image data, 640 x 480, 8-bit/color RGBA, non-interlaced`
- 文件大小：约 `313K`

### 相关全量回归

命令：

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-phase2-c3c5 \
   python -m pytest tests/sim tests/queries tests/integration -q'
```

结果：

```text
151 passed, 2 warnings
```

### Diff 检查

命令：

```bash
git diff --check
```

结果：通过，无 trailing whitespace 等格式问题。

## 录屏演示命令

### 1. 展示 GPU 和项目路径

```bash
hostname
nvidia-smi
cd /tmp/Uni-Lab-OS-phase2-c3c5
/home/ubuntu/miniforge3/bin/conda run -n unilab python --version
```

### 2. 展示 Isaac worker

```bash
ss -ltnp "( sport = :8092 )"
curl -sS http://127.0.0.1:8092/health
```

### 3. 展示 Edge 和 Query API

```bash
ss -ltnp "( sport = :50052 or sport = :8002 )"
tail -n 80 /tmp/unilab_edge_c5_50052.log
```

重点看：

```text
Runtime mode initialized: mode=sim ... physics=isaac ...
Query API gRPC server started at :50052
```

### 4. 展示 query 物理态和真 PNG

```bash
/home/ubuntu/miniforge3/bin/conda run -n unilab env PYTHONPATH=. \
  python scripts/smoke_sim_isaac_edge.py \
    --grpc 127.0.0.1:50052 \
    --physics-endpoint http://127.0.0.1:8092 \
    --state-target /World \
    --pose-target /World \
    --camera /World/Camera \
    --physics-timeout-s 300 \
    --out /tmp/labutopia-c5-e2e-recording.png
file /tmp/labutopia-c5-e2e-recording.png
ls -lh /tmp/labutopia-c5-e2e-recording.png
```

### 5. 展示测试回归

```bash
/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-phase2-c3c5 \
  python -m pytest tests/sim tests/queries tests/integration -q
```

## 当前运行状态

截至本轮验收结束，4090 上保留了以下进程用于继续录屏或人工检查：

- Isaac worker：`127.0.0.1:8092`
- Query gRPC：`127.0.0.1:50052`
- FastAPI：`0.0.0.0:8002`

PNG 验收文件：

- `/tmp/labutopia-phase2-c3c5.png`
- `/tmp/labutopia-c5-e2e.png`

## 注意事项

- 不要使用 `pkill -f "unilabos.sim.backends.isaac.worker"`，该模式可能匹配并杀掉自己的 SSH shell。需要停止 worker 时，先用 `pgrep -af "[u]nilabos.sim.backends.isaac.worker.*8092"` 找精确 PID。
- `8091` 可能被旧 Isaac demo 占用；本轮使用 `8092`。
- 本地 graph + `--app_bridges fastapi` + 非 websocket 可以离线启动，不需要 AK/SK。
- 如果启用 websocket、远程资源或云同步，仍需要有效 AK/SK。
- 本地 macOS 默认 `python3` 是 3.9，缺少本仓库 Python 3.11 依赖；正式验证使用 4090 的 `unilab` conda 环境。
- Worker 的 fallback render 只用于调试，不作为 C5 验收通过条件；smoke 脚本要求完整 PNG。

## 后续可继续增强

- 把更多 Isaac prim/device 映射成 Uni-Lab-OS 设备状态，而不仅是 `/World` 级别 observation。
- 增加更完整的 joint state、contact、wrench、rigid body 真实物理读写。
- 把 camera path、resolution、scene 作为标准 demo 配置管理。
- 为 worker 增加更清晰的日志和 render 错误诊断。
- 将当前工作树按 C3/C4/C5 拆分 commit，便于后续 PR review。
