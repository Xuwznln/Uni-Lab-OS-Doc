"""README 示例设备包（六个 demo 仓库）的引用清单、源码解析与运行时驻留工具。

unilabos 侧在这里固定引用六个 demo 仓库（URL + 已验证提交；同一份清单也收录在
awesome-lab-devices 索引里供 OpenLab 一键安装），e2e 用例按以下
顺序取得仓库源码：

1. ``UNILABOS_README_EXAMPLES_ROOT/<仓库名>``（外部预先 checkout）；
2. 与本仓库同级的开发目录 ``../<仓库名>``（本地联调，允许领先于 pinned 提交）；
3. 按 pinned 提交浅克隆到临时缓存目录（需要 git 与网络；CI 走这条）。

pinned 提交与各 demo 仓库 CI 里固定的 Uni-Lab-OS 提交互相锁定：core 有破坏性
改动时先改 demo 并推送，再在此处 bump。

运行时后端由 ``UNILABOS_E2E_BACKEND``（``hostlink`` 缺省 / ``ros2``）选择。ros2 模式
下 HostLink 仍然开启（承载物料权威与 host 服务，与 Site demo 的 ros2 形态一致），
设备动作与 Topic 走 ROS 2；每个用例用独立的 ``ROS_DOMAIN_ID`` 隔离。

进程拓扑由 ``UNILABOS_E2E_TOPOLOGY``（``single`` 缺省 / ``split``）选择：``single`` 加
``--no_safe_restart`` 让调度权威与 Host 同进程；``split`` 走 ``unilab`` 默认的
「监督进程 → 调度权威 → Host 子进程」，用例仍只连权威端口。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import unilabos

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = Path(unilabos.__file__).resolve().parent / "config" / "example_config.py"
TERMINAL_TASK_STATUSES = {"succeeded", "failed"}

E2E_BACKEND = os.environ.get("UNILABOS_E2E_BACKEND", "hostlink").strip().lower()
if E2E_BACKEND not in {"hostlink", "ros2"}:
    raise RuntimeError(f"UNILABOS_E2E_BACKEND 必须是 hostlink 或 ros2，当前: {E2E_BACKEND!r}")
#: 进程拓扑：``single``（缺省）加 ``--no_safe_restart``，调度权威与 Host 同进程，用例直接
#: 管这个进程的生死；``split`` 走默认拓扑（监督进程 → 调度权威 → Host 子进程），
#: 验证浏览器只连权威端口时六个 demo 的图 / 工作流 / 决策链仍完整。
E2E_TOPOLOGY = os.environ.get("UNILABOS_E2E_TOPOLOGY", "single").strip().lower()
if E2E_TOPOLOGY not in {"single", "split"}:
    raise RuntimeError(f"UNILABOS_E2E_TOPOLOGY 必须是 single 或 split，当前: {E2E_TOPOLOGY!r}")
#: 只属于测试夹具的控制变量。``UNILABOS_`` 前缀在运行时进程里是配置覆盖协议
#: （``UNILABOS_<Config类>_<字段>``），这些变量不能泄漏进被测进程。
HARNESS_ENV_VARS = (
    "UNILABOS_E2E_BACKEND",
    "UNILABOS_E2E_TOPOLOGY",
    "UNILABOS_README_EXAMPLES_ROOT",
)
#: ROS 2 节点启动与发现比 HostLink 慢，闭环与工作流等待窗口相应放宽。
_ROS2_TIMEOUT_SCALE = 1.5


@dataclass(frozen=True)
class WorkflowExpectation:
    """一个 ``@workflow`` 模板经管理 API 运行后的期望终态。"""

    name: str
    #: ``/workflow-tasks/{uuid}/node-runs`` 的条数 = 工作流节点数（每节点一个节点运行）。
    node_count: int
    task_status: str = "succeeded"
    #: 节点运行终态（当前 attempt 的投影）；缺省表示全部 succeeded。
    node_statuses: Optional[tuple[str, ...]] = None
    #: 每个节点运行的 attempt 数；缺省全部为 1。retry 过的节点 > 1，历史留在 ``attempts``。
    attempt_counts: Optional[tuple[int, ...]] = None
    #: 期望失败 attempt 先进入错误决策链；给出网页式决策 payload
    #: （``{"action": "abort"}``、``{"action": "retry"}`` 或携带 ``result`` 的
    #: ``operator_intervention``）。
    error_decision: Optional[dict[str, Any]] = None
    #: 并发组：相邻且同名 group 的工作流先全部创建任务再逐个等待终态，
    #: 复现"网页上连点几个运行"的调度竞争（动作锁 / 物料锁 / always_free）。
    group: Optional[str] = None
    #: 期望该任务在组内竞争时被调度器排队：``/scheduler/resources`` 里出现
    #: 该 task 的 ``waiting`` 申请且 ``blockers`` 非空。
    expect_waiting: bool = False
    #: 任务级错误：``error_info[0].code`` 与 message 子串（如库存不足时的
    #: ``plan_not_executable`` / ``short by``）。
    task_error_code: Optional[str] = None
    task_error_contains: Optional[str] = None

    def expected_node_statuses(self) -> tuple[str, ...]:
        return self.node_statuses or tuple("succeeded" for _ in range(self.node_count))

    def expected_attempt_counts(self) -> tuple[int, ...]:
        return self.attempt_counts or tuple(1 for _ in range(self.node_count))


@dataclass(frozen=True)
class DemoSpec:
    repo: str
    url: str
    ref: str
    package: str
    host_graph: str
    workflows: tuple[WorkflowExpectation, ...]
    slave_graph: Optional[str] = None
    #: 环境变量名 -> 设备闭环写出的 proof 文件名（相对运行临时目录）。
    proof_env: dict[str, str] = field(default_factory=dict)
    extra_env: dict[str, str] = field(default_factory=dict)
    #: ros2 后端下覆盖 extra_env 的项（demo 自带 smoke 在 ros2 下放宽的启动延时）。
    extra_env_ros2: dict[str, str] = field(default_factory=dict)
    timeout: float = 60.0

    @property
    def host_graph_name(self) -> str:
        """``-g <文件>`` 启动时登记进 Graph Authority 的图名（文件 stem）。"""

        return Path(self.host_graph).stem

    @property
    def runtime_env(self) -> dict[str, str]:
        if E2E_BACKEND == "ros2":
            return {**self.extra_env, **self.extra_env_ros2}
        return dict(self.extra_env)

    @property
    def runtime_timeout(self) -> float:
        return self.timeout * (_ROS2_TIMEOUT_SCALE if E2E_BACKEND == "ros2" else 1.0)


DEMOS: tuple[DemoSpec, ...] = (
    DemoSpec(
        repo="LabDeviceLanDemo",
        url="https://github.com/Xuwznln/LabDeviceLanDemo",
        ref="1d7139f940b8b2ad5bf16f311ab74fa9d1019446",
        package="lan_demo",
        host_graph="examples/host.json",
        slave_graph="examples/slave.json",
        proof_env={"LAN_DEMO_PROOF_FILE": "proof.json"},
        extra_env={
            "LAN_DEMO_TERMINATE_AFTER": "3",
            "LAN_DEMO_COUNT_RATE": "100",
            "LAN_DEMO_CYCLE_PAUSE": "60",
        },
        workflows=(WorkflowExpectation(name="LAN 远程轮次控制", node_count=3),),
    ),
    DemoSpec(
        repo="LabDeviceWorkstationDemo",
        url="https://github.com/Xuwznln/LabDeviceWorkstationDemo",
        # b9da1ae6：设备不自跑 proof，inspect_endpoints 成为第四步。
        ref="b9da1ae65bcf8e912c2255695cb1f7d220f5fc03",
        package="workstation_demo",
        host_graph="graph/workstation_demo.json",
        # 设备不自跑闭环：串口回环 / 双传感器探测 / 共享端点计数全部由 @workflow 经 API 触发
        workflows=(WorkflowExpectation(name="工作站演示流水", node_count=4),),
    ),
    DemoSpec(
        repo="LabDeviceExceptionDemo",
        url="https://github.com/Xuwznln/LabDeviceExceptionDemo",
        ref="b0355efc8066b299575273979f3c7c338b6011d7",
        package="exception_demo",
        host_graph="graph/exception_demo.json",
        # 该 demo 设备内不自跑闭环：全部路径都是网页式工作流提交 + 决策链。
        workflows=(
            WorkflowExpectation(
                name="异常传播演示",
                node_count=4,
                task_status="failed",
                node_statuses=("succeeded", "succeeded", "succeeded", "failed"),
                error_decision={"action": "abort", "reason": "readme demo e2e 放行失败结果"},
            ),
            WorkflowExpectation(
                name="人工替换恢复演示",
                node_count=2,
                error_decision={
                    "action": "operator_intervention",
                    "reason": "readme demo e2e 人工替换结果",
                    "result": {"success": True, "step_name": "flaky", "replaced_by": "operator"},
                },
            ),
            # retry：节点运行的当前结果是 attempt 2 的成功，attempt 1 的失败留在历史里，任务不中断
            WorkflowExpectation(
                name="重试恢复演示",
                node_count=2,
                attempt_counts=(2, 1),
                error_decision={"action": "retry", "reason": "readme demo e2e 重试瞬时故障"},
            ),
        ),
    ),
    DemoSpec(
        repo="LabDeviceMaterialsDemo",
        url="https://github.com/Xuwznln/LabDeviceMaterialsDemo",
        # c0751d42：改名 + 设备不自跑（两轮闭环都是 @workflow）+ 阶段三「出库装板并加液」
        # （纯 HTTP 上传的工作流由其自带 smoke 覆盖，这里只跑 @workflow 上报的四条）+ 库存需求
        # kind reagent -> lot。阶段三依赖本仓库的 HostLink id/name ResourceSlot 兜底与注册表懒加载
        # PLR 类，@workflow 四条不依赖。
        ref="47e3a5f7a1ef2bd912a8e294f979983e7fa56318",
        package="materials_demo",
        host_graph="graph/host.json",
        slave_graph="graph/slave.json",
        # 设备不自跑闭环：两轮位点 / 物料闭环全部是 @workflow，经 API 顺序运行
        workflows=(
            WorkflowExpectation(name="位点闭环演示", node_count=4),
            WorkflowExpectation(name="物料闭环演示", node_count=6),
            WorkflowExpectation(name="位点操作演示", node_count=3),
            WorkflowExpectation(name="物料流转演示", node_count=5),
        ),
        timeout=120.0,
    ),
    DemoSpec(
        repo="LabDeviceLockDemo",
        url="https://github.com/Xuwznln/LabDeviceLockDemo",
        ref="349c43f9c2c4fbe29ecb32d7874ebfaed8aa848c",
        package="lock_demo",
        host_graph="graph/lock_demo.json",
        # 无设备自跑闭环：锁语义只能在"多个任务同时申请资源"时观察，靠并发组制造竞争。
        workflows=(
            WorkflowExpectation(name="锁演示：准备物料", node_count=1),
            # 动作锁：同设备同动作串行；第二个 occupy 在调度器排队。两次 peek 是 always_free，不排队。
            WorkflowExpectation(name="动作锁：占用 A（第一次）", node_count=1, group="action-lock"),
            WorkflowExpectation(
                name="动作锁：占用 A（第二次）", node_count=1, group="action-lock", expect_waiting=True
            ),
            WorkflowExpectation(name="always_free：探测 A（第一次）", node_count=1, group="action-lock"),
            WorkflowExpectation(name="always_free：探测 A（第二次）", node_count=1, group="action-lock"),
            # 物料锁：B 处理同一块 P1 排在 A 后面；A 同时处理 P2 与 P1 并行。
            WorkflowExpectation(name="物料锁：A 处理 P1", node_count=1, group="material-lock"),
            WorkflowExpectation(
                name="物料锁：B 处理 P1", node_count=1, group="material-lock", expect_waiting=True
            ),
            WorkflowExpectation(name="物料锁：A 处理 P2", node_count=1, group="material-lock"),
            # 审计器读两台探针的账本：四条结论任一不成立即任务失败
            WorkflowExpectation(name="锁账本审计", node_count=1),
        ),
    ),
    DemoSpec(
        repo="LabDeviceInventoryDemo",
        url="https://github.com/Xuwznln/LabDeviceInventoryDemo",
        # b142910b：库存需求 kind reagent -> lot（与本仓库 InventoryRequirement 的 Literal 同步）。
        ref="790e9d3644eea836f888fb59406e01f396b9897f",
        package="inventory_demo",
        host_graph="graph/inventory_demo.json",
        # 每次 e2e 都是全新数据库：入库 100 → 出库 40 → 500 被拒 → 盘点 60/60/0
        workflows=(
            WorkflowExpectation(name="试剂入库：水 100 ml", node_count=1),
            WorkflowExpectation(name="出库成功：分液 40 ml", node_count=2),
            # 库存不足：任务在派发前 failed，节点运行 canceled，设备未被调用
            WorkflowExpectation(
                name="出库不足：分液 500 ml",
                node_count=1,
                task_status="failed",
                node_statuses=("canceled",),
                task_error_code="plan_not_executable",
                task_error_contains="short by 440",
            ),
            WorkflowExpectation(name="库存盘点", node_count=1),
        ),
    ),
)

DEMOS_BY_REPO = {spec.repo: spec for spec in DEMOS}


def workflow_batches(
    expectations: tuple[WorkflowExpectation, ...],
) -> list[list[WorkflowExpectation]]:
    """按 ``group`` 把相邻工作流合并成并发提交批次；无 group 的各成一批。"""

    batches: list[list[WorkflowExpectation]] = []
    for expectation in expectations:
        if (
            expectation.group is not None
            and batches
            and batches[-1][0].group == expectation.group
        ):
            batches[-1].append(expectation)
        else:
            batches.append([expectation])
    return batches


# ---------------------------------------------------------------------------
# 源码解析
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    ).stdout.strip()


def _clone_pinned(spec: DemoSpec, cache_root: Path) -> Path:
    target = cache_root / f"{spec.repo}-{spec.ref[:12]}"
    if (target / ".git").is_dir():
        try:
            if _git("rev-parse", "HEAD", cwd=target) == spec.ref:
                return target
        except (subprocess.SubprocessError, OSError):
            pass
    target.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=target)
    _git("remote", "add", "origin", spec.url, cwd=target)
    _git("fetch", "-q", "--depth", "1", "origin", spec.ref, cwd=target)
    _git("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD", cwd=target)
    return target


def resolve_demo_source(
    spec: DemoSpec, cache_root: Optional[Path] = None
) -> tuple[Path, str]:
    """返回 (仓库根目录, 来源说明)；三种来源都不可用时抛 RuntimeError。"""

    examples_root = os.environ.get("UNILABOS_README_EXAMPLES_ROOT")
    if examples_root:
        candidate = Path(examples_root) / spec.repo
        if (candidate / spec.package).is_dir():
            return candidate, f"UNILABOS_README_EXAMPLES_ROOT ({candidate})"
    sibling = REPO_ROOT.parent / spec.repo
    if (sibling / spec.package).is_dir():
        return sibling, f"sibling checkout ({sibling})"
    cache_root = cache_root or Path(tempfile.gettempdir()) / "unilabos-readme-demos"
    try:
        cloned = _clone_pinned(spec, cache_root)
    except (subprocess.SubprocessError, OSError) as exc:
        raise RuntimeError(
            f"无法取得 {spec.repo}：未设置 UNILABOS_README_EXAMPLES_ROOT、"
            f"没有同级目录 {sibling}，按 {spec.ref[:12]} 克隆失败: {exc}"
        ) from exc
    return cloned, f"pinned clone {spec.ref[:12]} ({cloned})"


# ---------------------------------------------------------------------------
# 进程 / 网络工具
# ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def unilab_command(*args: str) -> list[str]:
    """以当前解释器调用 ``unilab`` CLI（等价于 console script）。"""

    return [sys.executable, "-m", "unilabos", *args]


def ros_domain_id(hostlink_port: int) -> str:
    """由本用例的 HostLink 端口派生一个独立 ROS_DOMAIN_ID，避免并行用例互相发现。"""

    return str(10 + hostlink_port % 190)


def runtime_command(
    *,
    package_dir: Path,
    graph: Path,
    database_root: Path,
    management_port: int,
    hostlink_port: int,
    is_slave: bool,
    backend: str = E2E_BACKEND,
) -> list[str]:
    """按 demo README 记录的启动形态组装 ``unilab`` 运行时命令。

    两种后端都保留 HostLink（host 绑定 / slave 接入）：hostlink 模式承载全部通信，
    ros2 模式承载物料权威与 host 服务，设备动作、Topic 走 ROS 2。
    """

    command = unilab_command(
        "--backend",
        backend,
        "--skip_env_check",
        "--devices",
        str(package_dir),
        "--external_devices_only",
        "--visual",
        "disable",
        "--disable_browser",
        "--port",
        str(management_port),
        "--server_database_root",
        str(database_root),
        "--working_dir",
        str(database_root / "work"),
        "--config",
        str(EXAMPLE_CONFIG),
        "-g",
        str(graph),
    )
    if is_slave:
        command += [
            "--is_slave",
            "--host_node_ip",
            "127.0.0.1",
            "--hostlink_port",
            str(hostlink_port),
        ]
    else:
        command += ["--hostlink_bind", "127.0.0.1", "--hostlink_port", str(hostlink_port)]
        if E2E_TOPOLOGY == "single":
            # 用例自己管这个进程的生死，不要监督进程 / Host 子进程再套层
            command.append("--no_safe_restart")
    if backend == "ros2":
        command += ["--ros_domain_id", ros_domain_id(hostlink_port)]
    return command


def subprocess_env(extra: dict[str, str]) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key not in HARNESS_ENV_VARS
    }
    environment.update(
        {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    )
    environment.update(extra)
    return environment


def stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def api_request(port: int, path: str, payload: Optional[dict[str, Any]] = None) -> Any:
    """请求管理 API；``{"code":0,"data":...}`` 信封自动解包，其余原样返回。"""

    url = f"http://127.0.0.1:{port}/api/v1{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if payload is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(
        url, data=data, headers=headers, method="GET" if payload is None else "POST"
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = json.loads(response.read().decode("utf-8"))
    if isinstance(body, dict) and "code" in body:
        if body["code"] != 0:
            raise RuntimeError(f"管理 API {path} 返回错误: {body}")
        return body.get("data")
    return body


def wait_until(
    predicate,
    *,
    timeout: float,
    interval: float = 0.2,
    abort=None,
    description: str = "",
):
    """轮询直到 predicate 返回真值；``abort()`` 返回真值时立刻失败。"""

    deadline = time.monotonic() + timeout
    last_error: Optional[BaseException] = None
    while time.monotonic() < deadline:
        if abort is not None and abort():
            raise RuntimeError(f"等待 {description or 'condition'} 期间被中止")
        try:
            value = predicate()
        except (urllib.error.URLError, OSError, RuntimeError, KeyError, ValueError) as exc:
            last_error = exc
            value = None
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(
        f"{timeout}s 内未满足 {description or 'condition'}"
        + (f"（最后错误: {last_error!r}）" if last_error else "")
    )


def hostlink_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


__all__ = [
    "DEMOS",
    "DEMOS_BY_REPO",
    "DemoSpec",
    "EXAMPLE_CONFIG",
    "REPO_ROOT",
    "TERMINAL_TASK_STATUSES",
    "WorkflowExpectation",
    "api_request",
    "free_port",
    "hostlink_port_open",
    "resolve_demo_source",
    "runtime_command",
    "stop_process",
    "subprocess_env",
    "unilab_command",
    "wait_until",
]
