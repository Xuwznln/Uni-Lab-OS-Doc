"""每个物理数据库保有稳定职责；schema 变化时删除重建（未发布期约定）。"""

from __future__ import annotations

import sqlite3

import pytest

from unilabos.server.database import (
    DATABASE_SPECS,
    DatabaseIdentityConflict,
    initialize_database,
)


def test_one_database_file_cannot_change_role(tmp_path) -> None:
    path = tmp_path / "one-role.db"
    connection = initialize_database(path, DATABASE_SPECS["runtime"])
    connection.close()

    with pytest.raises(DatabaseIdentityConflict, match="cannot open it as 'materials'"):
        initialize_database(path, DATABASE_SPECS["materials"])

    connection = sqlite3.connect(path)
    try:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()


def test_schema_drift_rebuilds_database(tmp_path) -> None:
    """checksum 不一致时不再报错，而是删除文件重建。"""
    path = tmp_path / "runtime.db"
    connection = initialize_database(path, DATABASE_SPECS["runtime"])
    with connection:
        connection.execute(
            "UPDATE schema_identity SET checksum='tampered' "
            "WHERE database_key='runtime'"
        )
        connection.execute("CREATE TABLE drift_marker(x TEXT)")
    connection.close()

    connection = initialize_database(path, DATABASE_SPECS["runtime"])
    try:
        row = connection.execute(
            "SELECT database_key, checksum FROM schema_identity"
        ).fetchone()
        assert tuple(row) == ("runtime", DATABASE_SPECS["runtime"].checksum)
        # 旧文件（含 drift_marker 表）随重建一起被丢弃
        tables = {
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "drift_marker" not in tables
    finally:
        connection.close()


def test_legacy_database_without_identity_is_rebuilt(tmp_path) -> None:
    """schema_migration 时代或未知来源的库直接重建。"""
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE legacy_fact(uuid TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    connection = initialize_database(path, DATABASE_SPECS["runtime"])
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "legacy_fact" not in tables
        assert "schema_identity" in tables
    finally:
        connection.close()


def test_identity_records_real_embedded_checksum(tmp_path) -> None:
    spec = DATABASE_SPECS["history"]
    connection = initialize_database(tmp_path / spec.filename, spec)
    try:
        row = connection.execute(
            "SELECT database_key, checksum FROM schema_identity"
        ).fetchone()
        assert tuple(row) == ("history", spec.checksum)
        assert len(row[1]) == 64
    finally:
        connection.close()
