# Phase 2 Isaac C5 端到端与真画面验收实施计划

> **给执行 agent 的要求:** 实施本计划时必须使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`，逐项执行并用 checkbox 记录状态。

**目标:** 在 4090 上证明完整 Route A 链路：Isaac worker 在 `matterix` 中运行，Uni-Lab-OS edge 在 `unilab` 中以 `--physics isaac` 运行，query API 能看到 physics observation，并且能从 Isaac worker 取回 LabUtopia 场景 PNG。

**架构:** C5 不改 query transport。新增 `PhysicsLiveSource`，它按需读取 `RuntimeContext.physics.get_observation(target)` 并转成 `Pose` / `State`。edge 启动 query API 时把它插入现有 source 链路：`RosLiveSource` 优先，其次 `PhysicsLiveSource`，最后 LabUtopia 静态 source。验收通过一个 smoke script 检查 gRPC query 和 render PNG。

**技术栈:** Python 3.11、现有 `QueryEngine`、`RosLiveSource`、C2 `IsaacBridgeBackend`、C3 Isaac worker、ROS2/gRPC query service、4090 `unilab`、4090 `matterix`、pytest。

---

## 当前事实

- C1/C2 已有 physics contract 和 HTTP bridge。
- C3 应提供 worker：`http://127.0.0.1:8092/rpc`；4090 上 `8091` 可能已有其他 Isaac demo 占用。
- C4 应提供 edge CLI：
  - `--mode sim`
  - `--physics isaac`
  - `--physics_endpoint`
  - `--physics_scene`
- 现有 query startup 在 `unilabos/ros/main_slave_run.py:_start_query_services()`。
- 现有 live source 顺序是 `RosLiveSource` 在最前面。
- 4090 上 LabUtopia USD：
  - `/home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd`
- 4090 上 asset cards 目录在：
  - `/home/ubuntu/lab4090/projects/robo-unilabos-phase13/generated/`
  - 最终跑 C5 前要重新确认具体使用哪个 asset-card 子目录。
- 4090 的 `~/canonical/Uni-Lab-OS` 曾经是 dirty 状态。C5 验证继续使用 `/tmp` 干净副本或新 clone，不覆盖 canonical。

## 文件清单

- 新增 `unilabos/queries/physics_live_source.py`
  - 把 physics observation 映射成 query `Pose` / `State`。
- 修改 `unilabos/queries/__init__.py`
  - 导出 `PhysicsLiveSource`。
- 修改 `unilabos/ros/main_slave_run.py`
  - runtime 有 physics 时，把 `PhysicsLiveSource` 加入 query source。
- 新增 `tests/queries/test_physics_live_source.py`
  - 覆盖 pose/state 映射。
- 修改 `tests/integration/test_edge_query_wiring.py`
  - 覆盖 query source 构造顺序。
- 新增 `scripts/smoke_sim_isaac_edge.py`
  - 通过 gRPC 查询 state/pose，并通过 `IsaacBridgeBackend.render()` 写 PNG。
- 新增 `tests/integration/test_smoke_sim_isaac_edge_script.py`
  - 覆盖 smoke script 参数解析和 PNG 判断 helper。
- 新增 `docs/demo/phase2_isaac_e2e_4090.md`
  - 记录 4090 端到端命令和期望证据。

## Task 1: 新增 PhysicsLiveSource

**文件:**
- 新增 `unilabos/queries/physics_live_source.py`
- 修改 `unilabos/queries/__init__.py`
- 新增 `tests/queries/test_physics_live_source.py`

- [ ] **Step 1: 写失败测试**

新测试覆盖：

- observation 中有 `pose` dict 时能转为 `Pose`。
- observation 中有 UR 风格 `tcp_pose` 时能转为 `Pose`。
- observation 能转为 `State`。
- target 缺失时返回 `None`，让 query engine 继续 fallback 到静态 source。
- frame mismatch 时返回 `None`。

核心测试：

```python
class FakePhysics:
    name = "fake"

    def __init__(self):
        self.observations = {
            "arm": {
                "entity_id": "arm",
                "pose": {"xyz": [0.1, 0.2, 0.3], "quat_xyzw": [0, 0, 0, 1], "frame_id": "world"},
                "joint_positions": [1.0, 2.0],
                "joint_names": ["j1", "j2"],
            },
            "tool": {
                "entity_id": "tool",
                "tcp_pose": [0.4, 0.5, 0.6, 0.0, 0.0, 0.0],
            },
        }

    def get_observation(self, entity_id):
        if entity_id not in self.observations:
            raise KeyError(entity_id)
        return dict(self.observations[entity_id])
```

关键断言：

```python
def test_physics_live_source_maps_pose_dict_to_query_pose():
    source = PhysicsLiveSource(FakePhysics())
    pose = source.query_pose("arm")

    assert pose.xyz == [0.1, 0.2, 0.3]
    assert pose.quat_xyzw == [0, 0, 0, 1]
    assert pose.frame_id == "world"
    assert pose.source == "physics_live:fake"


def test_physics_live_source_maps_observation_to_state():
    source = PhysicsLiveSource(FakePhysics())
    state = source.query_state("arm")

    assert state.values["entity_id"] == "arm"
    assert state.values["joint_positions"] == [1.0, 2.0]
    assert state.values["joint_names"] == ["j1", "j2"]
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/queries/test_physics_live_source.py -q
```

预期：失败，因为 `PhysicsLiveSource` 不存在。

- [ ] **Step 3: 实现 `PhysicsLiveSource`**

`unilabos/queries/physics_live_source.py`：

```python
class PhysicsLiveSource:
    name = "physics_live"

    def __init__(self, physics_backend: Any):
        self.physics_backend = physics_backend

    @property
    def _source_name(self) -> str:
        return f"physics_live:{getattr(self.physics_backend, 'name', 'unknown')}"

    def _observation(self, target: str) -> Optional[dict[str, Any]]:
        try:
            return dict(self.physics_backend.get_observation(target))
        except Exception:
            return None
```

`query_pose()` 规则：

- 优先读 `obs["pose"]`：
  - `xyz`
  - `quat_xyzw`
  - `frame_id`
- 如果没有 `pose`，尝试读 `tcp_pose` 或 `tool_pose`：
  - 前 3 个数为 xyz
  - 后 3 个 rotvec 转 quaternion
- 如果 frame 不匹配，返回 `None`。

`query_state()` 规则：

```python
return State(name=target, values=obs, stamp=utc_timestamp(), source=self._source_name)
```

其他 query source 方法返回空：

- `query_affordance()` 返回 `[]`
- `query_action_schema()` 返回 `None`
- `query_safety_zones()` 返回 `[]`

在 `unilabos/queries/__init__.py` 导出：

```python
from unilabos.queries.physics_live_source import PhysicsLiveSource
```

并加入 `__all__`。

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/queries/test_physics_live_source.py tests/queries/test_ros_live_source.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/queries/physics_live_source.py unilabos/queries/__init__.py tests/queries/test_physics_live_source.py
git commit -m "feat(query): add physics live source"
```

## Task 2: 在 query startup 中接入 PhysicsLiveSource

**文件:**
- 修改 `unilabos/ros/main_slave_run.py`
- 修改 `tests/integration/test_edge_query_wiring.py`

- [ ] **Step 1: 写失败 helper 测试**

在 `tests/integration/test_edge_query_wiring.py` 新增：

```python
def test_build_query_static_sources_puts_physics_before_labutopia(monkeypatch):
    from unilabos.ros.main_slave_run import _build_query_static_sources
    from unilabos.sim.backends.fake_physics import FakePhysicsBackend
    from unilabos.sim.context import RuntimeContext

    class StaticSource:
        name = "static"

    import unilabos.ros.main_slave_run as mod
    monkeypatch.setattr(mod, "_build_labutopia_sources", lambda ctx: [StaticSource()])

    sources = _build_query_static_sources(RuntimeContext(mode="sim", physics=FakePhysicsBackend()))

    assert sources[0].name == "physics_live"
    assert sources[1].name == "static"
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/integration/test_edge_query_wiring.py::test_build_query_static_sources_puts_physics_before_labutopia -q
```

预期：失败，因为 `_build_query_static_sources()` 不存在。

- [ ] **Step 3: 抽出 source 构造 helper**

在 `unilabos/ros/main_slave_run.py` 添加：

```python
def _build_query_static_sources(ctx) -> list:
    sources = []
    physics = getattr(ctx, "physics", None)
    if physics is not None:
        from unilabos.queries.physics_live_source import PhysicsLiveSource

        sources.append(PhysicsLiveSource(physics))
    sources.extend(_build_labutopia_sources(ctx))
    return sources
```

- [ ] **Step 4: 修改 `_start_query_services()`**

把：

```python
static_sources = _build_labutopia_sources(ctx)
```

替换为：

```python
static_sources = _build_query_static_sources(ctx)
```

最终 query source 优先级：

1. `RosLiveSource`
2. `PhysicsLiveSource`
3. LabUtopia USD / asset card / task config source

- [ ] **Step 5: 验证通过**

```bash
python -m pytest tests/integration/test_edge_query_wiring.py tests/queries/test_physics_live_source.py -q
```

预期：通过。

- [ ] **Step 6: 提交点**

```bash
git add unilabos/ros/main_slave_run.py tests/integration/test_edge_query_wiring.py
git commit -m "feat(edge): include physics observations in query API"
```

## Task 3: 增加 edge + Isaac worker smoke script

**文件:**
- 新增 `scripts/smoke_sim_isaac_edge.py`
- 新增 `tests/integration/test_smoke_sim_isaac_edge_script.py`

- [ ] **Step 1: 写失败测试**

测试参数解析和 PNG 判断：

```python
def test_smoke_script_parse_args(tmp_path):
    args = smoke_sim_isaac_edge.parse_args(
        [
            "--grpc",
            "127.0.0.1:50051",
            "--physics-endpoint",
            "http://127.0.0.1:8091",
            "--state-target",
            "arm",
            "--pose-target",
            "tool",
            "--out",
            str(tmp_path / "frame.png"),
        ]
    )

    assert args.grpc == "127.0.0.1:50051"
    assert args.physics_endpoint == "http://127.0.0.1:8091"
    assert args.camera == "/World/Camera"
    assert args.width == 640
    assert args.height == 480


def test_validate_png_rejects_empty_payload():
    assert smoke_sim_isaac_edge.is_png_like(b"") is False
    assert smoke_sim_isaac_edge.is_png_like(b"\x89PNG\r\n\x1a\npayload") is True
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/integration/test_smoke_sim_isaac_edge_script.py -q
```

预期：失败，因为脚本不存在。

- [ ] **Step 3: 实现 smoke script**

`scripts/smoke_sim_isaac_edge.py` 参数：

- `--grpc`
- `--physics-endpoint`
- `--state-target`
- `--pose-target`
- `--camera`
- `--width`
- `--height`
- `--out`
- `--poll-timeout-s`
- `--poll-interval-s`

核心逻辑：

```python
client = RoboUniLabOSRemote(grpc_transport(args.grpc))
state, pose = _poll_query(client, args.state_target, args.pose_target, args.poll_timeout_s, args.poll_interval_s)

physics = IsaacBridgeBackend(args.physics_endpoint, timeout=30.0)
image = physics.render(args.camera, args.width, args.height)
if not is_png_like(image):
    raise RuntimeError(f"render payload is not PNG-like, got {len(image)} bytes")

Path(args.out).write_bytes(image)
```

`_poll_query()` 要在 timeout 内反复尝试：

- `client.query_state(state_target)`
- `client.query_pose(pose_target)`

直到都成功，或 timeout 抛 `RuntimeError`。

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/integration/test_smoke_sim_isaac_edge_script.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add scripts/smoke_sim_isaac_edge.py tests/integration/test_smoke_sim_isaac_edge_script.py
git commit -m "test(edge): add isaac e2e smoke script"
```

## Task 4: 写 4090 端到端 runbook

**文件:**
- 新增 `docs/demo/phase2_isaac_e2e_4090.md`

- [ ] **Step 1: 写 runbook**

内容必须包含以下命令。

同步代码：

```bash
ssh ubuntu@172.20.0.39 'rm -rf /tmp/Uni-Lab-OS-c5-e2e && mkdir -p /tmp/Uni-Lab-OS-c5-e2e'
rsync -a --exclude .git --exclude __pycache__ --exclude .pytest_cache ./ \
  ubuntu@172.20.0.39:/tmp/Uni-Lab-OS-c5-e2e/
```

启动 Isaac worker：

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

不要使用 `pkill -f "unilabos.sim.backends.isaac.worker"`；该模式可能匹配并杀掉自己的 SSH shell。需要停止时先用 `pgrep -af "[u]nilabos.sim.backends.isaac.worker.*8092"` 查精确 PID。

启动 edge：

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

本地 graph + `--app_bridges fastapi` + 非 websocket 可以离线启动，不需要 `UNILAB_AK/UNILAB_SK`。

运行 smoke：

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

停止进程：

```bash
ssh ubuntu@172.20.0.39 '
  kill $(cat /tmp/unilab_edge_c5_50052.pid) || true
  pgrep -af "[u]nilabos.sim.backends.isaac.worker.*8092"
'
```

runbook 必须写明期望证据：

- `ss -ltnp "( sport = :8092 )"` 能看到 worker。
- `ss -ltnp "( sport = :50052 )"` 能看到 query gRPC。
- smoke script 退出码为 0。
- smoke 输出的 `state.source` 和 `pose.source` 是 `physics_live:isaac`。
- `/tmp/labutopia-c5-e2e.png` 是 `PNG image data, 640 x 480, 8-bit/color RGBA`。
- 最终回归通过。

- [ ] **Step 2: 提交点**

```bash
git add docs/demo/phase2_isaac_e2e_4090.md
git commit -m "docs(sim): add isaac e2e 4090 runbook"
```

## Task 5: 执行 4090 端到端验证

**文件:** 不新增源文件，只执行验证。

- [ ] **Step 1: 跑目标测试**

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-c5-e2e \
   python -m pytest tests/queries/test_physics_live_source.py \
     tests/integration/test_smoke_sim_isaac_edge_script.py \
     tests/integration/test_edge_query_wiring.py -q'
```

预期：通过。

- [ ] **Step 2: 跑 worker + edge + smoke**

严格按 `docs/demo/phase2_isaac_e2e_4090.md` 执行。

注意：

- 本地 graph + fastapi-only 离线模式不需要 `UNILAB_AK` 和 `UNILAB_SK`。
- 如果切回 websocket 或远程资源模式，仍然需要凭证；不要把 ak/sk 写进文档、commit message 或 PR 描述。
- 如果首次启动 `unilab` 出现交互提示，先确认 `/tmp/Uni-Lab-OS-c5-e2e/unilabos_data` 是否存在；必要时使用交接文档中的 `echo Y | unilab ...` 方案。

预期：smoke 退出码为 0，并写出 `/tmp/labutopia-c5-e2e.png`。

- [ ] **Step 3: 跑最终回归**

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-c5-e2e \
   python -m pytest tests/sim tests/queries tests/integration -q'
```

预期：通过。

- [ ] **Step 4: 记录 PR 证据**

PR 描述里记录：

```text
4090 C5 validation:
- Worker: matterix, http://127.0.0.1:8092
- Edge: unilab, --mode sim --physics isaac
- Query: gRPC 127.0.0.1:50052
- Render output: /tmp/labutopia-c5-e2e.png, <ls -lh size>
- Regression: pytest tests/sim tests/queries tests/integration -q => <N> passed
```

- [ ] **Step 5: 提交点**

```bash
git add .
git commit -m "test(sim): validate isaac edge e2e render"
```

## C5 验收标准

- `PhysicsLiveSource` 能把 Isaac observation 映射成 query `Pose` 和 `State`。
- `_start_query_services()` 在 `RuntimeContext.physics` 存在时包含 physics live source。
- edge 能用 `--mode sim --physics isaac` 启动，gRPC query 保持在线。
- smoke script 能通过 gRPC 查到 physics-backed state/pose。
- smoke script 能从 Isaac worker 写出 PNG-like LabUtopia render。
- 4090 最终回归 `pytest tests/sim tests/queries tests/integration -q` 通过。

## C5 不做什么

- 不做前端 VirtualLab Toolbar。
- 不做连续帧 WebSocket streaming。
- 不验证化学任务成功、抓取成功或 contact correctness。
- 不声称完成校准机器人动力学。C5 的完成标准是“LabUtopia 场景可渲染 + query 可见 physics state”。
