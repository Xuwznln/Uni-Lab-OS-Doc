from __future__ import annotations

from unilabos.workflow.intervention import ActionInterventionRepository
from unilabos.workflow.store import WorkflowStore


def _required() -> dict:
    return {
        "decision_id": "decision-1",
        "job_id": "job-1",
        "device_uuid": "device-uuid-1",
        "device_id": "device-route-1",
        "host_uuid": "host-1",
        "authority_epoch": "epoch-1",
        "attempt_id": "attempt-1",
        "attempt_no": 1,
        "attempt_kind": "original",
        "options": [{"action": "retry"}, {"action": "abort"}],
        "created_at": "2026-08-15T10:00:00Z",
        "expires_at": "2099-08-15T10:05:00Z",
        "default_on_decision_timeout": "abort",
    }


def _command(command_id: str = "command-1", action: str = "retry") -> dict:
    return {
        "command_id": command_id,
        "decision_id": "decision-1",
        "job_id": "job-1",
        "device_uuid": "device-uuid-1",
        "device_id": "device-route-1",
        "host_uuid": "host-1",
        "authority_epoch": "epoch-1",
        "action": action,
        "reason": "operator selected",
    }


def test_required_command_and_resolved_are_durable_and_versioned(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.db")
    repository = ActionInterventionRepository(store)
    try:
        required, replayed = repository.record_required(_required())
        assert replayed is False
        assert required["state"] == "pending"
        assert required["aggregate_version"] == 1

        claim = repository.claim_command(_command(), trusted_actor="host-1")
        assert claim.claimed is True
        assert claim.status == "pending"
        assert claim.result["submitted_version"] == 2

        resolved, replayed = repository.record_resolved(
            {
                "decision_id": "decision-1",
                "selected_action": "retry",
                "reason": "operator selected",
                "resolved_at": "2026-08-15T10:01:00Z",
            },
            causation_kind="decision_command",
            causation_id="command-1",
        )
        assert replayed is False
        assert resolved["state"] == "resolved"
        assert resolved["aggregate_version"] == 3

        command_replay = repository.claim_command(
            _command(),
            trusted_actor="host-1",
        )
        assert command_replay.replayed is True
        assert command_replay.status == "completed"
        assert command_replay.result["resolved_version"] == 3

        events = store._conn.execute(
            """
            SELECT event_type, aggregate_version, payload
            FROM workflow_event_outbox
            ORDER BY global_sequence
            """
        ).fetchall()
        assert [(row["event_type"], row["aggregate_version"]) for row in events] == [
            ("decision_required", 1),
            ("decision_submitted", 2),
            ("decision_resolved", 3),
        ]
        assert all('"decision_id":"decision-1"' in row["payload"] for row in events)
    finally:
        store.close()


def test_command_id_conflict_does_not_replace_first_snapshot(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.db")
    repository = ActionInterventionRepository(store)
    try:
        repository.record_required(_required())
        first = repository.claim_command(_command(), trusted_actor="host-1")
        assert first.claimed is True

        conflict = repository.claim_command(
            _command(action="abort"),
            trusted_actor="host-1",
        )
        assert conflict.status == "rejected"
        assert conflict.error["code"] == "idempotency_conflict"

        replay = repository.claim_command(_command(), trusted_actor="host-1")
        assert replay.replayed is True
        assert replay.status == "pending"
        assert replay.result["submitted_version"] == 2
    finally:
        store.close()


def test_interlock_rejection_keeps_decision_pending_and_is_replayed(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.db")
    repository = ActionInterventionRepository(store)
    try:
        repository.record_required(_required())
        with store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO workflow_execution_hold(
                    hold_id, create_time, update_time, cause_id, scope_type,
                    scope_uuid, source, status, created_at
                ) VALUES (
                    'hold-1', 't1', 't1', 'incident-1', 'device',
                    'device-uuid-1', 'status-policy', 'active', 't1'
                )
                """
            )

        rejected = repository.claim_command(_command(), trusted_actor="host-1")
        assert rejected.status == "rejected"
        assert rejected.error["code"] == "interlock_active"
        assert repository.get("decision-1")["state"] == "pending"
        assert repository.get("decision-1")["aggregate_version"] == 2

        replay = repository.claim_command(_command(), trusted_actor="host-1")
        assert replay.replayed is True
        assert replay.error["code"] == "interlock_active"
    finally:
        store.close()


def test_job_status_projection_is_monotonic_and_terminal_does_not_regress(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.db")
    repository = ActionInterventionRepository(store)
    try:
        executing, replayed = repository.record_job_status(
            {
                "job_id": "job-1",
                "device_uuid": "device-uuid-1",
                "action_name": "run",
                "status": "executing",
            },
            causation_kind="dispatch",
            causation_id="dispatch-1",
        )
        assert replayed is False
        assert executing["status_version"] == 1

        success, replayed = repository.record_job_status(
            {
                "job_id": "job-1",
                "device_uuid": "device-uuid-1",
                "action_name": "run",
                "status": "success",
            },
            causation_kind="action_result",
            causation_id="result-1",
        )
        assert replayed is False
        assert success["status_version"] == 2
        assert success["terminal"] is True

        stale, replayed = repository.record_job_status(
            {
                "job_id": "job-1",
                "device_uuid": "device-uuid-1",
                "action_name": "run",
                "status": "executing",
            },
            causation_kind="dispatch",
            causation_id="late-dispatch",
        )
        assert replayed is True
        assert stale["status"] == "success"
        assert stale["status_version"] == 2
        assert store.count_rows("workflow_event_outbox") == 2
    finally:
        store.close()


def test_new_authority_epoch_supersedes_old_pending_and_rejects_claim(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.db")
    repository = ActionInterventionRepository(store)
    try:
        repository.record_required(_required())
        claim = repository.claim_command(_command(), trusted_actor="host-1")
        assert claim.claimed is True

        reconciled = repository.reconcile_authority(
            host_uuid="host-1",
            authority_epoch="epoch-2",
        )
        assert len(reconciled) == 1
        assert reconciled[0]["state"] == "superseded"
        assert reconciled[0]["resolution_reason"] == "host_restarted"
        assert reconciled[0]["aggregate_version"] == 3

        replay = repository.claim_command(_command(), trusted_actor="host-1")
        assert replay.replayed is True
        assert replay.status == "rejected"
        assert replay.error["code"] == "stale_authority"
    finally:
        store.close()
