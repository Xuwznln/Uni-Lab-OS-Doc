"""四类 SQLite 的迁移所有者、版本状态与布局兼容检查。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from unilabos.app.scheduler.inventory.store import SCHEMA_VERSION
from unilabos.storage.paths import RuntimeStoragePaths
from unilabos.storage.profiles import SchedulerAuthorityProfile


class StoreLayoutConflict(RuntimeError):
    """不同权威或读写特征的存储被配置到同一 SQLite。"""


class MigrationReadiness(str, Enum):
    """存储的版本化迁移准备状态。"""

    VERSIONED = "versioned"
    BOOTSTRAP_REQUIRED = "bootstrap_required"


@dataclass(frozen=True)
class StoreMigrationManifest:
    """一个 SQLite 存储的迁移清单（Migration Manifest）。"""

    store_key: str
    database_path: Path | None
    role: str
    migration_owner: str
    schema_version: int | None
    readiness: MigrationReadiness
    enabled_profiles: frozenset[SchedulerAuthorityProfile]

    def opens_in(self, profile: SchedulerAuthorityProfile | str) -> bool:
        return self.database_path is not None and (
            SchedulerAuthorityProfile.parse(profile) in self.enabled_profiles
        )


def build_store_migration_manifest(
    paths: RuntimeStoragePaths,
) -> dict[str, StoreMigrationManifest]:
    """为同一组解析路径建立四库清单，不打开任何数据库。"""

    local_profiles = frozenset(
        {
            SchedulerAuthorityProfile.LOCAL_SCHEDULER,
            SchedulerAuthorityProfile.OFFLINE_RECOVERY,
        }
    )
    return {
        "workflow": StoreMigrationManifest(
            store_key="workflow",
            database_path=paths.workflow_db,
            role="local_workflow_scheduler_authority",
            migration_owner="unilabos.workflow",
            schema_version=None,
            readiness=MigrationReadiness.BOOTSTRAP_REQUIRED,
            enabled_profiles=frozenset(SchedulerAuthorityProfile),
        ),
        "inventory": StoreMigrationManifest(
            store_key="inventory",
            database_path=paths.inventory_db,
            role="local_inventory_authority",
            migration_owner="unilabos.app.scheduler.inventory",
            schema_version=SCHEMA_VERSION,
            readiness=MigrationReadiness.VERSIONED,
            enabled_profiles=local_profiles,
        ),
        "device_state": StoreMigrationManifest(
            store_key="device_state",
            database_path=paths.device_state_db,
            role="device_telemetry_projection",
            migration_owner="unilabos.app.scheduler.device_state",
            schema_version=None,
            readiness=MigrationReadiness.BOOTSTRAP_REQUIRED,
            enabled_profiles=local_profiles,
        ),
        "edge_control": StoreMigrationManifest(
            store_key="edge_control",
            database_path=paths.edge_control_db,
            role="backend_edge_delivery_store",
            migration_owner="unilabos.app.edge_control",
            schema_version=None,
            readiness=MigrationReadiness.BOOTSTRAP_REQUIRED,
            enabled_profiles=frozenset({SchedulerAuthorityProfile.BACKEND_CONTROLLED}),
        ),
    }


def validate_store_layout(
    manifest: Mapping[str, StoreMigrationManifest],
) -> None:
    """拒绝把不同职责的存储静默合并成一个 SQLite 文件。"""

    owners_by_path: dict[Path, list[str]] = {}
    for entry in manifest.values():
        if entry.database_path is None:
            continue
        owners_by_path.setdefault(entry.database_path.resolve(), []).append(
            entry.store_key
        )
    conflicts = {
        path: store_keys
        for path, store_keys in owners_by_path.items()
        if len(store_keys) > 1
    }
    if conflicts:
        details = "; ".join(
            f"{path}: {', '.join(store_keys)}"
            for path, store_keys in sorted(
                conflicts.items(), key=lambda item: str(item[0])
            )
        )
        raise StoreLayoutConflict("不同权威与读写特征必须使用独立 SQLite；" + details)


__all__ = [
    "MigrationReadiness",
    "StoreLayoutConflict",
    "StoreMigrationManifest",
    "build_store_migration_manifest",
    "validate_store_layout",
]
