# Phase 2 Isaac 4090 端到端验收 Runbook

## 前置条件

- Host: `ubuntu@172.20.0.39`
- Edge env: `/home/ubuntu/miniforge3/envs/unilab`
- Isaac env: `/home/ubuntu/miniforge3/envs/matterix`
- Test copy: `/tmp/Uni-Lab-OS-phase2-c3c5`
- Worker endpoint: `http://127.0.0.1:8092`
- Query gRPC: `127.0.0.1:50052`
- Scene: `/home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd`

不要覆盖 4090 上可能 dirty 的 `~/canonical/Uni-Lab-OS`。
`8091` 可能已有其他 Isaac demo 占用，本 runbook 使用 `8092`。

## 1. 同步代码

```bash
rsync -az --delete --exclude .git --exclude .pytest_cache ./ \
  ubuntu@172.20.0.39:/tmp/Uni-Lab-OS-phase2-c3c5/
```

## 2. 启动 Isaac Worker

不要使用 `pkill -f "unilabos.sim.backends.isaac.worker"`，这个模式可能匹配并杀掉自己的 SSH shell。需要停旧 worker 时先用 `pgrep -af "[u]nilabos.sim.backends.isaac.worker.*8092"` 找到精确 PID。

```bash
ssh ubuntu@172.20.0.39 '
  cd /tmp/Uni-Lab-OS-phase2-c3c5
  nohup /home/ubuntu/miniforge3/bin/conda run -n matterix env PYTHONPATH=. \
    python -m unilabos.sim.backends.isaac.worker \
      --host 127.0.0.1 \
      --port 8092 \
      --headless \
      --scene /home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd \
      --camera /World/Camera \
      --rpc-timeout-s 600 \
    > /tmp/isaac_worker_phase2_c3c5_8092.log 2>&1 &
  echo $! > /tmp/isaac_worker_phase2_c3c5_8092.pid
'
```

Isaac 启动较慢，等 60 秒后检查：

```bash
ssh ubuntu@172.20.0.39 'sleep 70; ss -ltnp "( sport = :8092 )"; curl -sS http://127.0.0.1:8092/health'
```

## 3. 启动 Edge

本地 graph + `--app_bridges fastapi` + 非 websocket 可以离线启动，不需要 `UNILAB_AK/UNILAB_SK`。

```bash
ssh ubuntu@172.20.0.39 '
  cd /tmp/Uni-Lab-OS-phase2-c3c5
  nohup /home/ubuntu/miniforge3/bin/conda run --no-capture-output -n unilab env PYTHONPATH=. \
    unilab --graph unilabos/test/experiments/mock_devices/mock_all.json \
      --config unilabos/config/example_config.py \
      --backend ros \
      --mode sim \
      --sim_rate 10 \
      --physics isaac \
      --physics_endpoint http://127.0.0.1:8092 \
      --physics_scene /home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd \
      --physics_timeout 300 \
      --query_labutopia_usd /home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd \
      --query_grpc_port 50052 \
      --app_bridges fastapi \
      --visual disable \
      --skip_env_check \
      --disable_browser \
      --test_mode \
      --port 8002 \
    > /tmp/unilab_edge_c5_50052.log 2>&1 &
  echo $! > /tmp/unilab_edge_c5_50052.pid
'
```

检查 gRPC：

```bash
ssh ubuntu@172.20.0.39 'sleep 80; ss -ltnp "( sport = :50052 or sport = :8002 )"; tail -n 120 /tmp/unilab_edge_c5_50052.log'
```

## 4. 运行 Smoke

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

## 5. 停止进程

```bash
ssh ubuntu@172.20.0.39 '
  kill $(cat /tmp/unilab_edge_c5_50052.pid) || true
  pgrep -af "[u]nilabos.sim.backends.isaac.worker.*8092"
'
```

## 6. 最终回归

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-phase2-c3c5 \
   python -m pytest tests/sim tests/queries tests/integration -q'
```

## 期望证据

- `ss -ltnp "( sport = :8092 )"` 能看到 Isaac worker。
- `ss -ltnp "( sport = :50052 )"` 能看到 query gRPC。
- smoke script 退出码为 0。
- smoke 输出的 `state.source` 和 `pose.source` 是 `physics_live:isaac`。
- `/tmp/labutopia-c5-e2e.png` 是 `PNG image data, 640 x 480, 8-bit/color RGBA`。
- 最终回归通过。
