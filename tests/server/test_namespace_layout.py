"""微后端代码只能从 ``unilabos.server`` 命名空间导入。"""

from __future__ import annotations

from pathlib import Path

import unilabos


def test_microbackend_namespaces_live_under_server() -> None:
    package_root = Path(unilabos.__file__).resolve().parent

    for removed in ("workflow", "scheduler", "storage"):
        assert not (package_root / removed).exists()
    assert not (package_root / "app" / "scheduler").exists()

    server_root = package_root / "server"
    assert (server_root / "workflow" / "upload.py").is_file()
    # workflow 的 HTTP API 归位 api/，SQLite 存储层归位 database/repositories/
    assert (server_root / "api" / "workflow.py").is_file()
    assert not (server_root / "workflow" / "api.py").exists()
    assert not (server_root / "workflow" / "store.py").exists()
    assert (
        server_root / "database" / "repositories" / "workflow" / "store.py"
    ).is_file()
    # 未发布阶段不维护 migration 链，DDL 与表模型同文件
    assert not (server_root / "database" / "migrations").exists()
    assert not (server_root / "scheduler").exists()
    for module_name in (
        "composition.py",
        "coordinator.py",
        "execution.py",
        "execution_queue.py",
        "incidents.py",
        "inventory.py",
        "telemetry.py",
    ):
        assert (server_root / "backend" / module_name).is_file()
    assert (server_root / "backend" / "scheduler" / "service.py").is_file()
    assert (
        server_root / "backend" / "scheduler" / "dag" / "executor.py"
    ).is_file()
    assert (server_root / "composition.py").is_file()
    assert not list((server_root / "storage").glob("*.py"))
    assert (package_root / "hostlink" / "network.py").is_file()
