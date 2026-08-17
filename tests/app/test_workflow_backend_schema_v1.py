"""Workflow SQLite v1 的 Backend-shaped Schema 与旧库迁移回归。"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from unilabos.workflow.schema import WORKFLOW_SCHEMA_VERSION
from unilabos.workflow.store import WorkflowStore, _SCHEMA


RUNTIME_TABLES = {
    "workflow_task_command",
    "execution_lock_lease",
    "workflow_node_job_result",
    "workflow_node_job_feedback_history",
    "workflow_intervention",
    "workflow_intervention_command",
    "workflow_event_outbox",
    "workflow_execution_hold",
    "workflow_job_status_projection",
    "workflow_manual_confirmation",
}


def _table_names(store: WorkflowStore) -> set[str]:
    return {
        str(row["name"])
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _create_v0_database(path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA)
    connection.execute(
        """
        INSERT INTO workflow(
            uuid, create_time, update_time, deleted_at, description,
            meta_data, name, tags, revision
        ) VALUES ('workflow-1', 't0', 't0', NULL, NULL, '{}', 'legacy', '[]', 1)
        """
    )
    connection.execute(
        """
        INSERT INTO workflow_task(
            uuid, create_time, update_time, deleted_at, description,
            meta_data, workflow_uuid, status, workflow_snapshot,
            execution_plan, run_mode, target_node_uuid, control_status,
            cleanup_status, trace_context, input, output, error_info
        ) VALUES (
            'task-1', 't1', 't1', NULL, NULL, '{}', 'workflow-1',
            'success', '{}', '{}', 'normal', NULL, 'active', 'none',
            '{}', '{}', '{}', '[]'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO workflow_node_job(
            uuid, create_time, update_time, deleted_at, description,
            meta_data, workflow_task_uuid, workflow_node_uuid,
            feedback_sequence, topological_index, executor_kind,
            execution_policy, execution_timeout_seconds, status, attempt,
            param, feedback_data, return_info, control_data, error_info
        ) VALUES (
            'job-1', 't2', 't2', NULL, NULL, '{}', 'task-1', 'node-1',
            0, 0, 'compute', '{}', 0, 'cancelled', 1, '{}', '{}', '{}',
            '{}', '[]'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO frontend_event(event, data, create_time)
        VALUES ('workflow.authoring.changed', '{"workflow_uuid":"workflow-1"}', 't3')
        """
    )
    connection.commit()
    connection.close()


def test_new_workflow_database_has_versioned_runtime_fact_tables(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.db")
    try:
        assert (
            store._conn.execute("PRAGMA user_version").fetchone()[0]
            == WORKFLOW_SCHEMA_VERSION
        )
        assert RUNTIME_TABLES <= _table_names(store)

        task_columns = {
            row["name"]: row
            for row in store._conn.execute(
                "PRAGMA table_info(workflow_task)"
            ).fetchall()
        }
        assert task_columns["workflow_uuid"]["notnull"] == 0
        assert {
            "execution_kind",
            "idempotency_key",
            "request_fingerprint",
        } <= task_columns.keys()

        event_columns = {
            row["name"]
            for row in store._conn.execute(
                "PRAGMA table_info(frontend_event)"
            ).fetchall()
        }
        assert event_columns == {
            "sequence",
            "uuid",
            "create_time",
            "type",
            "aggregate_uuid",
            "payload",
        }
    finally:
        store.close()


def test_v0_workflow_database_migrates_rows_and_event_cursor(tmp_path):
    path = tmp_path / "workflow.db"
    _create_v0_database(path)

    store = WorkflowStore(path)
    try:
        task = store.get_task("task-1")
        assert task["workflow_uuid"] == "workflow-1"
        assert task["execution_kind"] == "workflow"
        assert task["status"] == "succeeded"
        job = store.get_job("job-1")
        assert job["workflow_task_uuid"] == "task-1"
        assert job["status"] == "canceled"

        events = store.list_events()
        assert events == [
            {
                "id": 1,
                "uuid": "00000000-0000-4000-8000-000000000001",
                "event": "workflow.authoring.changed",
                "aggregate_uuid": "workflow-1",
                "data": {"workflow_uuid": "workflow-1"},
                "create_time": "t3",
            }
        ]
        assert RUNTIME_TABLES <= _table_names(store)
    finally:
        store.close()


def test_workflow_runtime_constraints_match_backend_idempotency(tmp_path):
    path = tmp_path / "workflow.db"
    _create_v0_database(path)
    store = WorkflowStore(path)
    try:
        now = "2026-08-11T00:00:00Z"
        base = (now, now, "{}")
        store._conn.execute(
            """
            INSERT INTO workflow_task_command(
                uuid, create_time, update_time, meta_data,
                workflow_task_uuid, type, idempotency_key, status,
                result, trace_context
            ) VALUES (?, ?, ?, ?, 'task-1', 'pause', 'same-key', 'pending', '{}', '{}')
            """,
            ("command-1", *base),
        )
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                """
                INSERT INTO workflow_task_command(
                    uuid, create_time, update_time, meta_data,
                    workflow_task_uuid, type, idempotency_key, status,
                    result, trace_context
                ) VALUES (?, ?, ?, ?, 'task-1', 'resume', 'same-key', 'pending', '{}', '{}')
                """,
                ("command-2", *base),
            )

        store._conn.execute(
            """
            INSERT INTO workflow_node_job_feedback_history(
                uuid, create_time, update_time, meta_data,
                workflow_node_job_uuid, sequence, feedback_type, data,
                observed_at, received_at, idempotency_key
            ) VALUES (?, ?, ?, ?, 'job-1', 1, 'progress', '{}', ?, ?, 'feedback-1')
            """,
            ("feedback-1", *base, now, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                """
                INSERT INTO workflow_node_job_feedback_history(
                    uuid, create_time, update_time, meta_data,
                    workflow_node_job_uuid, sequence, feedback_type, data,
                    observed_at, received_at, idempotency_key
                ) VALUES (?, ?, ?, ?, 'job-1', 1, 'progress', '{}', ?, ?, 'feedback-2')
                """,
                ("feedback-2", *base, now, now),
            )

        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                """
                INSERT INTO workflow_task(
                    uuid, create_time, update_time, meta_data, workflow_uuid,
                    status, workflow_snapshot, execution_plan, run_mode,
                    control_status, cleanup_status, trace_context, input,
                    output, error_info, execution_kind, idempotency_key,
                    request_fingerprint
                ) VALUES (
                    'bad-ad-hoc', ?, ?, '{}', NULL, 'pending', '{}', '{}',
                    'normal', 'active', 'none', '{}', '{}', '{}', '[]',
                    'ad_hoc_device_action', NULL, ''
                )
                """,
                (now, now),
            )

        store._conn.execute(
            """
            INSERT INTO workflow_task(
                uuid, create_time, update_time, meta_data, workflow_uuid,
                status, workflow_snapshot, execution_plan, run_mode,
                control_status, cleanup_status, trace_context, input,
                output, error_info, execution_kind, idempotency_key,
                request_fingerprint
            ) VALUES (
                'valid-ad-hoc', ?, ?, '{}', NULL, 'pending', '{}', '{}',
                'normal', 'active', 'none', '{}', '{}', '{}', '[]',
                'ad_hoc_device_action', 'direct-action-1', 'sha256:request-1'
            )
            """,
            (now, now),
        )
        direct_task = store.get_task("valid-ad-hoc")
        assert direct_task["workflow_uuid"] is None
        assert direct_task["execution_kind"] == "ad_hoc_device_action"
    finally:
        store._conn.rollback()
        store.close()


def test_v0_migration_is_serialized_for_concurrent_store_open(tmp_path):
    path = tmp_path / "workflow.db"
    _create_v0_database(path)
    barrier = threading.Barrier(2)
    versions: list[int] = []
    errors: list[BaseException] = []

    def open_store() -> None:
        barrier.wait()
        try:
            store = WorkflowStore(path)
            try:
                versions.append(
                    int(store._conn.execute("PRAGMA user_version").fetchone()[0])
                )
            finally:
                store.close()
        except BaseException as error:  # pragma: no cover - 断言会展示原异常
            errors.append(error)

    threads = [threading.Thread(target=open_store) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert versions == [WORKFLOW_SCHEMA_VERSION, WORKFLOW_SCHEMA_VERSION]
