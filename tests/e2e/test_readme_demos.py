"""四个 README demo 仓库的端到端：unilab CLI 建图/登记 → 起微后端 → 管理 API 跑工作流。

每个 demo 走同一条链路（hostlink 后端），全部经真实子进程与 HTTP，不在测试进程内
拼装运行时：

1. ``unilab --check_mode --devices <pkg> --external_devices_only`` 校验设备包注册表；
2. ``unilab graph create --devices <pkg>`` 生成图骨架，与 demo 图声明的设备模板对照；
3. ``unilab -g <demo 图> --devices <pkg> ...`` 启动 host（及 slave）进程——启动即把图
   登记进 Graph Authority、上报 ``@workflow`` 模板、拉起微后端管理 API；
4. 等待设备后台闭环写出 proof；
5. ``unilab graph list/download/upload --port_management`` 经管理 API 读写 Graph Authority；
6. 管理 API 检索工作流、创建任务、（按需）放行错误决策、断言任务与 job 终态。

demo 仓库的引用（URL + pinned 提交）见 :mod:`tests.e2e.readme_demos`。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

import pytest

from tests.e2e.readme_demos import (
    DEMOS,
    TERMINAL_TASK_STATUSES,
    DemoSpec,
    WorkflowExpectation,
    api_request,
    free_port,
    hostlink_port_open,
    resolve_demo_source,
    runtime_command,
    stop_process,
    subprocess_env,
    unilab_command,
    wait_until,
)

CLI_TIMEOUT = 300.0


def _tail(path: Path, lines: int = 80) -> str:
    if not path.is_file():
        return "<no log>"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def _run_cli(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        unilab_command(*args),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLI_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"unilab {' '.join(args)} 退出码 {result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-4000:]}\n--- stderr ---\n{result.stderr[-4000:]}"
    )
    return result


def _load_graph(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _device_template_names(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for node in payload.get("nodes") or []:
        if node.get("type") != "device":
            continue
        name = str(node.get("template_name") or node.get("class") or "").strip()
        if name and name != "host_node":
            names.add(name)
    return names


def _assert_authority_shape(payload: dict[str, Any]) -> None:
    """权威图快照契约：节点都有 uuid、不含 children、设备节点带 template_name。"""

    nodes = payload["nodes"]
    assert nodes, "权威图没有节点"
    for node in nodes:
        assert node.get("uuid"), f"节点缺少权威 uuid: {node.get('id')}"
        assert "children" not in node, f"权威快照不应含 children: {node.get('id')}"
        if node.get("type") == "device":
            assert node.get("template_name"), f"设备节点缺少 template_name: {node.get('id')}"


class _DemoProcesses:
    """host/slave 运行时子进程的启动、就绪等待、日志与清理。"""

    def __init__(self, spec: DemoSpec, repo_root: Path, work_root: Path) -> None:
        self.spec = spec
        self.repo_root = repo_root
        self.package_dir = repo_root / spec.package
        self.work_root = work_root
        self.management_port = free_port()
        self.hostlink_port = free_port()
        self.proof_paths = {
            env_name: work_root / filename for env_name, filename in spec.proof_env.items()
        }
        self.env = subprocess_env(
            {**{name: str(path) for name, path in self.proof_paths.items()}, **spec.extra_env}
        )
        self.host_log = work_root / "host.log"
        self.slave_log = work_root / "slave.log"
        self._handles: list[Any] = []
        self.host: Optional[subprocess.Popen[str]] = None
        self.slave: Optional[subprocess.Popen[str]] = None

    def _spawn(self, command: list[str], log_path: Path) -> subprocess.Popen[str]:
        handle = log_path.open("w", encoding="utf-8")
        self._handles.append(handle)
        return subprocess.Popen(
            command,
            cwd=self.repo_root,
            env=self.env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def any_exited(self) -> bool:
        return any(
            process is not None and process.poll() is not None
            for process in (self.host, self.slave)
        )

    def start(self) -> None:
        spec = self.spec
        self.host = self._spawn(
            runtime_command(
                package_dir=self.package_dir,
                graph=self.repo_root / spec.host_graph,
                database_root=self.work_root / "host-db",
                management_port=self.management_port,
                hostlink_port=self.hostlink_port,
                is_slave=False,
            ),
            self.host_log,
        )
        wait_until(
            lambda: api_request(self.management_port, "/health").get("status") == "ok"
            and hostlink_port_open(self.hostlink_port),
            timeout=min(60.0, spec.timeout),
            abort=self.any_exited,
            description="host 管理 API 与 HostLink 端口就绪",
        )
        if spec.slave_graph is None:
            return
        self.slave = self._spawn(
            runtime_command(
                package_dir=self.package_dir,
                graph=self.repo_root / spec.slave_graph,
                database_root=self.work_root / "slave-db",
                management_port=free_port(),
                hostlink_port=self.hostlink_port,
                is_slave=True,
            ),
            self.slave_log,
        )
        wait_until(
            lambda: len(api_request(self.management_port, "/hostlink/peers")["peers"]) >= 1,
            timeout=min(60.0, spec.timeout),
            abort=self.any_exited,
            description="slave 经 HostLink 接入 host",
        )

    def wait_proofs(self) -> dict[str, dict[str, Any]]:
        wait_until(
            lambda: all(path.is_file() for path in self.proof_paths.values()),
            timeout=self.spec.timeout,
            abort=self.any_exited,
            description=f"闭环 proof {sorted(self.proof_paths)}",
        )
        return {
            name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in self.proof_paths.items()
        }

    def logs(self) -> str:
        text = f"--- host log tail ---\n{_tail(self.host_log)}"
        if self.spec.slave_graph is not None:
            text += f"\n--- slave log tail ---\n{_tail(self.slave_log)}"
        return text

    def stop(self) -> None:
        for process in (self.slave, self.host):
            if process is not None:
                stop_process(process)
        for handle in self._handles:
            handle.close()


def _run_workflow(
    port: int, expectation: WorkflowExpectation, *, timeout: float, abort
) -> dict[str, Any]:
    """经管理 API 找到 @workflow 上报的模板、创建任务并等待终态，返回任务与 job 证据。"""

    def find_workflow():
        listing = api_request(port, "/workflows?page=1&page_size=100")
        matches = [item for item in listing["items"] if item["name"] == expectation.name]
        return matches[0] if matches else None

    workflow = wait_until(
        find_workflow, timeout=timeout, abort=abort, description=f"工作流 {expectation.name!r} 上报可检索"
    )
    task = api_request(
        port, "/workflow-tasks", {"workflow_uuid": workflow["uuid"], "run_mode": "normal"}
    )
    task_uuid = task["uuid"]

    decision = None
    if expectation.resolve_error_decision:

        def pending_decision():
            items = api_request(port, "/error-decisions")["items"]
            matches = [item for item in items if item.get("task_id") == task_uuid]
            return matches[0] if matches else None

        report = wait_until(
            pending_decision, timeout=timeout, abort=abort, description="失败 attempt 进入错误决策链"
        )
        resolved = api_request(
            port,
            f"/error-decisions/{report['decision_id']}",
            {
                "action": expectation.resolve_error_decision,
                "reason": "readme demo e2e 放行失败结果",
                "job_id": report["job_id"],
                "device_id": report["device_id"],
            },
        )
        assert resolved["status"] == "resolved", resolved
        decision = {**report, "resolved_action": expectation.resolve_error_decision}

    def terminal_task():
        current = api_request(port, f"/workflow-tasks/{task_uuid}")
        return current if current.get("status") in TERMINAL_TASK_STATUSES else None

    final = wait_until(
        terminal_task, timeout=timeout, abort=abort, description=f"任务 {task_uuid} 到达终态"
    )
    assert final["status"] == expectation.task_status, (
        f"工作流 {expectation.name!r} 终态 {final['status']!r}，期望 {expectation.task_status!r}"
    )
    jobs = api_request(port, f"/workflow-tasks/{task_uuid}/jobs")
    statuses = [job["status"] for job in jobs]
    assert statuses == list(expectation.expected_job_statuses()), (
        f"工作流 {expectation.name!r} job 终态 {statuses}，期望 {expectation.expected_job_statuses()}"
    )
    return {"workflow": workflow, "task": final, "jobs": jobs, "decision": decision}


@pytest.mark.parametrize("spec", DEMOS, ids=[spec.repo for spec in DEMOS])
def test_readme_demo_end_to_end_via_unilab_cli_and_management_api(
    spec: DemoSpec, tmp_path: Path
) -> None:
    try:
        repo_root, source = resolve_demo_source(spec)
    except RuntimeError as exc:
        pytest.skip(str(exc))
    package_dir = repo_root / spec.package
    cli_env = subprocess_env({})
    cli_work_dir = tmp_path / "cli"
    cli_work_dir.mkdir()
    cli_prefix = ["--working_dir", str(cli_work_dir)]

    # 1) 注册表校验（与主 CI 对外部设备包的门禁同一命令）
    _run_cli(
        [
            "--check_mode",
            "--skip_env_check",
            "--devices",
            str(package_dir),
            "--external_devices_only",
        ],
        cwd=repo_root,
        env=cli_env,
    )

    # 2) unilab graph create：骨架覆盖 demo 图声明的全部设备模板，且为 parent-only 契约
    skeleton_path = tmp_path / "skeleton.json"
    _run_cli(
        ["graph", "create", "--devices", str(package_dir), "-o", str(skeleton_path)],
        cwd=repo_root,
        env=cli_env,
    )
    skeleton = _load_graph(skeleton_path)
    skeleton_templates = {node["template_name"] for node in skeleton["nodes"]}
    demo_graphs = [_load_graph(repo_root / spec.host_graph)]
    if spec.slave_graph is not None:
        demo_graphs.append(_load_graph(repo_root / spec.slave_graph))
    expected_templates = set().union(*(_device_template_names(graph) for graph in demo_graphs))
    assert expected_templates, f"{spec.repo} 的图没有设备节点"
    assert expected_templates <= skeleton_templates, (
        f"graph create 骨架缺少设备模板: {sorted(expected_templates - skeleton_templates)}"
    )
    for node in skeleton["nodes"]:
        assert node["uuid"] and node["parent"] is None and "children" not in node

    # 3) 真实运行时：unilab -g <demo 图> 启动 host（及 slave），启动即登记 Graph Authority
    processes = _DemoProcesses(spec, repo_root, tmp_path / "run")
    processes.work_root.mkdir()
    try:
        try:
            processes.start()
            port = processes.management_port

            # 4) 设备后台闭环 proof
            proofs = processes.wait_proofs()
            for name, proof in proofs.items():
                assert proof.get("success") is True, f"{name} 闭环失败: {proof}"
                assert proof.get("backend") == "hostlink", f"{name} backend 不匹配: {proof}"

            # 5) Graph Authority：启动登记的图可经 API/CLI 读回，骨架可经 CLI 上传
            record = api_request(port, f"/graphs/{spec.host_graph_name}")
            assert record["name"] == spec.host_graph_name and record["revision"] >= 1
            _assert_authority_shape(record["payload"])
            assert _device_template_names(record["payload"]) == _device_template_names(
                demo_graphs[0]
            )

            _run_cli(
                [*cli_prefix, "graph", "list", "--port_management", str(port)],
                cwd=cli_work_dir,
                env=cli_env,
            )
            downloaded_path = tmp_path / "downloaded.json"
            _run_cli(
                [
                    *cli_prefix,
                    "graph",
                    "download",
                    spec.host_graph_name,
                    "-o",
                    str(downloaded_path),
                    "--port_management",
                    str(port),
                ],
                cwd=cli_work_dir,
                env=cli_env,
            )
            assert _load_graph(downloaded_path) == record["payload"]

            skeleton_name = f"{spec.package}-skeleton"
            _run_cli(
                [
                    *cli_prefix,
                    "graph",
                    "upload",
                    "-f",
                    str(skeleton_path),
                    "-n",
                    skeleton_name,
                    "--port_management",
                    str(port),
                ],
                cwd=cli_work_dir,
                env=cli_env,
            )
            uploaded = api_request(port, f"/graphs/{skeleton_name}")
            assert uploaded["revision"] == 1
            _assert_authority_shape(uploaded["payload"])
            assert len(uploaded["payload"]["nodes"]) == len(skeleton["nodes"])
            listing = api_request(port, "/graphs?page=1&page_size=100")
            assert {spec.host_graph_name, skeleton_name} <= {
                item["name"] for item in listing["items"]
            }

            # 6) 管理 API 运行 @workflow 上报的工作流并断言终态
            results = [
                _run_workflow(
                    port, expectation, timeout=spec.timeout, abort=processes.any_exited
                )
                for expectation in spec.workflows
            ]
            assert [result["workflow"]["name"] for result in results] == [
                expectation.name for expectation in spec.workflows
            ]
        except Exception as exc:  # noqa: BLE001 - 失败时附带运行时日志再抛出
            pytest.fail(
                f"{spec.repo} ({source}) 失败: {type(exc).__name__}: {exc}\n{processes.logs()}",
                pytrace=False,
            )
    finally:
        processes.stop()
