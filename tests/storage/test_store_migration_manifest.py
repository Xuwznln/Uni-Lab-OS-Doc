"""四库迁移清单（Store Migration Manifest）的公开接口测试。"""

from __future__ import annotations

import pytest

from unilabos.app.scheduler.inventory.store import SCHEMA_VERSION
from unilabos.storage.migrations import (
    MigrationReadiness,
    StoreLayoutConflict,
    build_store_migration_manifest,
    validate_store_layout,
)
from unilabos.storage.paths import RuntimeStoragePaths
from unilabos.storage.profiles import SchedulerAuthorityProfile


def test_manifest_declares_four_store_roles_and_migration_owners(tmp_path) -> None:
    """迁移清单应完整声明四库角色、所有者和版本准备状态。"""

    paths = RuntimeStoragePaths.resolve(
        {
            "working_dir": tmp_path / "workspace",
            "home_dir": tmp_path / "home",
        }
    )

    manifest = build_store_migration_manifest(paths)

    assert set(manifest) == {"workflow", "inventory", "device_state", "edge_control"}
    assert manifest["workflow"].database_path == paths.workflow_db
    assert manifest["inventory"].database_path == paths.inventory_db
    assert manifest["device_state"].database_path == paths.device_state_db
    assert manifest["edge_control"].database_path == paths.edge_control_db
    assert len({entry.migration_owner for entry in manifest.values()}) == 4
    assert manifest["inventory"].schema_version == SCHEMA_VERSION
    assert manifest["inventory"].readiness is MigrationReadiness.VERSIONED
    assert manifest["workflow"].readiness is MigrationReadiness.BOOTSTRAP_REQUIRED
    assert manifest["device_state"].readiness is MigrationReadiness.BOOTSTRAP_REQUIRED
    assert manifest["edge_control"].readiness is MigrationReadiness.BOOTSTRAP_REQUIRED


def test_manifest_opens_stores_only_in_their_authority_profile(tmp_path) -> None:
    """每类存储只能在拥有对应读写权威的运行模式中打开。"""

    paths = RuntimeStoragePaths.resolve(
        {
            "working_dir": tmp_path / "workspace",
            "home_dir": tmp_path / "home",
        }
    )
    manifest = build_store_migration_manifest(paths)

    local = SchedulerAuthorityProfile.LOCAL_SCHEDULER
    backend = SchedulerAuthorityProfile.BACKEND_CONTROLLED
    recovery = SchedulerAuthorityProfile.OFFLINE_RECOVERY

    assert manifest["workflow"].opens_in(local)
    assert manifest["workflow"].opens_in(backend)
    assert manifest["workflow"].opens_in(recovery)
    assert manifest["inventory"].opens_in(local)
    assert not manifest["inventory"].opens_in(backend)
    assert manifest["inventory"].opens_in(recovery)
    assert manifest["device_state"].opens_in(local)
    assert not manifest["device_state"].opens_in(backend)
    assert manifest["device_state"].opens_in(recovery)
    assert not manifest["edge_control"].opens_in(local)
    assert manifest["edge_control"].opens_in(backend)
    assert not manifest["edge_control"].opens_in(recovery)


def test_layout_rejects_one_database_for_different_authorities(tmp_path) -> None:
    """不同权威职责指向同一 SQLite 时必须在启动前拒绝。"""

    shared = (tmp_path / "shared.db").resolve()
    paths = RuntimeStoragePaths(
        working_dir=tmp_path.resolve(),
        workflow_db=shared,
        inventory_db=shared,
        device_state_db=(tmp_path / "device.db").resolve(),
        edge_control_db=(tmp_path / "edge.db").resolve(),
    )

    with pytest.raises(StoreLayoutConflict, match="独立 SQLite"):
        validate_store_layout(build_store_migration_manifest(paths))


def test_disabled_optional_store_remains_declared_but_closed(tmp_path) -> None:
    """关闭的可选存储仍应在清单中可审计，但不得被打开。"""

    paths = RuntimeStoragePaths.resolve(
        {
            "working_dir": tmp_path / "workspace",
            "home_dir": tmp_path / "home",
            "edge_inventory_db": "off",
            "edge_device_state_db": "off",
        }
    )

    manifest = build_store_migration_manifest(paths)

    assert manifest["inventory"].database_path is None
    assert manifest["device_state"].database_path is None
    assert not manifest["inventory"].opens_in(SchedulerAuthorityProfile.LOCAL_SCHEDULER)
    assert not manifest["device_state"].opens_in(
        SchedulerAuthorityProfile.LOCAL_SCHEDULER
    )
