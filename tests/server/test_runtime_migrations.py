"""runtime.db 的真实版本迁移：0.13 基线（v1）的库升级后保留业务数据。"""
from __future__ import annotations

import sqlite3

import pytest

from unilabos.server.database import DatabaseMigrationRequired, initialize_database
from unilabos.server.database.tables.runtime import RUNTIME_DATABASE

#: 已发布版本的结构指纹，冻结在测试里。
V1_CHECKSUM = "68bbaa4dbedafb58de6a87d5f3a0153ce27165920c3797a9206237605a7e46ed"
V2_CHECKSUM = "8542e457cc32ffe4334964b33cd57f58dc3a0adc89d06505edd8eb2a32b1c443"
V1_WAITER_DDL = """
    CREATE TABLE workflow_resource_waiter (
        job_uuid TEXT PRIMARY KEY REFERENCES workflow_node_job(uuid),
        task_uuid TEXT NOT NULL REFERENCES workflow_task(uuid),
        lock_keys TEXT NOT NULL CHECK(json_valid(lock_keys) AND json_type(lock_keys)='array'),
        blockers TEXT NOT NULL CHECK(json_valid(blockers) AND json_type(blockers)='array')
    )
"""


def _revert_v3(connection) -> None:
    """撤回 v3：attempt 没有预计时长两列，job_runs 投影常量 0.0 / canonical_projection。"""

    view = connection.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name='job_runs'").fetchone()[0]
    old_view = (view.replace("COALESCE(job.estimated_s, 0.0) AS estimated_s", "0.0 AS estimated_s")
                .replace("COALESCE(job.estimate_source, 'unknown') AS estimate_source", "'canonical_projection' AS estimate_source"))
    assert old_view != view
    connection.execute("DROP VIEW job_runs")
    connection.execute("ALTER TABLE workflow_node_job DROP COLUMN estimated_s")
    connection.execute("ALTER TABLE workflow_node_job DROP COLUMN estimate_source")
    connection.execute(old_view)


def _as_v2(path, checksum: str = V2_CHECKSUM) -> None:
    connection = initialize_database(path, RUNTIME_DATABASE)
    connection.execute("PRAGMA foreign_keys = OFF")
    with connection:
        _revert_v3(connection)
        connection.execute("UPDATE schema_identity SET checksum=? WHERE database_key='runtime'", (checksum,))
        connection.execute("PRAGMA user_version = 2")
    connection.close()


def _as_v1(path, checksum: str = V1_CHECKSUM) -> None:
    connection = initialize_database(path, RUNTIME_DATABASE)
    connection.execute("PRAGMA foreign_keys = OFF")
    with connection:
        _revert_v3(connection)
        connection.execute("DROP TABLE workflow_resource_waiter")
        connection.execute(V1_WAITER_DDL)
        connection.execute("INSERT INTO workflow_resource_waiter VALUES ('job-1','task-1','[]','[]')")
        connection.execute("INSERT INTO workflow_operation_category(uuid, name, create_time, update_time) "
                           "VALUES ('keep-me', '保留', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')")
        connection.execute("UPDATE schema_identity SET checksum=? WHERE database_key='runtime'", (checksum,))
        connection.execute("PRAGMA user_version = 1")
    connection.close()


def test_v1_runtime_database_migrates_to_durable_waiter_order(tmp_path) -> None:
    path = tmp_path / "runtime.db"
    _as_v1(path)

    connection = initialize_database(path, RUNTIME_DATABASE)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == RUNTIME_DATABASE.schema_version == 3
        columns = [row[1] for row in connection.execute("PRAGMA table_info(workflow_resource_waiter)")]
        assert "enqueued_at" in columns
        # 等待记录是瞬态事实，迁移时清空、由调度器重新排队；其余业务数据原样保留。
        assert connection.execute("SELECT count(*) FROM workflow_resource_waiter").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM workflow_operation_category WHERE uuid='keep-me'").fetchone()[0] == "保留"
        history = connection.execute("SELECT version FROM schema_migration").fetchall()
        assert [row[0] for row in history] == [2, 3]
    finally:
        connection.close()
    assert list(tmp_path.glob("runtime.db.pre-v3-*.bak")), "迁移前必须先备份"
    sqlite3.connect(next(tmp_path.glob("runtime.db.pre-v3-*.bak"))).close()


def test_v2_runtime_database_gains_attempt_estimates(tmp_path) -> None:
    path = tmp_path / "runtime.db"
    _as_v2(path)

    connection = initialize_database(path, RUNTIME_DATABASE)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_node_job)")}
        assert {"estimated_s", "estimate_source"} <= columns
        view = connection.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name='job_runs'").fetchone()[0]
        assert "COALESCE(job.estimated_s, 0.0)" in view
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migration")] == [3]
    finally:
        connection.close()
    assert list(tmp_path.glob("runtime.db.pre-v3-*.bak"))


def test_same_version_with_a_foreign_fingerprint_is_refused_not_half_migrated(tmp_path) -> None:
    """压平前的开发库也可能停在 user_version 1：指纹不是 0.13 基线就没有迁移路径，按 refuse 保留原样。"""
    path = tmp_path / "runtime.db"
    _as_v1(path, checksum="pre-baseline-dev")
    with pytest.raises(DatabaseMigrationRequired):
        initialize_database(path, RUNTIME_DATABASE).close()
    peek = sqlite3.connect(path)
    try:
        assert peek.execute("PRAGMA user_version").fetchone()[0] == 1
        assert peek.execute("SELECT count(*) FROM workflow_resource_waiter").fetchone()[0] == 1
    finally:
        peek.close()
    assert not list(tmp_path.glob("runtime.db.pre-v*-*.bak"))
