"""The public `unilab workspace` command bypasses product runtime composition."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from unilabos.app.main import main


def test_workspace_status_returns_stable_offline_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "unilab",
            "workspace",
            "status",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "unilab-workspace-host/v1"
    assert payload["workspacePath"] == str(workspace)
    assert payload["host"]["phase"] == "offline"
    assert set(payload["components"]) == {"backend", "edge", "plc", "renderer"}


def test_workspace_restart_uses_the_shared_client_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[tuple[str, object]] = []

    class Client:
        def execute(self, command: str, **options: object) -> dict[str, object]:
            calls.append((command, options))
            return {"operationId": "cli-restart", "phase": "succeeded"}

    monkeypatch.setattr(
        "unilabos.workspace_host.cli.ensure_workspace_host",
        lambda _workspace: Client(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "unilab",
            "workspace",
            "restart",
            "--workspace",
            str(workspace),
            "--component",
            "os",
            "--runtime-mode",
            "normal",
            "--operation-id",
            "cli-restart",
            "--wait",
            "9",
            "--json",
        ],
    )

    main()

    assert json.loads(capsys.readouterr().out)["phase"] == "succeeded"
    assert calls == [
        (
            "os.restart",
            {
                "parameters": {"runtimeMode": "normal"},
                "operation_id": "cli-restart",
                "timeout": 9.0,
            },
        )
    ]


def test_workspace_start_passes_runtime_mode_to_scheduler_and_edge_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一条 start 命令把运行配置交给 Scheduler 与 Edge 两个进程。

    参数：``tmp_path`` 隔离工作区，``monkeypatch`` 替换外部 Host 客户端，
    ``capsys`` 捕获 CLI 输出。返回：无。异常：默认命令或参数发生回归时失败。
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[tuple[str, object]] = []

    class Client:
        def execute(self, command: str, **options: object) -> dict[str, object]:
            """记录 CLI 通过公开客户端提交的 Host 操作。"""

            calls.append((command, options))
            return {"operationId": "stack-start", "phase": "succeeded"}

    monkeypatch.setattr(
        "unilabos.workspace_host.cli.ensure_workspace_host",
        lambda _workspace: Client(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "unilab",
            "workspace",
            "start",
            "--workspace",
            str(workspace),
            "--runtime-mode",
            "normal",
            "--operation-id",
            "stack-start",
            "--wait",
            "30",
            "--json",
        ],
    )

    main()

    assert json.loads(capsys.readouterr().out)["phase"] == "succeeded"
    assert calls == [
        (
            "workspace.start",
            {
                "parameters": {
                    "runtimeMode": "normal",
                },
                "operation_id": "stack-start",
                "timeout": 30.0,
            },
        )
    ]


def test_workspace_reset_local_requires_confirmation_and_uses_shared_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[tuple[str, object]] = []

    class Client:
        def execute(self, command: str, **options: object) -> dict[str, object]:
            calls.append((command, options))
            return {"operationId": "reset-local", "phase": "succeeded"}

    monkeypatch.setattr(
        "unilabos.workspace_host.cli.ensure_workspace_host",
        lambda _workspace: Client(),
    )
    base_arguments = [
        "unilab",
        "workspace",
        "reset-local",
        "--workspace",
        str(workspace),
        "--json",
    ]
    monkeypatch.setattr(sys, "argv", base_arguments)
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == (
        "confirmation_required"
    )
    assert calls == []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            *base_arguments,
            "--yes",
            "--graph",
            "deployment/graphs/lab.json",
            "--runtime-mode",
            "normal",
            "--operation-id",
            "reset-local",
            "--wait",
            "15",
        ],
    )
    main()

    assert json.loads(capsys.readouterr().out)["phase"] == "succeeded"
    assert calls == [
        (
            "local.reset-state",
            {
                "parameters": {
                    "graphPath": "deployment/graphs/lab.json",
                    "runtimeMode": "normal",
                },
                "operation_id": "reset-local",
                "timeout": 15.0,
            },
        )
    ]
