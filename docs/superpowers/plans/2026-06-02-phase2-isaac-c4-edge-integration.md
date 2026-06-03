# Phase 2 Isaac C4 Edge 集成实施计划

> **给执行 agent 的要求:** 实施本计划时必须使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`，逐项执行并用 checkbox 记录状态。

**目标:** 把 C1/C2 的 physics backend 接入 Uni-Lab-OS edge 启动流程，使 `unilab --mode sim --physics fake|isaac` 能初始化 `RuntimeContext.physics`，并让代表性的 HAL / 虚拟设备命令走 physics backend。

**架构:** C4 只做 edge 侧集成，不做 Isaac worker 本体。CLI 增加 physics 参数，`app/backend.py` 通过 factory 构造 backend，`RuntimeContext` 成为进程内 physics 的唯一入口。HAL 侧先接 `URHAL`，因为它已有 sim backend 契约；虚拟设备侧先接 `VirtualMultiwayValve` 作为 opt-in 样例，不一次性重写所有虚拟设备。

**技术栈:** Python 3.11、argparse、`RuntimeContext`、C1 `FakePhysicsBackend`、C2 `IsaacBridgeBackend`、pytest。

---

## 当前事实

- `unilabos/app/main.py` 已有：
  - `--mode`
  - `--sim_rate`
  - `--sim_paused`
  - `--disable_sim_services`
  - query 相关参数
- `unilabos/app/backend.py:start_backend()` 当前会调用 `configure_runtime()`，并把 query 配置写入 `_runtime_services.context`。
- C1 已给 `RuntimeContext` 增加：
  - `physics_backend_name`
  - `physics_endpoint`
  - `physics_scene`
- `URHAL` 已支持显式传入 `mode="sim", sim_backend=...`。
- 当前 repo 没有 `VirtualArm` 类。虚拟设备主要是化学设备，因此 C4 不应该虚构机械臂虚拟设备。
- C5 才负责 query API 返回 physics observation；C4 只负责把 physics backend 初始化和基本命令路由接通。

## 文件清单

- 修改 `unilabos/app/main.py`
  - 新增 CLI 参数：`--physics`、`--physics_endpoint`、`--physics_scene`。
- 新增 `unilabos/sim/backends/factory.py`
  - 构造 `None`、`FakePhysicsBackend` 或 `IsaacBridgeBackend`。
- 修改 `unilabos/sim/runtime.py`
  - `configure_runtime()` 接收 physics 对象和配置字段。
- 修改 `unilabos/app/backend.py`
  - 在启动 backend 时构造并写入 `RuntimeContext.physics`。
- 新增 `unilabos/sim/device_physics.py`
  - 给虚拟设备用的小 helper，避免每个虚拟设备直接 import backend 类型。
- 修改 `unilabos/hal/adapters/ur_adapter.py`
  - sim 模式优先用显式 `sim_backend`，否则读 `get_runtime_context().physics`。
- 修改 `unilabos/devices/virtual/virtual_multiway_valve.py`
  - `set_position()` / `close()` 发送代表性 command 到 physics backend。
- 修改或新增测试：
  - `tests/sim/test_cli_runtime.py`
  - `tests/sim/backends/test_factory.py`
  - `tests/sim/test_runtime_configuration.py`
  - `tests/sim/test_backend_physics_configuration.py`
  - `tests/queries/test_ur_adapter.py`
  - `tests/sim/test_device_physics.py`
  - `tests/sim/test_virtual_device_clock.py`

## Task 1: 增加 CLI physics 参数

**文件:**
- 修改 `unilabos/app/main.py`
- 修改 `tests/sim/test_cli_runtime.py`

- [ ] **Step 1: 写失败测试**

在 `tests/sim/test_cli_runtime.py` 增加：

```python
def test_cli_physics_defaults_are_backward_compatible():
    args = build_argparser().parse_args([])

    assert args.physics == "none"
    assert args.physics_endpoint is None
    assert args.physics_scene is None


def test_cli_accepts_isaac_physics_options():
    args = build_argparser().parse_args(
        [
            "--mode",
            "sim",
            "--physics",
            "isaac",
            "--physics_endpoint",
            "http://127.0.0.1:8091",
            "--physics_scene",
            "/tmp/lab.usd",
        ]
    )

    assert args.physics == "isaac"
    assert args.physics_endpoint == "http://127.0.0.1:8091"
    assert args.physics_scene == "/tmp/lab.usd"
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/test_cli_runtime.py::test_cli_physics_defaults_are_backward_compatible \
  tests/sim/test_cli_runtime.py::test_cli_accepts_isaac_physics_options -q
```

预期：失败，因为参数还不存在。

- [ ] **Step 3: 实现 parser 参数**

在 `--disable_sim_services` 后添加：

```python
parser.add_argument(
    "--physics",
    choices=["none", "fake", "isaac"],
    default="none",
    help="Physics backend for sim mode: none, fake in-process backend, or Isaac HTTP bridge.",
)
parser.add_argument(
    "--physics_endpoint",
    type=str,
    default=None,
    help="Physics backend endpoint, required for --physics isaac.",
)
parser.add_argument(
    "--physics_scene",
    type=str,
    default=None,
    help="Scene path to load into the selected physics backend during startup.",
)
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/test_cli_runtime.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/app/main.py tests/sim/test_cli_runtime.py
git commit -m "feat(edge): add physics backend CLI options"
```

## Task 2: 增加 physics backend factory

**文件:**
- 新增 `unilabos/sim/backends/factory.py`
- 新增 `tests/sim/backends/test_factory.py`

- [ ] **Step 1: 写失败测试**

测试覆盖：

- `none` 返回 `None`
- `fake` 返回 `FakePhysicsBackend`
- `fake` 支持 `scene` 并调用 `load_scene`
- `isaac` 缺 endpoint 时抛错
- `isaac` 有 endpoint 时返回 `IsaacBridgeBackend`
- 未知 backend 抛错

核心测试：

```python
def test_factory_builds_fake_backend_and_loads_scene():
    backend = build_physics_backend("fake", endpoint=None, scene="/tmp/lab.usd")

    assert isinstance(backend, FakePhysicsBackend)
    assert backend.scene_path == "/tmp/lab.usd"


def test_factory_requires_endpoint_for_isaac_backend():
    with pytest.raises(ValueError, match="--physics_endpoint is required"):
        build_physics_backend("isaac", endpoint=None, scene=None)
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/backends/test_factory.py -q
```

预期：失败，因为 factory 不存在。

- [ ] **Step 3: 实现 factory**

`unilabos/sim/backends/factory.py`：

```python
def build_physics_backend(
    name: str | None,
    endpoint: str | None = None,
    scene: str | None = None,
) -> PhysicsBackend | None:
    backend_name = (name or "none").strip().lower()
    if backend_name == "none":
        return None
    if backend_name == "fake":
        from unilabos.sim.backends.fake_physics import FakePhysicsBackend
        backend: PhysicsBackend = FakePhysicsBackend()
    elif backend_name == "isaac":
        if not endpoint:
            raise ValueError("--physics_endpoint is required when --physics isaac")
        from unilabos.sim.backends.isaac_bridge import IsaacBridgeBackend
        backend = IsaacBridgeBackend(endpoint)
    else:
        raise ValueError(f"Unsupported physics backend: {name}")

    if scene:
        backend.load_scene(scene)
    return backend
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/backends/test_factory.py tests/sim/backends/test_fake_physics.py tests/sim/backends/test_isaac_bridge.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/sim/backends/factory.py tests/sim/backends/test_factory.py
git commit -m "feat(sim): add physics backend factory"
```

## Task 3: 让 `configure_runtime()` 携带 physics

**文件:**
- 修改 `unilabos/sim/runtime.py`
- 修改 `tests/sim/test_runtime_configuration.py`

- [ ] **Step 1: 写失败测试**

新增：

```python
class DummyPhysics:
    name = "dummy"


def test_configure_runtime_stores_physics_backend_and_config():
    physics = DummyPhysics()

    services = configure_runtime(
        mode="sim",
        physics=physics,
        physics_backend_name="fake",
        physics_endpoint="http://127.0.0.1:8091",
        physics_scene="/tmp/lab.usd",
    )

    assert services.context.physics is physics
    assert get_runtime_context().physics is physics
    assert services.context.physics_backend_name == "fake"
    assert services.context.physics_endpoint == "http://127.0.0.1:8091"
    assert services.context.physics_scene == "/tmp/lab.usd"
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/test_runtime_configuration.py::test_configure_runtime_stores_physics_backend_and_config -q
```

预期：失败，因为 `configure_runtime()` 还不接收这些参数。

- [ ] **Step 3: 修改 `configure_runtime()`**

新增参数：

```python
physics=None
physics_backend_name: str = "none"
physics_endpoint: str | None = None
physics_scene: str | None = None
```

构造 `RuntimeContext` 时传入：

```python
context = RuntimeContext(
    mode=mode,
    clock=clock,
    sim_paused=sim_paused,
    physics=physics,
    physics_backend_name=physics_backend_name,
    physics_endpoint=physics_endpoint,
    physics_scene=physics_scene,
)
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/test_runtime_configuration.py tests/sim/test_context_and_clock.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/sim/runtime.py tests/sim/test_runtime_configuration.py
git commit -m "feat(sim): pass physics backend through runtime configuration"
```

## Task 4: 在 `app/backend.py` 初始化 physics

**文件:**
- 修改 `unilabos/app/backend.py`
- 新增 `tests/sim/test_backend_physics_configuration.py`

- [ ] **Step 1: 写失败测试**

新增 helper 测试，不启动 ROS thread：

```python
def test_initialize_runtime_for_backend_builds_fake_physics():
    services = backend_mod._initialize_runtime_for_backend(
        backend="ros",
        kwargs={
            "mode": "sim",
            "sim_rate": 10.0,
            "sim_paused": True,
            "physics": "fake",
            "physics_endpoint": None,
            "physics_scene": "/tmp/lab.usd",
            "disable_sim_services": False,
            "disable_query_api": False,
            "query_grpc_port": 50051,
        },
    )

    assert isinstance(services.context.physics, FakePhysicsBackend)
    assert services.context.physics_backend_name == "fake"
    assert services.context.physics_scene == "/tmp/lab.usd"
    assert services.context.sim_services_enabled is True
    assert services.context.query_api_enabled is True
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/test_backend_physics_configuration.py -q
```

预期：失败，因为 `_initialize_runtime_for_backend()` 不存在。

- [ ] **Step 3: 抽出 runtime 初始化 helper**

在 `unilabos/app/backend.py` 添加：

```python
from unilabos.sim.backends.factory import build_physics_backend
```

新增：

```python
def _initialize_runtime_for_backend(backend: str, kwargs: dict) -> RuntimeServices:
    mode = kwargs.get("mode", "real")
    sim_rate = kwargs.get("sim_rate", 1.0)
    sim_paused = kwargs.get("sim_paused", False)
    physics_name = kwargs.get("physics", "none")
    physics_endpoint = kwargs.get("physics_endpoint")
    physics_scene = kwargs.get("physics_scene")
    physics = build_physics_backend(physics_name, endpoint=physics_endpoint, scene=physics_scene)
    start_sim_services = backend == "ros" and not kwargs.get("disable_sim_services", False)
    services = configure_runtime(
        mode=mode,
        sim_rate=sim_rate,
        sim_paused=sim_paused,
        start_ros_services=False,
        physics=physics,
        physics_backend_name=physics_name,
        physics_endpoint=physics_endpoint,
        physics_scene=physics_scene,
    )
    services.context.sim_services_enabled = start_sim_services and mode in ("sim", "twin")
    services.context.query_api_enabled = backend == "ros" and not kwargs.get("disable_query_api", False)
    services.context.query_grpc_port = int(kwargs.get("query_grpc_port", 50051))
    services.context.query_labutopia_assets = kwargs.get("query_labutopia_assets")
    services.context.query_labutopia_config = kwargs.get("query_labutopia_config")
    services.context.query_labutopia_usd = kwargs.get("query_labutopia_usd")
    return services
```

在 `start_backend()` 中用它替换原有 runtime 初始化块：

```python
_runtime_services = _initialize_runtime_for_backend(backend, kwargs)
```

日志补充：

```python
f"physics={_runtime_services.context.physics_backend_name}, "
f"physics_endpoint={_runtime_services.context.physics_endpoint}, "
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/test_backend_physics_configuration.py tests/sim/test_runtime_configuration.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/app/backend.py tests/sim/test_backend_physics_configuration.py
git commit -m "feat(edge): initialize physics backend at startup"
```

## Task 5: 让 URHAL sim 模式默认使用 RuntimeContext.physics

**文件:**
- 修改 `unilabos/hal/adapters/ur_adapter.py`
- 修改 `tests/queries/test_ur_adapter.py`

- [ ] **Step 1: 写失败测试**

新增测试：

```python
def test_sim_mode_defaults_to_runtime_physics_backend():
    _reset_for_test()
    backend = FakeSimBackend()
    init_runtime_context(RuntimeContext(mode="sim", physics=backend, physics_backend_name="fake"))
    try:
        hal = URHAL(host="sim", robot_id="ur5_runtime", mode="sim")
        hal.move_j([0, 1, 2, 3, 4, 5], speed=0.4)

        assert backend.commands[-1] == (
            "ur5_runtime",
            {"type": "move_j", "joint_positions": [0, 1, 2, 3, 4, 5], "speed": 0.4},
        )
    finally:
        _reset_for_test()
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/queries/test_ur_adapter.py::URAdapterTest::test_sim_mode_defaults_to_runtime_physics_backend -q
```

预期：失败，因为当前 `URHAL` 需要显式 `sim_backend`。

- [ ] **Step 3: 实现 runtime 默认 backend**

在 `URHAL` 增加：

```python
def _active_sim_backend(self):
    if self.sim_backend is not None:
        return self.sim_backend
    from unilabos.sim.context import get_runtime_context

    backend = get_runtime_context().physics
    if backend is None:
        raise RuntimeError("URHAL sim mode requires sim_backend or RuntimeContext.physics")
    return backend
```

替换 `_sim_observation()` / `_sim_command()`：

```python
def _sim_observation(self) -> dict[str, Any]:
    return dict(self._active_sim_backend().get_observation(self.robot_id))


def _sim_command(self, command: dict[str, Any]) -> None:
    self._active_sim_backend().set_command(self.robot_id, command)
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/queries/test_ur_adapter.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/hal/adapters/ur_adapter.py tests/queries/test_ur_adapter.py
git commit -m "feat(hal): default UR sim mode to runtime physics"
```

## Task 6: 增加虚拟设备 physics dispatch helper

**文件:**
- 新增 `unilabos/sim/device_physics.py`
- 新增 `tests/sim/test_device_physics.py`

- [ ] **Step 1: 写失败测试**

测试内容：

```python
def test_dispatch_device_command_noops_without_physics():
    assert dispatch_device_command("valve", {"type": "set_position"}) is False


def test_dispatch_device_command_sends_to_runtime_physics():
    physics = RecordingPhysics()
    init_runtime_context(RuntimeContext(mode="sim", physics=physics, physics_backend_name="fake"))

    assert dispatch_device_command("valve", {"type": "set_position", "position": 3}) is True
    assert physics.commands == [("valve", {"type": "set_position", "position": 3})]
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/test_device_physics.py -q
```

预期：失败，因为 helper 不存在。

- [ ] **Step 3: 实现 helper**

`unilabos/sim/device_physics.py`：

```python
def dispatch_device_command(entity_id: str, command: dict[str, Any]) -> bool:
    backend = get_runtime_context().physics
    if backend is None:
        return False
    backend.set_command(str(entity_id), dict(command))
    return True
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/test_device_physics.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/sim/device_physics.py tests/sim/test_device_physics.py
git commit -m "feat(sim): add virtual device physics dispatch helper"
```

## Task 7: 将 `VirtualMultiwayValve` 作为代表性虚拟设备接入 physics

**文件:**
- 修改 `unilabos/devices/virtual/virtual_multiway_valve.py`
- 修改 `tests/sim/test_virtual_device_clock.py`

- [ ] **Step 1: 写失败测试**

新增：

```python
def test_virtual_multiway_valve_dispatches_position_to_physics():
    physics = FakePhysicsBackend()
    init_runtime_context(RuntimeContext(mode="sim", clock=SimClock("sim", scale=100.0), physics=physics))
    valve = VirtualMultiwayValve(id="valve_a", positions=8)

    valve.set_position(3)

    assert physics.commands["valve_a"] == {
        "type": "set_position",
        "position": 3,
        "device": "virtual_multiway_valve",
    }
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/test_virtual_device_clock.py::test_virtual_multiway_valve_dispatches_position_to_physics -q
```

预期：失败，因为 valve 还不会 dispatch physics command。

- [ ] **Step 3: 实现 opt-in dispatch**

在 `VirtualMultiwayValve.__init__` 存稳定 id：

```python
self.device_id = kwargs.get("device_id") or kwargs.get("id") or self.port
```

导入 helper：

```python
from unilabos.sim.device_physics import dispatch_device_command
```

在 `set_position()` 设置 `_target_position` 后添加：

```python
dispatch_device_command(
    self.device_id,
    {"type": "set_position", "position": pos, "device": "virtual_multiway_valve"},
)
```

在 `close()` 中设置 closing 状态后添加：

```python
dispatch_device_command(
    self.device_id,
    {"type": "close", "position": self._current_position, "device": "virtual_multiway_valve"},
)
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/test_virtual_device_clock.py tests/sim/test_device_physics.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/devices/virtual/virtual_multiway_valve.py tests/sim/test_virtual_device_clock.py
git commit -m "feat(devices): route virtual valve commands to physics"
```

## Task 8: 在 4090 上跑 C4 回归

**文件:** 不新增源文件，只执行验证。

- [ ] **Step 1: 同步到 4090 独立目录**

```bash
ssh ubuntu@172.20.0.39 'rm -rf /tmp/Uni-Lab-OS-c4-edge && mkdir -p /tmp/Uni-Lab-OS-c4-edge'
rsync -a --exclude .git --exclude __pycache__ --exclude .pytest_cache ./ \
  ubuntu@172.20.0.39:/tmp/Uni-Lab-OS-c4-edge/
```

- [ ] **Step 2: 跑 C4 目标测试**

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-c4-edge \
   python -m pytest tests/sim/test_cli_runtime.py \
     tests/sim/backends/test_factory.py \
     tests/sim/test_runtime_configuration.py \
     tests/sim/test_backend_physics_configuration.py \
     tests/sim/test_device_physics.py \
     tests/sim/test_virtual_device_clock.py \
     tests/queries/test_ur_adapter.py -q'
```

预期：通过。

- [ ] **Step 3: 跑 Phase 1/3 回归切片**

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-c4-edge \
   python -m pytest tests/sim tests/queries tests/integration -q'
```

预期：通过。

- [ ] **Step 4: 提交点**

```bash
git add .
git commit -m "test(edge): verify physics integration on 4090"
```

## C4 验收标准

- `build_argparser()` 接受：
  - `--physics fake`
  - `--physics isaac`
  - `--physics_endpoint`
  - `--physics_scene`
- `start_backend()` 通过 factory 初始化 `RuntimeContext.physics`。
- `--physics fake --physics_scene /tmp/lab.usd` 能在 startup 测试里加载 fake scene。
- `URHAL(mode="sim")` 不显式传 `sim_backend` 时能使用 `RuntimeContext.physics`。
- `VirtualMultiwayValve` 在 physics 启用时能发送代表性 command，同时保留原有 sim-clock 行为。
- 4090 上 `pytest tests/sim tests/queries tests/integration -q` 保持通过。

## C4 不做什么

- 不启动真实 Isaac worker；C3 已覆盖 worker 独立运行。
- 不让 query API 返回 physics observation；那是 C5。
- 不把所有虚拟设备一次性改成 physics-aware。
- 不启用 Feetech 真实动作；`FeetechRoboArmHAL` 仍保持 read-only。
