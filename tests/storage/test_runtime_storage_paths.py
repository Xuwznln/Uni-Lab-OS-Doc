"""运行时存储路径（RuntimeStoragePaths）的公开接口测试。"""

from __future__ import annotations

import sqlite3

import pytest

from unilabos.app.edge_control.client import EdgeControlSettings
from unilabos.app.main import configure_runtime_storage
from unilabos.app.scheduler import integration as scheduler_integration
from unilabos.config.config import BasicConfig, EdgeControlConfig
from unilabos.storage.paths import RuntimeStorageConflict, RuntimeStoragePaths
from unilabos.storage.profiles import SchedulerAuthorityProfile
from unilabos.workflow.composition import (
    compose_workflow_runtime,
    reset_workflow_service_for_test,
)


def _write_domain_fact(path) -> None:
    """在测试库写入最小工作流事实，用于模拟已有权威数据。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE workflow_task (uuid TEXT PRIMARY KEY NOT NULL)"
        )
        connection.execute("INSERT INTO workflow_task(uuid) VALUES ('task-1')")
        connection.commit()
    finally:
        connection.close()


def test_resolve_uses_one_four_store_layout(tmp_path) -> None:
    """默认解析应产生一份确定且职责分离的四库存储布局。"""

    working_dir = tmp_path / "workspace"
    home_dir = tmp_path / "home"

    paths = RuntimeStoragePaths.resolve(
        {
            "working_dir": working_dir,
            "home_dir": home_dir,
        }
    )

    assert paths.working_dir == working_dir.resolve()
    assert (
        paths.workflow_db == (home_dir / ".unilabos" / "workflow_history.db").resolve()
    )
    assert paths.inventory_db == (home_dir / ".unilabos" / "inventory.db").resolve()
    assert (
        paths.device_state_db == (home_dir / ".unilabos" / "device_state.db").resolve()
    )
    assert paths.edge_control_db == (working_dir / "edge_control.db").resolve()
    assert paths.legacy_workflow_history_enabled is True


def test_resolve_normalizes_explicit_paths_and_off_switches(tmp_path) -> None:
    """显式相对路径应归一化，关闭开关不得偷偷创建可选存储。"""

    working_dir = tmp_path / "workspace"

    paths = RuntimeStoragePaths.resolve(
        {
            "working_dir": working_dir,
            "home_dir": tmp_path / "home",
            "edge_inventory_db": "stores/inventory.sqlite",
            "edge_device_state_db": "off",
            "edge_workflow_history_db": "off",
            "edge_state_db": "stores/edge-control.sqlite",
        }
    )

    assert paths.inventory_db == (working_dir / "stores/inventory.sqlite").resolve()
    assert paths.device_state_db is None
    assert (
        paths.workflow_db
        == (tmp_path / "home" / ".unilabos" / "workflow_history.db").resolve()
    )
    assert paths.legacy_workflow_history_enabled is False
    assert (
        paths.edge_control_db == (working_dir / "stores/edge-control.sqlite").resolve()
    )


def test_resolve_reuses_the_only_existing_workflow_authority(tmp_path) -> None:
    """仅有一个旧库含领域事实时应沿用其工作流权威。"""

    working_dir = tmp_path / "workspace"
    existing_workflow_db = working_dir / "workflow_history.db"
    _write_domain_fact(existing_workflow_db)

    paths = RuntimeStoragePaths.resolve(
        {
            "working_dir": working_dir,
            "home_dir": tmp_path / "home",
        }
    )

    assert paths.workflow_db == existing_workflow_db.resolve()


def test_resolve_fails_closed_when_two_workflow_databases_have_facts(tmp_path) -> None:
    """两个旧库都有领域事实时应失败关闭，避免产生双真相。"""

    working_dir = tmp_path / "workspace"
    home_dir = tmp_path / "home"
    _write_domain_fact(working_dir / "workflow_history.db")
    _write_domain_fact(home_dir / ".unilabos" / "workflow_history.db")

    with pytest.raises(RuntimeStorageConflict, match="工作流数据库"):
        RuntimeStoragePaths.resolve(
            {
                "working_dir": working_dir,
                "home_dir": home_dir,
            }
        )


def test_resolve_is_deterministic_for_all_composition_callers(tmp_path) -> None:
    """相同输入必须为所有组合根解析出完全相同的路径对象值。"""

    config = {
        "working_dir": tmp_path / "workspace",
        "home_dir": tmp_path / "home",
        "edge_inventory_db": tmp_path / "inventory.db",
        "edge_device_state_db": tmp_path / "device-state.db",
        "edge_workflow_history_db": tmp_path / "workflow.db",
        "edge_state_db": tmp_path / "edge-control.db",
    }

    assert RuntimeStoragePaths.resolve(config) == RuntimeStoragePaths.resolve(config)


def test_startup_publishes_one_resolved_object_to_all_composition_roots(
    tmp_path,
    monkeypatch,
) -> None:
    """启动组合根应向调度、工作流和 Edge 控制发布同一解析结果。"""

    monkeypatch.setattr(BasicConfig, "runtime_storage_paths", None)
    monkeypatch.setattr(
        BasicConfig,
        "scheduler_authority_profile",
        "local_scheduler",
    )
    monkeypatch.setattr(EdgeControlConfig, "state_db", "")
    args = {
        "app_bridges": ["fastapi"],
        "scheduler_authority_profile": "local_scheduler",
        "edge_inventory_db": "stores/inventory.db",
        "edge_device_state_db": "stores/device-state.db",
        "edge_workflow_history_db": "stores/workflow.db",
        "edge_state_db": "stores/edge-control.db",
        "home_dir": tmp_path / "home",
    }

    paths, profile = configure_runtime_storage(args, working_dir=tmp_path)

    assert BasicConfig.runtime_storage_paths is paths
    assert BasicConfig.scheduler_authority_profile == profile.value
    assert EdgeControlConfig.state_db == str(paths.edge_control_db)
    EdgeControlConfig.state_db = ""
    assert EdgeControlSettings.from_config().state_db == str(paths.edge_control_db)


def test_workflow_composition_uses_injected_path_and_fixed_profile(tmp_path) -> None:
    """工作流组合根必须复用注入路径，并拒绝运行期切换权威模式。"""

    paths = RuntimeStoragePaths.resolve(
        {
            "working_dir": tmp_path,
            "home_dir": tmp_path / "home",
            "edge_workflow_history_db": tmp_path / "workflow.db",
        }
    )
    reset_workflow_service_for_test()
    try:
        service = compose_workflow_runtime(
            paths,
            authority_profile=SchedulerAuthorityProfile.LOCAL_SCHEDULER,
        )
        assert service is compose_workflow_runtime(
            paths,
            authority_profile=SchedulerAuthorityProfile.LOCAL_SCHEDULER,
        )
        assert paths.workflow_db.exists()
        with pytest.raises(RuntimeError, match="authority profile"):
            compose_workflow_runtime(
                paths,
                authority_profile=SchedulerAuthorityProfile.BACKEND_CONTROLLED,
            )
    finally:
        reset_workflow_service_for_test()


def test_edge_scheduler_composition_uses_injected_store_paths(tmp_path) -> None:
    """Edge 调度器应仅打开统一注入且在本模式启用的存储路径。"""

    paths = RuntimeStoragePaths.resolve(
        {
            "working_dir": tmp_path,
            "home_dir": tmp_path / "home",
            "edge_inventory_db": "off",
            "edge_device_state_db": tmp_path / "device-state.db",
            "edge_workflow_history_db": tmp_path / "workflow.db",
        }
    )
    scheduler_integration.reset_for_test()
    backend = None
    try:
        _scheduler, backend = scheduler_integration.setup_edge_scheduler(
            storage_paths=paths,
            host_node_getter=lambda: None,
        )
        assert paths.device_state_db is not None
        assert paths.device_state_db.exists()
        assert paths.workflow_db.exists()
    finally:
        if backend is not None:
            backend.stop()
        scheduler_integration.reset_for_test()
