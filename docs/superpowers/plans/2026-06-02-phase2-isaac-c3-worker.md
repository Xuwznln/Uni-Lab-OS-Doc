# Phase 2 Isaac C3 Worker 实施计划

> **给执行 agent 的要求:** 实施本计划时必须使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`，逐项执行并用 checkbox 记录状态。

**目标:** 新增一个可以在 4090 `matterix` 环境中独立运行的 Isaac worker，对外提供 C1/C2 已定义的 `/rpc` 物理后端协议。

**架构:** C3 只做 Isaac 端 worker，不接 edge。worker 分成两层：一层是无 Isaac 依赖的 HTTP/RPC 壳，能在普通 `unilab` 环境单测；另一层是 lazy import 的 Isaac controller，只在 `matterix` 环境启动 `SimulationApp`、加载 LabUtopia USD、step、读取 observation、render PNG。

**技术栈:** Python 3.11、`http.server`、`json`、C1/C2 的 `unilabos.sim.backends.isaac.protocol`、Isaac Sim、pytest。

---

## 当前事实

- C1/C2 已完成：
  - `PhysicsBackend.load_scene()` / `render()`
  - `FakePhysicsBackend`
  - `IsaacBridgeBackend`
  - `/rpc` JSON request 编码
- 4090 上环境分工：
  - `unilab`: 跑 edge 和普通 pytest
  - `matterix`: 跑 Isaac / LabUtopia headless
- 4090 上已有参考 demo：
  - `/home/ubuntu/isaac_roboarm_bridge/isaac_virtual_leader_server.py`
  - 它使用 `ThreadingHTTPServer`、延迟 Isaac import、CLI 参数和 HTTP handler。
- LabUtopia USD 已确认存在：
  - `/home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd`
  - `/home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_003/lab_003.usd`
  - `/home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/hard_task/lab_004.usd`
- 本地 repo 里有 `robot_assets/roboarm_chem_04`，但它是早期 kinematic query asset，不代表高保真动力学模型。C3 不能声称完成校准机器人动力学。

## 文件清单

- 修改 `unilabos/sim/backends/isaac/protocol.py`
  - 增加 worker 端 helper：`decode_request()`、`encode_response()`、`encode_error()`。
- 新增 `unilabos/sim/backends/isaac/worker_http.py`
  - 无 Isaac 依赖的 `/health` 和 `/rpc` HTTP server。
- 新增 `unilabos/sim/backends/isaac/worker.py`
  - Isaac worker CLI 入口。
  - `IsaacWorkerState` 负责 `/rpc` op 分发。
  - `IsaacController` 负责 Isaac API，必须 lazy import。
- 新增 `scripts/smoke_isaac_worker.py`
  - 连接一个已经运行的 worker，load scene、step、query observation、render PNG。
- 新增测试：
  - `tests/sim/backends/test_isaac_worker_protocol.py`
  - `tests/sim/backends/test_isaac_worker_http.py`
  - `tests/sim/backends/test_isaac_worker_cli.py`
  - `tests/sim/backends/test_isaac_worker_smoke_script.py`

## Task 1: 增加 worker 端 JSON helper

**文件:**
- 修改 `unilabos/sim/backends/isaac/protocol.py`
- 新增 `tests/sim/backends/test_isaac_worker_protocol.py`

- [ ] **Step 1: 写失败测试**

测试内容要覆盖：

```python
def test_decode_request_reads_operation_and_args():
    op, args = decode_request(b'{"op":"step","args":{"dt":0.05}}')
    assert op == "step"
    assert args == {"dt": 0.05}


def test_decode_request_rejects_missing_operation():
    with pytest.raises(ValueError, match="RPC request missing op"):
        decode_request(b'{"args":{}}')


def test_encode_response_matches_client_decode_shape():
    body = encode_response({"ok_value": 1})
    assert json.loads(body.decode("utf-8")) == {"ok": True, "result": {"ok_value": 1}}


def test_encode_error_matches_client_decode_shape():
    body = encode_error("bad scene")
    assert json.loads(body.decode("utf-8")) == {"ok": False, "error": "bad scene"}
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/backends/test_isaac_worker_protocol.py -q
```

预期：失败，因为 helper 还不存在。

- [ ] **Step 3: 实现 helper**

在 `protocol.py` 增加：

```python
def decode_request(data: bytes) -> tuple[str, dict[str, Any]]:
    payload = json.loads(data.decode("utf-8"))
    op = payload.get("op")
    if not op:
        raise ValueError("RPC request missing op")
    args = payload.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("RPC request args must be an object")
    return str(op), dict(args)


def encode_response(result: Any = None) -> bytes:
    return json.dumps({"ok": True, "result": result}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode_error(error: str) -> bytes:
    return json.dumps({"ok": False, "error": str(error)}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/backends/test_isaac_worker_protocol.py tests/sim/backends/test_isaac_protocol.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/sim/backends/isaac/protocol.py tests/sim/backends/test_isaac_worker_protocol.py
git commit -m "feat(sim): add isaac worker protocol helpers"
```

## Task 2: 增加无 Isaac 依赖的 HTTP server 壳

**文件:**
- 新增 `unilabos/sim/backends/isaac/worker_http.py`
- 新增 `tests/sim/backends/test_isaac_worker_http.py`

- [ ] **Step 1: 写失败测试**

测试要启动本地 `ThreadingHTTPServer`，用 fake state 覆盖：

- `GET /health` 返回 JSON。
- `POST /rpc` 能 dispatch 到 `state.dispatch(op, args)`。
- dispatch 抛异常时返回 HTTP 500 和 `{"ok": false, "error": "..."}`。

核心测试形态：

```python
class FakeWorkerState:
    def __init__(self):
        self.calls = []

    def health(self):
        return {"ok": True, "backend": "fake_isaac_worker"}

    def dispatch(self, op, args):
        self.calls.append((op, args))
        if op == "explode":
            raise RuntimeError("boom")
        return {"op": op, "args": args}
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/backends/test_isaac_worker_http.py -q
```

预期：失败，因为 `worker_http.py` 不存在。

- [ ] **Step 3: 实现 HTTP 壳**

`worker_http.py` 必须只依赖 stdlib 和 `protocol.py`：

```python
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(worker_state: Any):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._write_json(worker_state.health(), status=200)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path != "/rpc":
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                op, args = decode_request(self.rfile.read(length))
                self._write_body(encode_response(worker_state.dispatch(op, args)), status=200)
            except Exception as exc:
                self._write_body(encode_error(str(exc)), status=500)
```

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/backends/test_isaac_worker_http.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/sim/backends/isaac/worker_http.py tests/sim/backends/test_isaac_worker_http.py
git commit -m "feat(sim): add isaac worker HTTP shell"
```

## Task 3: 增加 Isaac worker CLI 和 lazy controller

**文件:**
- 新增 `unilabos/sim/backends/isaac/worker.py`
- 新增 `tests/sim/backends/test_isaac_worker_cli.py`

- [ ] **Step 1: 写失败测试**

测试目标：

- import `worker.py` 时不加载 `isaacsim` / `omni.usd`。
- `parse_args([])` 默认值正确。
- `IsaacWorkerState.dispatch()` 能把 op 分发给 controller。

关键断言：

```python
def test_worker_import_does_not_import_isaac_modules():
    assert "isaacsim" not in sys.modules
    assert "omni.usd" not in sys.modules


def test_worker_parse_args_defaults():
    args = worker.parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8091
    assert args.headless is True
    assert args.scene is None
    assert args.camera == "/World/Camera"
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/backends/test_isaac_worker_cli.py -q
```

预期：失败，因为 `worker.py` 不存在。

- [ ] **Step 3: 实现 `worker.py`**

CLI 参数：

```python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uni-Lab-OS Isaac physics worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--scene", default=None)
    parser.add_argument("--robot-prim", default=None)
    parser.add_argument("--camera", default="/World/Camera")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--warmup-steps", type=int, default=2)
    return parser.parse_args(argv)
```

`IsaacWorkerState.dispatch()` 必须支持这些 op：

- `reset`
- `step`
- `load_scene`
- `get_observation`
- `set_command`
- `attach_rigid_body`
- `get_joint_states`
- `apply_wrench`
- `render`

`render` 返回给 HTTP client 时必须 base64 包装：

```python
if op == "render":
    image = self.controller.render(str(args["camera"]), int(args["width"]), int(args["height"]))
    return {"encoding": "base64", "data": base64.b64encode(image).decode("ascii")}
```

`IsaacController` 要求：

- 只在 `__init__()` 内部 import Isaac：

```python
from isaacsim import SimulationApp
```

- `load_scene(scene_path)` 使用 `omni.usd.get_context().open_stage(scene_path)`。
- `step(dt)` 至少调用 `self.app.update()`。
- `set_command(entity_id, command)` 先记录命令，不要求 C3 完成真实 articulation 控制。
- `get_observation(entity_id)` 返回至少：

```python
{
    "entity_id": entity_id,
    "scene_path": self.scene_path,
    "last_command": self.commands.get(entity_id),
    "source": "isaac_worker",
}
```

如果 prim 存在，再加入 pose。

- `render(camera, width, height)` 优先用 Isaac capture；如果 Isaac capture API 失败，允许返回 minimal fallback PNG，但必须在 observation 或日志里标出 `render_fallback="minimal_png"`。C5 会把“真实画面像素”作为更严格验收。

- [ ] **Step 4: 验证普通环境可导入**

```bash
python -m pytest tests/sim/backends/test_isaac_worker_cli.py tests/sim/backends/test_isaac_worker_http.py -q
```

预期：通过，并且不需要 Isaac。

- [ ] **Step 5: 提交点**

```bash
git add unilabos/sim/backends/isaac/worker.py tests/sim/backends/test_isaac_worker_cli.py
git commit -m "feat(sim): add isaac worker CLI"
```

## Task 4: 增加 worker smoke client

**文件:**
- 新增 `scripts/smoke_isaac_worker.py`
- 新增 `tests/sim/backends/test_isaac_worker_smoke_script.py`

- [ ] **Step 1: 写失败测试**

测试 `parse_args()`：

```python
def test_smoke_script_parse_args_defaults(tmp_path):
    args = smoke_isaac_worker.parse_args([
        "--endpoint", "http://127.0.0.1:8091",
        "--out", str(tmp_path / "frame.png"),
    ])

    assert args.endpoint == "http://127.0.0.1:8091"
    assert args.camera == "/World/Camera"
    assert args.width == 640
    assert args.height == 480
```

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/sim/backends/test_isaac_worker_smoke_script.py -q
```

预期：失败，因为脚本不存在。

- [ ] **Step 3: 实现脚本**

脚本使用 `IsaacBridgeBackend`：

```python
backend = IsaacBridgeBackend(args.endpoint, timeout=30.0)
if args.scene:
    backend.load_scene(args.scene)
backend.step(0.016)
observation = backend.get_observation(args.entity)
image = backend.render(args.camera, args.width, args.height)
Path(args.out).write_bytes(image)
```

参数：

- `--endpoint`
- `--scene`
- `--entity`
- `--camera`
- `--width`
- `--height`
- `--out`

- [ ] **Step 4: 验证通过**

```bash
python -m pytest tests/sim/backends/test_isaac_worker_smoke_script.py -q
```

预期：通过。

- [ ] **Step 5: 提交点**

```bash
git add scripts/smoke_isaac_worker.py tests/sim/backends/test_isaac_worker_smoke_script.py
git commit -m "test(sim): add isaac worker smoke client"
```

## Task 5: 在 4090 的 `matterix` 中验证 C3

**文件:** 不新增源文件，只执行验证。

- [ ] **Step 1: 同步到 4090 独立目录**

不要覆盖 4090 上 dirty 的 `~/canonical/Uni-Lab-OS`。

```bash
ssh ubuntu@172.20.0.39 'rm -rf /tmp/Uni-Lab-OS-c3-worker && mkdir -p /tmp/Uni-Lab-OS-c3-worker'
rsync -a --exclude .git --exclude __pycache__ --exclude .pytest_cache ./ \
  ubuntu@172.20.0.39:/tmp/Uni-Lab-OS-c3-worker/
```

- [ ] **Step 2: 在 `unilab` 跑 C3 单测**

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-c3-worker \
   python -m pytest tests/sim/backends/test_isaac_worker_protocol.py \
     tests/sim/backends/test_isaac_worker_http.py \
     tests/sim/backends/test_isaac_worker_cli.py \
     tests/sim/backends/test_isaac_worker_smoke_script.py -q'
```

预期：通过。

- [ ] **Step 3: 在 `matterix` 启动 worker**

```bash
ssh ubuntu@172.20.0.39 '
  pkill -f "unilabos.sim.backends.isaac.worker" || true
  cd /tmp/Uni-Lab-OS-c3-worker
  nohup /home/ubuntu/miniforge3/bin/conda run -n matterix env PYTHONPATH=. \
    python -m unilabos.sim.backends.isaac.worker \
      --host 127.0.0.1 \
      --port 8091 \
      --headless \
      --scene /home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd \
      --camera /World/Camera \
    > /tmp/isaac_worker_c3.log 2>&1 &
  echo $! > /tmp/isaac_worker_c3.pid
'
```

Isaac 启动慢，等 60 秒后检查：

```bash
ssh ubuntu@172.20.0.39 'sleep 60; tail -n 80 /tmp/isaac_worker_c3.log; ss -ltn | grep 8091'
```

预期：worker 进程仍在，`8091` 正在监听。

- [ ] **Step 4: 运行 smoke client**

```bash
ssh ubuntu@172.20.0.39 '
  cd /tmp/Uni-Lab-OS-c3-worker
  /home/ubuntu/miniforge3/bin/conda run -n unilab env PYTHONPATH=. \
    python scripts/smoke_isaac_worker.py \
      --endpoint http://127.0.0.1:8091 \
      --scene /home/ubuntu/labsim/LabUtopia_repro/assets/chemistry_lab/lab_001/lab_001.usd \
      --entity /World \
      --camera /World/Camera \
      --out /tmp/labutopia-c3-worker.png
  file /tmp/labutopia-c3-worker.png
  ls -lh /tmp/labutopia-c3-worker.png
'
```

预期：

- smoke script 退出码为 0。
- `/tmp/labutopia-c3-worker.png` 存在且非空。
- `/tmp/isaac_worker_c3.log` 没有 Python traceback。

- [ ] **Step 5: 停 worker**

```bash
ssh ubuntu@172.20.0.39 'kill $(cat /tmp/isaac_worker_c3.pid) || true'
```

- [ ] **Step 6: 跑回归切片**

```bash
ssh ubuntu@172.20.0.39 \
  '/home/ubuntu/miniforge3/bin/conda run -n unilab --cwd /tmp/Uni-Lab-OS-c3-worker \
   python -m pytest tests/sim tests/queries tests/integration -q'
```

预期：通过。

- [ ] **Step 7: 提交点**

```bash
git add .
git commit -m "test(sim): verify isaac worker on 4090"
```

## C3 验收标准

- worker HTTP 壳可以在无 Isaac 环境单测。
- import `unilabos.sim.backends.isaac.worker` 不会加载 Isaac 模块。
- 4090 `matterix` 能在 `127.0.0.1:8091` 启动 worker。
- worker 能 load LabUtopia `lab_001.usd`。
- smoke client 能 step、get observation、写 PNG。
- 4090 回归 `pytest tests/sim tests/queries tests/integration -q` 保持通过。

## C3 不做什么

- 不接 `unilab --mode sim --physics isaac`，那是 C4。
- 不要求 query API 返回 physics state，那是 C5。
- 不做机器人校准控制、抓取、contact、attachment。
- 不保证渲染像素语义完全正确；C3 只证明 worker render 管道能返回 image bytes。
