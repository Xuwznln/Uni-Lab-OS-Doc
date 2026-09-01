"""验证数据库职责身份与 checksum 驱动的 schema 重建策略。"""

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
    """checksum 不一致时删除数据库文件并按当前声明重建。"""
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
        # drift_marker 属于被替换的文件，不应出现在重建后的 schema 中。
        tables = {
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "drift_marker" not in tables
    finally:
        connection.close()


def test_unidentified_database_is_rebuilt(tmp_path) -> None:
    """缺少 schema_identity 的数据库按当前声明重建。"""
    path = tmp_path / "unidentified.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE foreign_fact(uuid TEXT PRIMARY KEY)")
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
        assert "foreign_fact" not in tables
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
