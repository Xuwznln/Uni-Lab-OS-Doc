"""Action 报错介入的持久化聚合与 backend-neutral 协调器。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable, Mapping
from uuid import uuid4

from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.store import WorkflowStore, utc_now


_TERMINAL_STATES = {"resolved", "superseded", "expired", "canceled"}


def _json(value: Any) -> str:
    return encode_json(value, sort_keys=True).decode("utf-8")


def _load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return decode_json_bytes(value.encode("utf-8"))


def _fingerprint(value: Mapping[str, Any]) -> str:
    return sha256(encode_json(dict(value), sort_keys=True)).hexdigest()


def _rfc3339(value: Any) -> str:
    if isinstance(value, (int, float)):
        return (
            datetime.fromtimestamp(float(value), timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    if isinstance(value, str) and value:
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return utc_now()


def _has_expired(value: str) -> bool:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(candidate) <= datetime.now(timezone.utc)


class InterventionConflict(RuntimeError):
    """同一个领域身份被用于不同内容。"""

    def __init__(self, code: str, details: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class DecisionCommandResponse:
    command_id: str
    status: str
    replayed: bool
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    http_status: int
    claimed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "status": self.status,
            "replayed": self.replayed,
            "result": self.result,
            "error": self.error,
        }


class ActionInterventionRepository:
    """在 ``workflow_history.db`` 中维护 intervention/command/outbox。"""

    def __init__(self, store: WorkflowStore):
        self.store = store

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "decision_id": row["uuid"],
            "kind": row["kind"],
            "state": row["state"],
            "aggregate_version": int(row["aggregate_version"]),
            "job_id": row["logical_job_id"],
            "device_uuid": row["device_uuid"],
            "device_id": row["device_id_snapshot"],
            "host_uuid": row["host_uuid"],
            "authority_epoch": row["authority_epoch"],
            "attempt_id": row["attempt_id"],
            "attempt_no": int(row["attempt_no"]),
            "attempt_kind": row["attempt_kind"],
            "options": _load_json(row["options"], []),
            "expires_at": row["expires_at"],
            "selected_action": row["selected_action"],
            "resolution_reason": row["resolution_reason"],
            "resolved_at": row["resolved_at"],
            "job_status_version": int(row["job_status_version"]),
            "required": _load_json(row["required_payload"], {}),
            "resolved": _load_json(row["resolved_payload"], {}),
            "opened_at": row["opened_at"],
            "updated_at": row["update_time"],
        }

    @staticmethod
    def _command_response(
        row: sqlite3.Row,
        *,
        replayed: bool,
    ) -> DecisionCommandResponse:
        terminal_status = row["terminal_status"]
        status = "pending" if terminal_status == "pending" else terminal_status
        result = _load_json(row["result_snapshot"], {}) or None
        error = _load_json(row["error_snapshot"], {}) or None
        return DecisionCommandResponse(
            command_id=row["command_id"],
            status=status,
            replayed=replayed,
            result=result,
            error=error,
            http_status=int(row["http_status"]),
            claimed=False,
        )

    @staticmethod
    def _insert_outbox(
        conn: sqlite3.Connection,
        *,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        causation_kind: str,
        causation_id: str,
        correlation_id: str,
        payload: Mapping[str, Any],
        occurred_at: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO workflow_event_outbox(
                event_id, occurred_at, aggregate_type, aggregate_id,
                aggregate_version, event_type, causation_kind, causation_id,
                correlation_id, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                occurred_at or utc_now(),
                aggregate_type,
                aggregate_id,
                aggregate_version,
                event_type,
                causation_kind,
                causation_id,
                correlation_id,
                _json(dict(payload)),
            ),
        )

    def record_required(
        self,
        report: Mapping[str, Any],
        *,
        causation_kind: str = "attempt",
        causation_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        required = dict(report)
        decision_id = str(required.get("decision_id") or "")
        job_id = str(required.get("job_id") or "")
        device_uuid = str(
            required.get("device_uuid") or required.get("device_id") or ""
        )
        host_uuid = str(required.get("host_uuid") or "")
        authority_epoch = str(required.get("authority_epoch") or "")
        attempt_id = str(required.get("attempt_id") or "")
        if not all(
            (decision_id, job_id, device_uuid, host_uuid, authority_epoch, attempt_id)
        ):
            raise InterventionConflict(
                "invalid_required_identity",
                {
                    "decision_id": bool(decision_id),
                    "job_id": bool(job_id),
                    "device_uuid": bool(device_uuid),
                    "host_uuid": bool(host_uuid),
                    "authority_epoch": bool(authority_epoch),
                    "attempt_id": bool(attempt_id),
                },
            )
        options = list(required.get("options") or [])
        opened_at = _rfc3339(required.get("created_at"))
        expires_at = _rfc3339(required.get("expires_at"))
        attempt_no = int(required.get("attempt_no") or 1)
        attempt_kind = str(required.get("attempt_kind") or "original")
        required_fingerprint = _fingerprint(required)
        now = utc_now()
        with self.store.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (decision_id,),
            ).fetchone()
            if existing is not None:
                if existing["required_fingerprint"] != required_fingerprint:
                    raise InterventionConflict(
                        "intervention_identity_conflict",
                        {"decision_id": decision_id},
                    )
                return self._snapshot(existing), True

            conn.execute(
                """
                INSERT INTO workflow_intervention(
                    uuid, create_time, update_time, kind, state,
                    aggregate_version, logical_job_id, device_uuid,
                    device_id_snapshot, host_uuid, authority_epoch, attempt_id,
                    attempt_no, attempt_kind, required_payload,
                    required_fingerprint, options, expires_at, opened_at
                ) VALUES (?, ?, ?, 'action_error', 'pending', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    now,
                    now,
                    job_id,
                    device_uuid,
                    required.get("device_id"),
                    host_uuid,
                    authority_epoch,
                    attempt_id,
                    attempt_no,
                    attempt_kind,
                    _json(required),
                    required_fingerprint,
                    _json(options),
                    expires_at,
                    opened_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (decision_id,),
            ).fetchone()
            snapshot = self._snapshot(row)
            self._insert_outbox(
                conn,
                aggregate_type="intervention",
                aggregate_id=decision_id,
                aggregate_version=1,
                event_type="decision_required",
                causation_kind=causation_kind,
                causation_id=causation_id or attempt_id,
                correlation_id=job_id,
                payload=snapshot,
                occurred_at=opened_at,
            )
            return snapshot, False

    def record_resolved(
        self,
        report: Mapping[str, Any],
        *,
        causation_kind: str,
        causation_id: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        resolved = dict(report)
        decision_id = str(resolved.get("decision_id") or "")
        selected_action = str(resolved.get("selected_action") or "")
        reason = str(resolved.get("reason") or resolved.get("resolution_reason") or "")
        resolved_at = _rfc3339(resolved.get("resolved_at"))
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (decision_id,),
            ).fetchone()
            if row is None:
                return None, False
            if row["state"] in _TERMINAL_STATES:
                if (
                    row["selected_action"] != selected_action
                    or (row["resolution_reason"] or "") != reason
                ):
                    raise InterventionConflict(
                        "intervention_resolution_conflict",
                        {"decision_id": decision_id, "state": row["state"]},
                    )
                return self._snapshot(row), True

            state = "resolved"
            if reason in {"decision_timeout", "timeout"}:
                state = "expired"
            elif reason == "job_canceled":
                state = "canceled"
            version = int(row["aggregate_version"]) + 1
            job_status = conn.execute(
                """
                SELECT status_version FROM workflow_job_status_projection
                WHERE logical_job_id = ?
                """,
                (row["logical_job_id"],),
            ).fetchone()
            job_status_version = (
                int(job_status["status_version"]) if job_status is not None else 0
            )
            conn.execute(
                """
                UPDATE workflow_intervention
                SET state = ?, aggregate_version = ?, selected_action = ?,
                    resolution_reason = ?, resolved_payload = ?, resolved_at = ?,
                    job_status_version = ?, update_time = ?
                WHERE uuid = ?
                """,
                (
                    state,
                    version,
                    selected_action,
                    reason,
                    _json(resolved),
                    resolved_at,
                    job_status_version,
                    utc_now(),
                    decision_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (decision_id,),
            ).fetchone()
            snapshot = self._snapshot(updated)
            self._insert_outbox(
                conn,
                aggregate_type="intervention",
                aggregate_id=decision_id,
                aggregate_version=version,
                event_type="decision_resolved",
                causation_kind=causation_kind,
                causation_id=causation_id,
                correlation_id=row["logical_job_id"],
                payload=snapshot,
                occurred_at=resolved_at,
            )
            pending_command = conn.execute(
                """
                SELECT * FROM workflow_intervention_command
                WHERE decision_id = ? AND terminal_status = 'pending'
                ORDER BY create_time DESC, command_id DESC LIMIT 1
                """,
                (decision_id,),
            ).fetchone()
            if pending_command is not None:
                result = {
                    "decision": snapshot,
                    "accepted": True,
                    "resolved_version": version,
                }
                conn.execute(
                    """
                    UPDATE workflow_intervention_command
                    SET terminal_status = 'completed', http_status = 200,
                        result_snapshot = ?, error_snapshot = '{}',
                        resolved_version = ?, update_time = ?
                    WHERE command_id = ?
                    """,
                    (
                        _json(result),
                        version,
                        utc_now(),
                        pending_command["command_id"],
                    ),
                )
            return snapshot, False

    @staticmethod
    def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
        return {"code": code, "message": message, "details": details}

    def _insert_rejected_command(
        self,
        conn: sqlite3.Connection,
        *,
        command: Mapping[str, Any],
        trusted_actor: str,
        fingerprint: str,
        expected_version: int,
        code: str,
        message: str,
        details: Mapping[str, Any],
        http_status: int = 409,
    ) -> DecisionCommandResponse:
        now = utc_now()
        error = self._error(code, message, **dict(details))
        conn.execute(
            """
            INSERT INTO workflow_intervention_command(
                command_id, create_time, update_time, decision_id,
                trusted_actor, authority_epoch, request_fingerprint,
                request_payload, selected_action, reason,
                expected_intervention_version, terminal_status, http_status,
                error_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rejected', ?, ?)
            """,
            (
                command["command_id"],
                now,
                now,
                command["decision_id"],
                trusted_actor,
                command["authority_epoch"],
                fingerprint,
                _json(dict(command)),
                command["action"],
                command.get("reason"),
                expected_version,
                http_status,
                _json(error),
            ),
        )
        return DecisionCommandResponse(
            command_id=str(command["command_id"]),
            status="rejected",
            replayed=False,
            result=None,
            error=error,
            http_status=http_status,
        )

    def claim_command(
        self,
        command: Mapping[str, Any],
        *,
        trusted_actor: str,
    ) -> DecisionCommandResponse:
        selected_option = command.get("option")
        option_action = (
            selected_option.get("action")
            if isinstance(selected_option, Mapping)
            else None
        )
        normalized = {
            "command_id": str(command.get("command_id") or ""),
            "decision_id": str(command.get("decision_id") or ""),
            "job_id": str(command.get("job_id") or ""),
            "device_uuid": str(
                command.get("device_uuid") or command.get("device_id") or ""
            ),
            "device_id": command.get("device_id"),
            "host_uuid": str(command.get("host_uuid") or ""),
            "authority_epoch": str(command.get("authority_epoch") or ""),
            "action": str(command.get("action") or option_action or ""),
            "reason": command.get("reason"),
        }
        for optional_field in ("option", "result", "return_value"):
            if optional_field in command:
                normalized[optional_field] = command[optional_field]
        required = (
            "command_id",
            "decision_id",
            "job_id",
            "device_uuid",
            "host_uuid",
            "authority_epoch",
            "action",
        )
        missing = [name for name in required if not normalized[name]]
        if missing:
            raise InterventionConflict("invalid_decision_command", {"missing": missing})
        fingerprint = _fingerprint({"trusted_actor": trusted_actor, **normalized})
        with self.store.transaction() as conn:
            existing_command = conn.execute(
                """
                SELECT * FROM workflow_intervention_command
                WHERE command_id = ?
                """,
                (normalized["command_id"],),
            ).fetchone()
            if existing_command is not None:
                if (
                    existing_command["request_fingerprint"] != fingerprint
                    or existing_command["trusted_actor"] != trusted_actor
                ):
                    return DecisionCommandResponse(
                        command_id=normalized["command_id"],
                        status="rejected",
                        replayed=False,
                        result=None,
                        error=self._error(
                            "idempotency_conflict",
                            "command_id was already used with a different request",
                        ),
                        http_status=409,
                    )
                return self._command_response(existing_command, replayed=True)

            intervention = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (normalized["decision_id"],),
            ).fetchone()
            if intervention is None:
                return self._insert_rejected_command(
                    conn,
                    command=normalized,
                    trusted_actor=trusted_actor,
                    fingerprint=fingerprint,
                    expected_version=0,
                    code="decision_not_found",
                    message="error decision does not exist",
                    details={"decision_id": normalized["decision_id"]},
                    http_status=404,
                )

            version = int(intervention["aggregate_version"])
            identity_mismatch = {
                "job_id": normalized["job_id"] != intervention["logical_job_id"],
                "device_uuid": (
                    normalized["device_uuid"] != intervention["device_uuid"]
                ),
                "device_id": bool(normalized.get("device_id"))
                and normalized.get("device_id")
                != intervention["device_id_snapshot"],
                "host_uuid": normalized["host_uuid"] != intervention["host_uuid"],
                "authority_epoch": (
                    normalized["authority_epoch"] != intervention["authority_epoch"]
                ),
            }
            mismatched = [
                name for name, mismatch in identity_mismatch.items() if mismatch
            ]
            if mismatched:
                return self._insert_rejected_command(
                    conn,
                    command=normalized,
                    trusted_actor=trusted_actor,
                    fingerprint=fingerprint,
                    expected_version=version,
                    code="decision_identity_mismatch",
                    message="decision command identity does not match pending decision",
                    details={"fields": mismatched},
                )
            if intervention["state"] != "pending":
                return self._insert_rejected_command(
                    conn,
                    command=normalized,
                    trusted_actor=trusted_actor,
                    fingerprint=fingerprint,
                    expected_version=version,
                    code="decision_not_pending",
                    message="error decision is no longer pending",
                    details={"state": intervention["state"]},
                )
            if _has_expired(intervention["expires_at"]):
                return self._insert_rejected_command(
                    conn,
                    command=normalized,
                    trusted_actor=trusted_actor,
                    fingerprint=fingerprint,
                    expected_version=version,
                    code="decision_expired",
                    message="error decision has expired",
                    details={"expires_at": intervention["expires_at"]},
                )
            options = _load_json(intervention["options"], [])
            allowed_actions = {
                str(option.get("action") or option.get("id"))
                if isinstance(option, dict)
                else str(option)
                for option in options
            }
            if normalized["action"] not in allowed_actions:
                return self._insert_rejected_command(
                    conn,
                    command=normalized,
                    trusted_actor=trusted_actor,
                    fingerprint=fingerprint,
                    expected_version=version,
                    code="invalid_decision_action",
                    message="selected action is not offered by the pending decision",
                    details={"allowed_actions": sorted(allowed_actions)},
                    http_status=422,
                )

            active_hold = conn.execute(
                """
                SELECT hold_id, cause_id, scope_type, scope_uuid
                FROM workflow_execution_hold
                WHERE status = 'active' AND (
                    (scope_type = 'device' AND scope_uuid = ?)
                    OR (scope_type = 'job' AND scope_uuid = ?)
                    OR (scope_type = 'action' AND scope_uuid = ?)
                )
                ORDER BY created_at, hold_id LIMIT 1
                """,
                (
                    intervention["device_uuid"],
                    intervention["logical_job_id"],
                    normalized["action"],
                ),
            ).fetchone()
            if active_hold is not None and normalized["action"] in {
                "retry",
                "fallback",
            }:
                rejected_version = version + 1
                conn.execute(
                    """
                    UPDATE workflow_intervention
                    SET aggregate_version = ?, update_time = ?
                    WHERE uuid = ? AND state = 'pending' AND aggregate_version = ?
                    """,
                    (rejected_version, utc_now(), intervention["uuid"], version),
                )
                updated = conn.execute(
                    "SELECT * FROM workflow_intervention WHERE uuid = ?",
                    (intervention["uuid"],),
                ).fetchone()
                snapshot = self._snapshot(updated)
                self._insert_outbox(
                    conn,
                    aggregate_type="intervention",
                    aggregate_id=intervention["uuid"],
                    aggregate_version=rejected_version,
                    event_type="decision_rejected",
                    causation_kind="decision_command",
                    causation_id=normalized["command_id"],
                    correlation_id=intervention["logical_job_id"],
                    payload=snapshot,
                )
                return self._insert_rejected_command(
                    conn,
                    command=normalized,
                    trusted_actor=trusted_actor,
                    fingerprint=fingerprint,
                    expected_version=version,
                    code="interlock_active",
                    message="an active execution hold blocks retry or fallback",
                    details={
                        "hold_id": active_hold["hold_id"],
                        "cause_id": active_hold["cause_id"],
                        "intervention_version": rejected_version,
                    },
                )

            submitted_version = version + 1
            cursor = conn.execute(
                """
                UPDATE workflow_intervention
                SET state = 'decision_submitted', aggregate_version = ?,
                    update_time = ?
                WHERE uuid = ? AND state = 'pending' AND aggregate_version = ?
                """,
                (submitted_version, utc_now(), intervention["uuid"], version),
            )
            if cursor.rowcount != 1:
                observed = conn.execute(
                    "SELECT state, aggregate_version FROM workflow_intervention WHERE uuid = ?",
                    (intervention["uuid"],),
                ).fetchone()
                code = (
                    "intervention_version_conflict"
                    if observed is not None and observed["state"] == "pending"
                    else "decision_not_pending"
                )
                return self._insert_rejected_command(
                    conn,
                    command=normalized,
                    trusted_actor=trusted_actor,
                    fingerprint=fingerprint,
                    expected_version=version,
                    code=code,
                    message="the pending intervention changed before command claim",
                    details={
                        "state": observed["state"] if observed else None,
                        "observed_version": (
                            int(observed["aggregate_version"]) if observed else None
                        ),
                    },
                )

            now = utc_now()
            conn.execute(
                """
                INSERT INTO workflow_intervention_command(
                    command_id, create_time, update_time, decision_id,
                    trusted_actor, authority_epoch, request_fingerprint,
                    request_payload, selected_action, reason,
                    expected_intervention_version, terminal_status, http_status,
                    result_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 202, ?)
                """,
                (
                    normalized["command_id"],
                    now,
                    now,
                    normalized["decision_id"],
                    trusted_actor,
                    normalized["authority_epoch"],
                    fingerprint,
                    _json(normalized),
                    normalized["action"],
                    normalized.get("reason"),
                    version,
                    _json(
                        {
                            "accepted": True,
                            "decision_id": normalized["decision_id"],
                            "submitted_version": submitted_version,
                        }
                    ),
                ),
            )
            updated = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (intervention["uuid"],),
            ).fetchone()
            snapshot = self._snapshot(updated)
            self._insert_outbox(
                conn,
                aggregate_type="intervention",
                aggregate_id=intervention["uuid"],
                aggregate_version=submitted_version,
                event_type="decision_submitted",
                causation_kind="decision_command",
                causation_id=normalized["command_id"],
                correlation_id=intervention["logical_job_id"],
                payload=snapshot,
            )
            return DecisionCommandResponse(
                command_id=normalized["command_id"],
                status="pending",
                replayed=False,
                result={
                    "accepted": True,
                    "decision": snapshot,
                    "submitted_version": submitted_version,
                },
                error=None,
                http_status=202,
                claimed=True,
            )

    def complete_command(self, command_id: str) -> DecisionCommandResponse:
        with self.store.transaction() as conn:
            command = conn.execute(
                "SELECT * FROM workflow_intervention_command WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if command is None:
                raise InterventionConflict("decision_command_not_found")
            if command["terminal_status"] != "pending":
                return self._command_response(command, replayed=False)
            intervention = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (command["decision_id"],),
            ).fetchone()
            if intervention is None or intervention["state"] not in _TERMINAL_STATES:
                return self._command_response(command, replayed=False)
            snapshot = self._snapshot(intervention)
            result = {
                "accepted": True,
                "decision": snapshot,
                "resolved_version": int(intervention["aggregate_version"]),
            }
            conn.execute(
                """
                UPDATE workflow_intervention_command
                SET terminal_status = 'completed', http_status = 200,
                    result_snapshot = ?, error_snapshot = '{}',
                    resolved_version = ?, update_time = ?
                WHERE command_id = ?
                """,
                (
                    _json(result),
                    int(intervention["aggregate_version"]),
                    utc_now(),
                    command_id,
                ),
            )
            return DecisionCommandResponse(
                command_id=command_id,
                status="completed",
                replayed=False,
                result=result,
                error=None,
                http_status=200,
            )

    def reject_claimed_command(
        self,
        command_id: str,
        *,
        code: str = "coordinator_rejected",
    ) -> DecisionCommandResponse:
        with self.store.transaction() as conn:
            command = conn.execute(
                "SELECT * FROM workflow_intervention_command WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if command is None:
                raise InterventionConflict("decision_command_not_found")
            if command["terminal_status"] != "pending":
                return self._command_response(command, replayed=False)
            intervention = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (command["decision_id"],),
            ).fetchone()
            version = int(intervention["aggregate_version"]) + 1
            conn.execute(
                """
                UPDATE workflow_intervention
                SET state = 'pending', aggregate_version = ?, update_time = ?
                WHERE uuid = ? AND state = 'decision_submitted'
                """,
                (version, utc_now(), intervention["uuid"]),
            )
            error = self._error(
                code,
                "execution coordinator did not accept the decision",
                intervention_version=version,
            )
            conn.execute(
                """
                UPDATE workflow_intervention_command
                SET terminal_status = 'rejected', http_status = 409,
                    result_snapshot = '{}', error_snapshot = ?, update_time = ?
                WHERE command_id = ?
                """,
                (_json(error), utc_now(), command_id),
            )
            updated = conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (intervention["uuid"],),
            ).fetchone()
            self._insert_outbox(
                conn,
                aggregate_type="intervention",
                aggregate_id=intervention["uuid"],
                aggregate_version=version,
                event_type="decision_rejected",
                causation_kind="decision_command",
                causation_id=command_id,
                correlation_id=intervention["logical_job_id"],
                payload=self._snapshot(updated),
            )
            return DecisionCommandResponse(
                command_id=command_id,
                status="rejected",
                replayed=False,
                result=None,
                error=error,
                http_status=409,
            )

    def record_job_status(
        self,
        payload: Mapping[str, Any],
        *,
        causation_kind: str,
        causation_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """按 logical job 保存单调 status_version 和同事务 outbox。"""

        data = dict(payload)
        job_id = str(data.get("job_id") or "")
        device_uuid = str(data.get("device_uuid") or data.get("device_id") or "")
        action_name = str(data.get("action_name") or data.get("action") or "")
        status = str(data.get("status") or "unknown")
        if not job_id or not device_uuid:
            raise InterventionConflict(
                "invalid_job_status_identity",
                {"job_id": bool(job_id), "device_uuid": bool(device_uuid)},
            )
        if status not in {
            "queued",
            "executing",
            "success",
            "failed",
            "canceled",
            "unknown",
        }:
            raise InterventionConflict("invalid_job_status", {"status": status})
        terminal = status in {"success", "failed", "canceled", "unknown"}
        now = utc_now()
        with self.store.transaction() as conn:
            existing = conn.execute(
                """
                SELECT * FROM workflow_job_status_projection
                WHERE logical_job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if existing is not None and int(existing["terminal"]):
                snapshot = {
                    **_load_json(existing["payload"], {}),
                    "status": existing["status"],
                    "status_version": int(existing["status_version"]),
                    "terminal": True,
                }
                return snapshot, True
            version = 1 if existing is None else int(existing["status_version"]) + 1
            snapshot = {
                **data,
                "job_id": job_id,
                "device_uuid": device_uuid,
                "action_name": action_name,
                "status": status,
                "status_version": version,
                "terminal": terminal,
                "updated_at": now,
            }
            conn.execute(
                """
                INSERT INTO workflow_job_status_projection(
                    logical_job_id, create_time, update_time, device_uuid,
                    action_name, status, status_version, terminal, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(logical_job_id) DO UPDATE SET
                    update_time = excluded.update_time,
                    device_uuid = excluded.device_uuid,
                    action_name = excluded.action_name,
                    status = excluded.status,
                    status_version = excluded.status_version,
                    terminal = excluded.terminal,
                    payload = excluded.payload
                """,
                (
                    job_id,
                    now,
                    now,
                    device_uuid,
                    action_name,
                    status,
                    version,
                    int(terminal),
                    _json(snapshot),
                ),
            )
            self._insert_outbox(
                conn,
                aggregate_type="job",
                aggregate_id=job_id,
                aggregate_version=version,
                event_type="job_status",
                causation_kind=causation_kind,
                causation_id=causation_id,
                correlation_id=job_id,
                payload=snapshot,
            )
            return snapshot, False

    def reconcile_authority(
        self,
        *,
        host_uuid: str,
        authority_epoch: str,
    ) -> list[dict[str, Any]]:
        """新 Host epoch 启动时原子收口旧 epoch 的未决介入。"""

        if not host_uuid or not authority_epoch:
            raise InterventionConflict("invalid_authority_identity")
        reconciliation_id = str(uuid4())
        resolved_at = utc_now()
        reconciled: list[dict[str, Any]] = []
        with self.store.transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workflow_intervention
                WHERE host_uuid = ?
                  AND authority_epoch <> ?
                  AND state IN ('pending', 'decision_submitted')
                ORDER BY opened_at, uuid
                """,
                (host_uuid, authority_epoch),
            ).fetchall()
            for row in rows:
                version = int(row["aggregate_version"]) + 1
                resolution = {
                    "decision_id": row["uuid"],
                    "job_id": row["logical_job_id"],
                    "device_uuid": row["device_uuid"],
                    "selected_action": "abort",
                    "reason": "host_restarted",
                    "resolved_at": resolved_at,
                    "previous_authority_epoch": row["authority_epoch"],
                    "authority_epoch": authority_epoch,
                }
                conn.execute(
                    """
                    UPDATE workflow_intervention
                    SET state = 'superseded', aggregate_version = ?,
                        selected_action = 'abort',
                        resolution_reason = 'host_restarted',
                        resolved_payload = ?, resolved_at = ?, update_time = ?
                    WHERE uuid = ?
                    """,
                    (
                        version,
                        _json(resolution),
                        resolved_at,
                        resolved_at,
                        row["uuid"],
                    ),
                )
                error = self._error(
                    "stale_authority",
                    "Host authority changed before the decision completed",
                    previous_authority_epoch=row["authority_epoch"],
                    authority_epoch=authority_epoch,
                )
                conn.execute(
                    """
                    UPDATE workflow_intervention_command
                    SET terminal_status = 'rejected', http_status = 409,
                        result_snapshot = '{}', error_snapshot = ?,
                        resolved_version = ?, update_time = ?
                    WHERE decision_id = ? AND terminal_status = 'pending'
                    """,
                    (
                        _json(error),
                        version,
                        resolved_at,
                        row["uuid"],
                    ),
                )
                updated = conn.execute(
                    "SELECT * FROM workflow_intervention WHERE uuid = ?",
                    (row["uuid"],),
                ).fetchone()
                snapshot = self._snapshot(updated)
                self._insert_outbox(
                    conn,
                    aggregate_type="intervention",
                    aggregate_id=row["uuid"],
                    aggregate_version=version,
                    event_type="decision_resolved",
                    causation_kind="reconciliation",
                    causation_id=reconciliation_id,
                    correlation_id=row["logical_job_id"],
                    payload=snapshot,
                    occurred_at=resolved_at,
                )
                reconciled.append(snapshot)
        return reconciled

    def get(self, decision_id: str) -> dict[str, Any] | None:
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT * FROM workflow_intervention WHERE uuid = ?",
                (decision_id,),
            ).fetchone()
        return self._snapshot(row) if row is not None else None

    def list_pending(self) -> list[dict[str, Any]]:
        with self.store._lock:
            rows = self.store._conn.execute(
                """
                SELECT * FROM workflow_intervention
                WHERE deleted_at IS NULL AND state IN ('pending', 'decision_submitted')
                ORDER BY opened_at, uuid
                """
            ).fetchall()
        return [self._snapshot(row) for row in rows]


class ActionExecutionCoordinator:
    """把后端命令幂等性与具体 ROS/HostLink 执行 Adapter 隔离。"""

    def __init__(
        self,
        repository: ActionInterventionRepository,
        decision_resolver: Callable[[str, str, str, dict[str, Any]], bool],
    ):
        self.repository = repository
        self._decision_resolver = decision_resolver

    def publish_action_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type == "job_error_decision_required":
            self.repository.record_required(payload)
        elif event_type == "job_error_decision_resolved":
            reason = str(payload.get("reason") or "coordinator")
            causation_kind = "decision_command"
            causation_id = str(payload.get("command_id") or "")
            if not causation_id:
                causation_kind = "resolution"
                causation_id = str(payload.get("event_id") or uuid4())
            self.repository.record_resolved(
                payload,
                causation_kind=causation_kind,
                causation_id=causation_id or reason,
            )

    def submit_decision(
        self,
        command: Mapping[str, Any],
        *,
        trusted_actor: str,
    ) -> DecisionCommandResponse:
        claim = self.repository.claim_command(command, trusted_actor=trusted_actor)
        if not claim.claimed:
            return claim
        accepted = self._decision_resolver(
            str(command["decision_id"]),
            str(command["job_id"]),
            str(command.get("device_id") or command.get("device_uuid") or ""),
            dict(command),
        )
        if not accepted:
            completed = self.repository.complete_command(str(command["command_id"]))
            if completed.status == "completed":
                return completed
            return self.repository.reject_claimed_command(str(command["command_id"]))
        return self.repository.complete_command(str(command["command_id"]))


__all__ = [
    "ActionExecutionCoordinator",
    "ActionInterventionRepository",
    "DecisionCommandResponse",
    "InterventionConflict",
]
