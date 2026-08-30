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
    # workflow 与其他四库同构：契约在 protocol/，DDL+行模型在 tables/，
    # 存储层在 repositories/，领域服务在 services/workflow/；不保留自成一体的
    # server/workflow 包
    assert not (server_root / "workflow").exists()
    assert (package_root / "protocol" / "workflow.py").is_file()
    assert (package_root / "protocol" / "workflow_validation.py").is_file()
    assert (server_root / "database" / "tables" / "workflow.py").is_file()
    assert (
        server_root / "database" / "repositories" / "workflow" / "store.py"
    ).is_file()
    assert not (
        server_root / "database" / "repositories" / "workflow" / "ddl.py"
    ).exists()
    assert (server_root / "services" / "workflow" / "service.py").is_file()
    assert (server_root / "services" / "workflow" / "upload.py").is_file()
    assert (server_root / "api" / "workflow.py").is_file()
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

    # 契约层独立于 server；物料适配器归 resources 域；传输实现统一收拢 backend 大类
    assert (package_root / "protocol" / "base.py").is_file()
    assert (package_root / "resources" / "adapters" / "plr_materials.py").is_file()
    assert not (package_root / "adapters").exists()
    assert not (server_root / "protocol").exists()
    assert not (server_root / "adapters").exists()
    backend_root = package_root / "backend"
    assert (backend_root / "hostlink" / "network.py").is_file()
    assert (backend_root / "ros2" / "base_device_node.py").is_file()
    assert (backend_root / "ros2" / "presets" / "host_node.py").is_file()
    # 传输实现平铺；presets 一词保留给"预设节点"，跨 backend 节点出现前不建该层
    assert not (backend_root / "presets").exists()
    assert (backend_root / "dora" / "runtime.py").is_file()
    # 设备执行内核与传输实现同属 backend 大类
    assert (backend_root / "runtime" / "node.py").is_file()
    assert (backend_root / "runtime" / "exception.py").is_file()
    assert not (backend_root / "device_runtime").exists()
    for legacy in ("ros", "hostlink", "dora", "device_runtime", "runtime"):
        assert not (package_root / legacy).exists()
