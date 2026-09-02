"""README 示例设备包（四个 demo 仓库）的引用清单、源码解析与运行时驻留工具。

unilabos 侧在这里固定引用四个 demo 仓库（URL + 已验证提交），e2e 用例按以下
顺序取得仓库源码：

1. ``UNILABOS_README_EXAMPLES_ROOT/<仓库名>``（外部预先 checkout）；
2. 与本仓库同级的开发目录 ``../<仓库名>``（本地联调，允许领先于 pinned 提交）；
3. 按 pinned 提交浅克隆到临时缓存目录（需要 git 与网络；CI 走这条）。

pinned 提交与各 demo 仓库 CI 里固定的 Uni-Lab-OS 提交互相锁定：core 有破坏性
改动时先改 demo 并推送，再在此处 bump。
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


@dataclass(frozen=True)
class WorkflowExpectation:
    """一个 ``@workflow`` 模板经管理 API 运行后的期望终态。"""

    name: str
    job_count: int
    task_status: str = "succeeded"
    #: 缺省表示全部 job 都应 succeeded。
    job_statuses: Optional[tuple[str, ...]] = None
    #: 期望失败 attempt 先进入错误决策链；给出网页式决策 payload
    #: （``{"action": "abort"}`` 或携带 ``result`` 的 ``operator_intervention``）。
    error_decision: Optional[dict[str, Any]] = None

    def expected_job_statuses(self) -> tuple[str, ...]:
        return self.job_statuses or tuple("succeeded" for _ in range(self.job_count))


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
    timeout: float = 60.0

    @property
    def host_graph_name(self) -> str:
        """``-g <文件>`` 启动时登记进 Graph Authority 的图名（文件 stem）。"""

        return Path(self.host_graph).stem


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
        workflows=(WorkflowExpectation(name="LAN 远程轮次控制", job_count=3),),
    ),
    DemoSpec(
        repo="LabDeviceWorkstationDemo",
        url="https://github.com/Xuwznln/LabDeviceWorkstationDemo",
        ref="04a8c24e6e6eff1dfc061e9272229eecfbf5e0bb",
        package="workstation_demo",
        host_graph="graph/workstation_demo.json",
        proof_env={"WORKSTATION_DEMO_PROOF_FILE": "proof.json"},
        extra_env={"WORKSTATION_DEMO_START_DELAY": "0.2"},
        workflows=(WorkflowExpectation(name="工作站演示流水", job_count=3),),
    ),
    DemoSpec(
        repo="LabDeviceExceptionDemo",
        url="https://github.com/Xuwznln/LabDeviceExceptionDemo",
        ref="63cbdad4248004440c215beb00088b70920d5e61",
        package="exception_demo",
        host_graph="graph/exception_demo.json",
        # 该 demo 设备内不自跑闭环：全部路径都是网页式工作流提交 + 决策链。
        workflows=(
            WorkflowExpectation(
                name="异常传播演示",
                job_count=4,
                task_status="failed",
                job_statuses=("succeeded", "succeeded", "succeeded", "failed"),
                error_decision={"action": "abort", "reason": "readme demo e2e 放行失败结果"},
            ),
            WorkflowExpectation(
                name="人工替换恢复演示",
                job_count=2,
                error_decision={
                    "action": "operator_intervention",
                    "reason": "readme demo e2e 人工替换结果",
                    "result": {"success": True, "step_name": "flaky", "replaced_by": "operator"},
                },
            ),
        ),
    ),
    DemoSpec(
        repo="LabDeviceSiteDemo",
        url="https://github.com/Xuwznln/LabDeviceSiteDemo",
        ref="84262c3ff67cfcd1719d756c93705344eaf30e78",
        package="site_demo",
        host_graph="graph/host.json",
        slave_graph="graph/slave.json",
        proof_env={
            "SITE_DEMO_PROOF_FILE": "rack-proof.json",
            "SITE_DEMO_BENCH_PROOF_FILE": "bench-proof.json",
        },
        extra_env={"SITE_DEMO_START_DELAY": "0.5"},
        workflows=(
            WorkflowExpectation(name="位点操作演示", job_count=3),
            WorkflowExpectation(name="物料流转演示", job_count=5),
        ),
        timeout=90.0,
    ),
)

DEMOS_BY_REPO = {spec.repo: spec for spec in DEMOS}


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


def runtime_command(
    *,
    package_dir: Path,
    graph: Path,
    database_root: Path,
    management_port: int,
    hostlink_port: int,
    is_slave: bool,
) -> list[str]:
    """按 demo README 记录的启动形态组装 ``unilab`` 运行时命令（hostlink 后端）。"""

    command = unilab_command(
        "--backend",
        "hostlink",
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
    return command


def subprocess_env(extra: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
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
