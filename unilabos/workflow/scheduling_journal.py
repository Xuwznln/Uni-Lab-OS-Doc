"""平台持有的调度决策账；与 Task/Job 共用 WorkflowStore 的事务权威。"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from unilabos.scheduling.protocol import (
    MAX_COUNTER,
    EngineError,
    counter,
    decode_message,
    encode_message,
    require,
    validate_value,
)
from unilabos.scheduling.validation import validate_decision
from unilabos.workflow.scheduling_observation import (
    ensure_observation_schema,
    import_legacy_observations,
    publish_observations,
)
from unilabos.workflow.store import WorkflowStore

_REJECTION = (
    "json_extract(event_json,'$.payload.kind')='decision_rejected' OR "
    "(json_extract(event_json,'$.payload.kind')='intent_result' "
    "AND json_extract(event_json,'$.payload.disposition')='rejected')"
)

_SCHEMA = (
    """CREATE INDEX IF NOT EXISTS ix_scheduler_unsettled_tasks ON workflow_task(create_time,uuid)
        WHERE deleted_at IS NULL AND (
            status NOT IN ('succeeded','failed','canceled','timeout')
            OR cleanup_status NOT IN ('none','settled'))""",
    """CREATE INDEX IF NOT EXISTS ix_scheduler_unsettled_jobs ON workflow_node_job(workflow_task_uuid)
        WHERE deleted_at IS NULL AND (
            status IN ('dispatched','running','cancel_requested','intervention_required')
            OR COALESCE(uncertainty_reason,'') != '')""",
    """CREATE INDEX IF NOT EXISTS ix_scheduler_unsettled_claims ON execution_claim(workflow_task_uuid)
        WHERE state != 'released'""",
    """CREATE TABLE IF NOT EXISTS scheduler_engine_domain (
        domain_id TEXT PRIMARY KEY, engine_id TEXT NOT NULL,
        config_id TEXT NOT NULL, config_json TEXT NOT NULL,
        instance_id TEXT NOT NULL, run_epoch TEXT NOT NULL,
        lifecycle TEXT NOT NULL, checkpoint_json TEXT NOT NULL, head_json TEXT,
        feedback_seq TEXT NOT NULL DEFAULT '0', snapshot_revision TEXT NOT NULL DEFAULT '0',
        snapshot_json TEXT, queue_seq TEXT NOT NULL DEFAULT '0',
        source_cursor INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS scheduler_engine_feedback (
        domain_id TEXT NOT NULL, seq TEXT NOT NULL, event_id TEXT NOT NULL,
        digest TEXT NOT NULL, event_json TEXT NOT NULL,
        PRIMARY KEY (domain_id, seq), UNIQUE(domain_id,event_id)
    )""",
    f"""CREATE INDEX IF NOT EXISTS ix_scheduler_feedback_rejection
        ON scheduler_engine_feedback(domain_id) WHERE {_REJECTION}""",
    """CREATE TABLE IF NOT EXISTS scheduler_engine_decision (
        domain_id TEXT NOT NULL, request_id TEXT NOT NULL,
        request_hash TEXT NOT NULL, response_json TEXT NOT NULL,
        receipt_json TEXT NOT NULL, accepted_at_ms INTEGER NOT NULL,
        PRIMARY KEY (domain_id,request_id)
    )""",
    """CREATE INDEX IF NOT EXISTS ix_scheduler_decision_domain
        ON scheduler_engine_decision(domain_id)""",
    """CREATE TABLE IF NOT EXISTS scheduler_engine_intent (
        domain_id TEXT NOT NULL, decision_id TEXT NOT NULL, intent_id TEXT PRIMARY KEY,
        run_epoch TEXT NOT NULL, status TEXT NOT NULL, intent_json TEXT NOT NULL,
        attempt_id TEXT, command_ids_json TEXT NOT NULL DEFAULT '[]'
    )""",
    """CREATE TABLE IF NOT EXISTS scheduler_engine_queue (
        domain_id TEXT NOT NULL, task_id TEXT NOT NULL, queue_seq TEXT NOT NULL,
        PRIMARY KEY (domain_id,task_id), UNIQUE(domain_id,queue_seq)
    )""",
    """CREATE TABLE IF NOT EXISTS scheduler_engine_parameters (
        domain_id TEXT NOT NULL, task_id TEXT NOT NULL, node_id TEXT NOT NULL,
        revision TEXT NOT NULL, digest TEXT NOT NULL,
        PRIMARY KEY (domain_id,task_id,node_id)
    )""",
)


def _json(value: Any) -> str:
    return encode_message(value).decode("utf-8")


def _decode(value: str | None) -> Any:
    return decode_message(value.encode()) if value is not None else None


def _next(value: str) -> str:
    current = counter(value)
    require(current < MAX_COUNTER, "PERSISTENCE_FAILED", "持久计数器已耗尽")
    return str(current + 1)


def _sources_pending(connection: sqlite3.Connection, *, domain_id: str, runtime_cursor: int) -> bool:
    """恢复闸门与查询共用全部源游标，避免遗漏独立提交的设备回执。"""
    return bool(connection.execute(
        "SELECT EXISTS(SELECT 1 FROM workflow_runtime_journal WHERE sequence>?) "
        "OR EXISTS(SELECT 1 FROM scheduler_engine_observation WHERE sequence>COALESCE("
        "(SELECT sequence FROM scheduler_engine_source_cursor "
        "WHERE domain_id=? AND source='observations'),0)) "
        "OR EXISTS(SELECT 1 FROM workflow_execution_started WHERE sequence>COALESCE("
        "(SELECT sequence FROM scheduler_engine_source_cursor "
        "WHERE domain_id=? AND source='execution_started'),0))",
        (runtime_cursor, domain_id, domain_id),
    ).fetchone()[0])


class SchedulingJournal:
    """只管理调度选择与反馈；不创建、释放或推断物理资源。"""

    def __init__(self, store: WorkflowStore, *, monotonic=time.monotonic) -> None:
        self.store = store
        self._monotonic = monotonic
        self._deadlines: dict[tuple[str, str], float] = {}
        with store.transaction() as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
            ensure_observation_schema(connection)
            from unilabos.workflow.execution_started import ensure_execution_started_schema
            ensure_execution_started_schema(connection)

    @staticmethod
    def _row(connection, domain_id):
        row = connection.execute(
            "SELECT * FROM scheduler_engine_domain WHERE domain_id=?", (domain_id,),
        ).fetchone()
        require(row is not None, "INSTANCE_NOT_FOUND", "调度域尚未初始化")
        return row

    @staticmethod
    def _view(row):
        return {
            "domain_id": row["domain_id"], "engine_id": row["engine_id"],
            "config_id": row["config_id"], "instance_id": row["instance_id"],
            "run_epoch": row["run_epoch"], "lifecycle": row["lifecycle"],
            "checkpoint": _decode(row["checkpoint_json"]), "head": _decode(row["head_json"]),
            "feedback_watermark": row["feedback_seq"], "snapshot_revision": row["snapshot_revision"],
        }

    def read(self, domain_id: str) -> dict[str, Any]:
        with self.store.read() as connection:
            return self._view(self._row(connection, domain_id))

    def status(self, domain_id: str) -> dict[str, Any]:
        """只读最近已提交选择；不暴露算法检查点，也不靠墙钟恢复旧授权。"""
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT domain_id,run_epoch,lifecycle,head_json,source_cursor,snapshot_revision,"
                "snapshot_json IS NOT NULL AS has_snapshot FROM scheduler_engine_domain WHERE domain_id=?",
                (domain_id,),
            ).fetchone()
            require(row is not None, "INSTANCE_NOT_FOUND", "调度域尚未初始化")
            head = _decode(row["head_json"])
            if head is not None:
                head["authorization_valid"] = self._authorized(row, head)
            latest = connection.execute(
                "SELECT accepted_at_ms,json_remove(json_extract(response_json,'$.result'),"
                "'$.next_state','$.forecast','$.dispatch_now','$.extensions') AS summary "
                "FROM scheduler_engine_decision WHERE domain_id=? ORDER BY rowid DESC LIMIT 1",
                (domain_id,),
            ).fetchone()
            decision = None
            if latest is not None:
                decision = _decode(latest["summary"])
                pending = _sources_pending(connection, domain_id=domain_id, runtime_cursor=row["source_cursor"])
                decision["accepted_at_ms"] = latest["accepted_at_ms"]
                decision["current"] = bool(
                    not pending and row["has_snapshot"] and row["lifecycle"] == "running"
                    and decision["run_epoch"] == row["run_epoch"]
                    and decision["base_snapshot_revision"] == row["snapshot_revision"]
                )
            rejected = connection.execute(
                "SELECT event_json FROM scheduler_engine_feedback "
                f"WHERE domain_id=? AND ({_REJECTION}) ORDER BY rowid DESC LIMIT 1", (domain_id,),
            ).fetchone()
            return {
                "lifecycle": row["lifecycle"], "head": head, "latest_decision": decision,
                "latest_rejection": _decode(rejected["event_json"]) if rejected is not None else None,
            }

    def start(self, creation: dict[str, Any], *, engine_id: str, rebuild: bool = False) -> dict[str, Any]:
        """建立新世代并关闭普通推进；旧选择保留，未准入候选只能重新计算。"""
        validate_value(creation, "CreateInput")
        domain_id = creation["domain_id"]
        with self.store.transaction() as connection:
            found = connection.execute(
                "SELECT * FROM scheduler_engine_domain WHERE domain_id=?", (domain_id,),
            ).fetchone()
            checkpoint = None
            model_reset = False
            if found is not None:
                try:
                    checkpoint = _decode(found["checkpoint_json"])
                    validate_value(checkpoint, "Checkpoint", code="STATE_CORRUPT")
                except EngineError as error:
                    if not (rebuild and creation["restore"]["allow_model_reset"]):
                        raise EngineError("STATE_CORRUPT", "持久检查点损坏，须显式允许重建模型") from error
                    # 无法读取的状态不能推断为无模型，仍按已丢弃模型审计。
                    checkpoint = None
                    model_reset = True
                require(
                    found["config_id"] != creation["config_id"] or found["config_json"] == _json(creation["config"]),
                    "IDEMPOTENCY_CONFLICT", "同一 config_id 对应不同配置",
                )
                compatible = found["engine_id"] == engine_id and found["config_id"] == creation["config_id"]
                require(compatible or rebuild, "STATE_INCOMPATIBLE", "引擎或配置变化需要显式 rebuild")
                if rebuild:
                    model_reset = checkpoint is None or checkpoint["state"] is not None
                    require(
                        not model_reset or creation["restore"]["allow_model_reset"],
                        "STATE_INCOMPATIBLE", "丢弃已有模型需要 allow_model_reset",
                    )
                if found["run_epoch"] == creation["run_epoch"]:
                    require(
                        found["instance_id"] == creation["instance_id"] and compatible and not rebuild,
                        "IDEMPOTENCY_CONFLICT", "运行世代已绑定不同实例",
                    )
                    return {**self._view(found), "restore_mode": "resume", "model_reset": False}
            if found is None or rebuild:
                checkpoint = {
                    "revision": "0", "feedback_cursor": found["feedback_seq"] if found else "0",
                    "engine_id": engine_id, "config_id": creation["config_id"], "state": None,
                }
            if found is None:
                connection.execute(
                    "INSERT INTO scheduler_engine_domain "
                    "(domain_id,engine_id,config_id,config_json,instance_id,run_epoch,lifecycle,checkpoint_json) "
                    "VALUES (?,?,?,?,?,?,'recovering',?)",
                    (domain_id, engine_id, creation["config_id"], _json(creation["config"]),
                     creation["instance_id"], creation["run_epoch"], _json(checkpoint)),
                )
            else:
                head = _decode(found["head_json"])
                if head is not None:
                    head["authorization_valid"] = False
                connection.execute(
                    "UPDATE scheduler_engine_domain SET engine_id=?, config_id=?,config_json=?,instance_id=?, "
                    "run_epoch=?,lifecycle='recovering',checkpoint_json=?,head_json=?,snapshot_json=NULL WHERE domain_id=?",
                    (engine_id, creation["config_id"], _json(creation["config"]), creation["instance_id"],
                     creation["run_epoch"], _json(checkpoint), _json(head) if head else None, domain_id),
                )
                connection.execute(
                    "UPDATE scheduler_engine_intent SET status='recovery_replan' WHERE domain_id=? AND status='pending'",
                    (domain_id,),
                )
            now = round(time.time() * 1000)
            skipped_history = import_legacy_observations(
                connection, domain_id, occurred_at_ms=now, rebuild=rebuild,
                allow_model_reset=creation["restore"]["allow_model_reset"],
            )
            model_reset = model_reset or bool(skipped_history)
            if (found is not None and rebuild) or skipped_history:
                # 新基线与重建事件、原始样本回放共用事务，任何失败均保留旧世代。
                self.append_feedback(
                    connection, domain_id,
                    event_id=str(uuid5(NAMESPACE_URL, f"unilab.scheduler.rebuild:{domain_id}:{creation['run_epoch']}")),
                    occurred_at_ms=now,
                    payload={"kind": "snapshot_resync", "model_reset": model_reset, "reason": {
                        "code": "MODEL_RESET" if model_reset else "RECOVERY_REQUIRED",
                        "message": (
                            "显式重建引擎，平台真实执行与占用保持不变"
                            + (f"；已按许可忽略 {skipped_history} 条不能验证的旧计时证据" if skipped_history else "")
                        ),
                        "task_id": None, "node": None, "resource_ids": [],
                    }},
                )
            publish_observations(
                connection, domain_id, self.append_feedback,
                replay_id=creation["run_epoch"] if found is not None and rebuild else None,
            )
            return {
                **self._view(self._row(connection, domain_id)),
                "restore_mode": "rebuild" if found is None or rebuild else "resume",
                "model_reset": model_reset,
            }

    def set_lifecycle(self, domain_id: str, run_epoch: str, lifecycle: str) -> None:
        require(lifecycle in {"running", "recovering", "draining", "engine_fault"}, "INVALID_REQUEST", "调度生命周期无效")
        with self.store.transaction() as connection:
            row = self._row(connection, domain_id)
            require(row["run_epoch"] == run_epoch, "STALE_DECISION", "调度世代已变更")
            if lifecycle == "running":
                # 源日志尚未投影时，反馈账水位追平不代表恢复已完成。
                pending = _sources_pending(connection, domain_id=domain_id, runtime_cursor=row["source_cursor"])
                require(not pending, "FEEDBACK_GAP", "源事实尚未投影为反馈，不能解除恢复屏障")
                require(
                    _decode(row["checkpoint_json"])["feedback_cursor"] == row["feedback_seq"],
                    "FEEDBACK_GAP", "反馈尚未追平，不能解除恢复屏障",
                )
            head = _decode(row["head_json"])
            if head is not None:
                head["authorization_valid"] = False
            connection.execute(
                "UPDATE scheduler_engine_domain SET lifecycle=?,head_json=?,snapshot_json=NULL WHERE domain_id=?",
                (lifecycle, _json(head) if head else None, domain_id),
            )

    def queue_sequence(self, domain_id: str, task_id: str) -> str:
        validate_value(task_id, "Id")
        with self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT queue_seq FROM scheduler_engine_queue WHERE domain_id=? AND task_id=?",
                (domain_id, task_id),
            ).fetchone()
            if existing:
                return existing["queue_seq"]
            sequence = _next(self._row(connection, domain_id)["queue_seq"])
            connection.execute(
                "INSERT INTO scheduler_engine_queue(domain_id,task_id,queue_seq) VALUES (?,?,?)",
                (domain_id, task_id, sequence),
            )
            connection.execute(
                "UPDATE scheduler_engine_domain SET queue_seq=? WHERE domain_id=?", (sequence, domain_id),
            )
            return sequence

    def publish_snapshot(self, domain_id: str, run_epoch: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        """平台在执行锁内调用，固定本轮事实、游标和控制状态的同一切面。"""
        snapshot = deepcopy(snapshot)
        with self.store.transaction() as connection:
            row = self._row(connection, domain_id)
            require(row["run_epoch"] == run_epoch, "STALE_DECISION", "调度世代已变更")
            head = _decode(row["head_json"])
            if head is not None and not self._authorized(row, head):
                head["authorization_valid"] = False
            snapshot.update(
                revision=_next(row["snapshot_revision"]), feedback_watermark=row["feedback_seq"],
                lifecycle=row["lifecycle"], execution_gate="open" if row["lifecycle"] == "running" else "held",
                head=head,
            )
            validate_value(snapshot, "Snapshot", code="SNAPSHOT_INVALID")
            connection.execute(
                "UPDATE scheduler_engine_domain SET snapshot_revision=?,snapshot_json=?,head_json=? WHERE domain_id=?",
                (snapshot["revision"], _json(snapshot), _json(head) if head else None, domain_id),
            )
        return snapshot

    def parameters_revision(self, domain_id: str, node: dict[str, str], value: Any) -> str:
        """参数事实变化时推进持久修订；恢复和重复读取不会重新编号。"""
        digest = hashlib.sha256(encode_message(value)).hexdigest()
        with self.store.transaction() as connection:
            self._row(connection, domain_id)
            row = connection.execute(
                "SELECT revision,digest FROM scheduler_engine_parameters WHERE domain_id=? AND task_id=? AND node_id=?",
                (domain_id, node["task_id"], node["node_id"]),
            ).fetchone()
            if row and row["digest"] == digest:
                return row["revision"]
            revision = _next(row["revision"]) if row else "1"
            connection.execute(
                "INSERT INTO scheduler_engine_parameters(domain_id,task_id,node_id,revision,digest) "
                "VALUES (?,?,?,?,?) ON CONFLICT(domain_id,task_id,node_id) "
                "DO UPDATE SET revision=excluded.revision,digest=excluded.digest",
                (domain_id, node["task_id"], node["node_id"], revision, digest),
            )
            return revision

    def append_feedback(
        self, connection: sqlite3.Connection, domain_id: str, *,
        event_id: str, occurred_at_ms: int, payload: dict[str, Any], invalidate_snapshot: bool = True,
    ) -> str:
        """已有事务内追加事实；调用者可与 Task/Job 结果一起提交或回滚。"""
        require(connection.in_transaction, "PERSISTENCE_FAILED", "反馈必须在权威写事务内保存")
        content = {"event_id": event_id, "occurred_at_ms": occurred_at_ms, "payload": payload}
        validate_value({"seq": "0", **content}, "FeedbackEvent")
        digest = hashlib.sha256(encode_message(content)).hexdigest()
        existing = connection.execute(
            "SELECT seq,digest FROM scheduler_engine_feedback WHERE domain_id=? AND event_id=?",
            (domain_id, event_id),
        ).fetchone()
        if existing:
            require(existing["digest"] == digest, "IDEMPOTENCY_CONFLICT", "反馈身份对应不同内容")
            return existing["seq"]
        sequence = _next(self._row(connection, domain_id)["feedback_seq"])
        connection.execute(
            "INSERT INTO scheduler_engine_feedback(domain_id,seq,event_id,digest,event_json) VALUES (?,?,?,?,?)",
            (domain_id, sequence, event_id, digest, _json({"seq": sequence, **content})),
        )
        connection.execute(
            "UPDATE scheduler_engine_domain SET feedback_seq=?, "
            "snapshot_json=CASE WHEN ? THEN NULL ELSE snapshot_json END WHERE domain_id=?",
            (sequence, invalidate_snapshot, domain_id),
        )
        return sequence

    def record_feedback(self, domain_id: str, **kwargs: Any) -> str:
        with self.store.transaction() as connection:
            return self.append_feedback(connection, domain_id, **kwargs)

    def feedback(self, domain_id: str, *, after_seq: str, limit: int) -> dict[str, Any]:
        counter(after_seq)
        require(isinstance(limit, int) and not isinstance(limit, bool) and limit > 0, "INVALID_REQUEST", "反馈批次上限无效")
        with self.store.read() as connection:
            row = self._row(connection, domain_id)
            require(int(after_seq) <= int(row["feedback_seq"]), "FEEDBACK_GAP", "游标超过反馈水位")
            records = connection.execute(
                "SELECT event_json FROM scheduler_engine_feedback WHERE domain_id=? "
                "AND (length(seq)>length(?) OR (length(seq)=length(?) AND seq>?)) "
                "ORDER BY length(seq),seq LIMIT ?",
                (domain_id, after_seq, after_seq, after_seq, limit),
            ).fetchall()
            events = [_decode(record["event_json"]) for record in records]
            through = events[-1]["seq"] if events else after_seq
            require(
                all(int(event["seq"]) == int(after_seq) + index + 1 for index, event in enumerate(events))
                and (bool(events) or through == row["feedback_seq"]),
                "FEEDBACK_GAP", "反馈日志存在缺口",
            )
            return {
                "after_seq": after_seq, "through_seq": through,
                "has_more": int(through) < int(row["feedback_seq"]), "events": events,
            }

    def accept(self, request: dict[str, Any], response: dict[str, Any], *, accepted_at_ms: int) -> dict[str, Any]:
        """D：原子保存候选、head、待应用意图与检查点；不创建物理执行。"""
        validate_decision(request, response)
        body, result = request["body"], response["result"]
        domain_id = body["domain_id"]
        request_hash = hashlib.sha256(encode_message(request)).hexdigest()
        deadline = self._monotonic() + body["apply_budget_ms"] / 1000
        try:
            with self.store.transaction() as connection:
                row = self._row(connection, domain_id)
                require(
                    row["run_epoch"] == body["run_epoch"] and row["instance_id"] == body["instance_id"],
                    "STALE_DECISION", "旧引擎世代的响应不能接受",
                )
                existing = connection.execute(
                    "SELECT * FROM scheduler_engine_decision WHERE domain_id=? AND request_id=?",
                    (domain_id, request["request_id"]),
                ).fetchone()
                if existing:
                    require(
                        existing["request_hash"] == request_hash and existing["response_json"] == _json(response),
                        "IDEMPOTENCY_CONFLICT", "同一决策身份对应不同内容",
                    )
                    return _decode(existing["receipt_json"])
                require(
                    row["snapshot_json"] == _json(body["snapshot"])
                    and row["checkpoint_json"] == _json(body["checkpoint"]),
                    "STALE_DECISION", "事实或检查点已发生变化",
                )
                checkpoint = {
                    **body["checkpoint"], "revision": _next(body["checkpoint"]["revision"]),
                    "feedback_cursor": result["consumed_feedback_seq"], "state": result["next_state"],
                }
                head = _decode(row["head_json"])
                if result["status"] != "sync":
                    is_advance = result["status"] == "advance"
                    head = {
                        "decision_id": result["decision_id"], "task_scope": result["task_scope"],
                        "accepted_at_ms": accepted_at_ms, "status": result["status"],
                        "run_epoch": body["run_epoch"], "authorization_valid": is_advance,
                        "authorization_expires_at_ms": accepted_at_ms + body["apply_budget_ms"] if is_advance else None,
                    }
                    connection.execute(
                        "UPDATE scheduler_engine_intent SET status='superseded' WHERE domain_id=? AND status='pending'",
                        (domain_id,),
                    )
                    for intent in result["dispatch_now"]:
                        connection.execute(
                            "INSERT INTO scheduler_engine_intent "
                            "(domain_id,decision_id,intent_id,run_epoch,status,intent_json) VALUES (?,?,?,?,'pending',?)",
                            (domain_id, result["decision_id"], intent["intent_id"], body["run_epoch"], _json(intent)),
                        )
                connection.execute(
                    "UPDATE scheduler_engine_domain SET checkpoint_json=?,head_json=? WHERE domain_id=?",
                    (_json(checkpoint), _json(head) if head else None, domain_id),
                )
                # 恢复/排空的 wait 与 sync 均只记审计，有限追赶不会被接受 ACK 延长。
                # 正常接受的 ACK 留待下一次真实事件触发时消费，不单独唤醒调度。
                if result["status"] != "sync" and row["lifecycle"] == "running":
                    self.append_feedback(
                        connection, domain_id,
                        event_id=str(uuid5(UUID(result["decision_id"]), "decision_accepted")),
                        occurred_at_ms=accepted_at_ms, invalidate_snapshot=False,
                        payload={"kind": "decision_accepted", "decision_id": result["decision_id"], "task_scope": result["task_scope"]},
                    )
                receipt = {"checkpoint": checkpoint, "head": head}
                connection.execute(
                    "INSERT INTO scheduler_engine_decision "
                    "(domain_id,request_id,request_hash,response_json,receipt_json,accepted_at_ms) VALUES (?,?,?,?,?,?)",
                    (domain_id, request["request_id"], request_hash, _json(response), _json(receipt), accepted_at_ms),
                )
        except sqlite3.Error as error:
            raise EngineError("PERSISTENCE_FAILED", "调度候选事务未能提交") from error
        if result["status"] == "advance":
            self._deadlines[(domain_id, result["decision_id"])] = deadline
        return receipt

    def capture(
        self, domain_id: str, run_epoch: str, snapshot: dict[str, Any], *, feedback_limit: int,
    ) -> dict[str, Any]:
        """在 WorkflowStore 同一读写锁切面中捕获快照、检查点与连续反馈。"""
        with self.store.read():
            snapshot = self.publish_snapshot(domain_id, run_epoch, snapshot)
            checkpoint = self.read(domain_id)["checkpoint"]
            feedback = self.feedback(
                domain_id, after_seq=checkpoint["feedback_cursor"], limit=feedback_limit,
            )
            return {"snapshot": snapshot, "checkpoint": checkpoint, "feedback": feedback}

    def _authorized(self, row, head) -> bool:
        return bool(
            head and head["authorization_valid"] and head["status"] == "advance"
            and head["run_epoch"] == row["run_epoch"] and row["lifecycle"] == "running"
            and self._monotonic() < self._deadlines.get((row["domain_id"], head["decision_id"]), float("-inf"))
        )

    def authorized(self, domain_id: str, run_epoch: str, decision_id: str, task_id: str) -> bool:
        """有效期使用本进程单调钟；重启后旧墙钟到期时间不能恢复授权。"""
        with self.store.read() as connection:
            row = self._row(connection, domain_id)
            head = _decode(row["head_json"])
            return bool(
                row["run_epoch"] == run_epoch and self._authorized(row, head)
                and head["decision_id"] == decision_id and task_id in head["task_scope"]
            )

    def require_local_authority(
        self, connection: sqlite3.Connection, domain_id: str, *,
        run_epoch: str, decision_id: str, task_id: str,
    ) -> None:
        """在本地控制事实的写事务中复验 D；失效必须回滚整个控制提交。"""
        require(connection.in_transaction, "PERSISTENCE_FAILED", "本地控制授权必须在事实事务内复验")
        row = self._row(connection, domain_id)
        head = _decode(row["head_json"])
        require(
            row["run_epoch"] == run_epoch and self._authorized(row, head)
            and head["decision_id"] == decision_id and task_id in head["task_scope"],
            "STALE_DECISION", "本地控制提交的调度授权已失效",
        )

    def admit(
        self, connection: sqlite3.Connection, domain_id: str, *, run_epoch: str,
        decision_id: str, intent_id: str, attempt_id: str, command_ids: list[str],
        occurred_at_ms: int,
    ) -> None:
        """A：在实际 Job、冻结绑定和资源准入的同一事务中登记原执行身份。

        调用者必须先复验真实门禁和绑定。此处仅核验决策授权，不能代替资源准入。
        """
        require(connection.in_transaction, "PERSISTENCE_FAILED", "执行准入必须与调度账共用事务")
        validate_value(attempt_id, "Uuid")
        for command in command_ids:
            validate_value(command, "Id")
        require(len(set(command_ids)) == len(command_ids), "INVALID_REQUEST", "命令身份重复")
        domain = self._row(connection, domain_id)
        require(domain["run_epoch"] == run_epoch, "STALE_DECISION", "执行准入世代已失效")
        row = connection.execute(
            "SELECT * FROM scheduler_engine_intent WHERE domain_id=? AND intent_id=?",
            (domain_id, intent_id),
        ).fetchone()
        require(
            row is not None and row["decision_id"] == decision_id and row["run_epoch"] == run_epoch,
            "INVALID_DECISION", "执行准入未绑定已接受意图",
        )
        intent = _decode(row["intent_json"])
        if row["attempt_id"] is not None:
            require(
                row["attempt_id"] == attempt_id and _decode(row["command_ids_json"]) == command_ids,
                "IDEMPOTENCY_CONFLICT", "已准入意图不能改变执行身份",
            )
            return
        head = _decode(domain["head_json"])
        require(
            row["status"] == "pending" and self._authorized(domain, head)
            and head["decision_id"] == decision_id and intent["node"]["task_id"] in head["task_scope"],
            "STALE_DECISION", "候选已被替代、过期或暂停",
        )
        if intent["kind"] == "continue_attempt":
            require(intent["attempt_id"] == attempt_id, "INVALID_DECISION", "续行必须复用原尝试身份")
            original = connection.execute(
                "SELECT command_ids_json FROM scheduler_engine_intent "
                "WHERE domain_id=? AND attempt_id=? ORDER BY rowid LIMIT 1",
                (domain_id, attempt_id),
            ).fetchone()
            # 从旧执行记录迁入的 A 可以没有引擎账；真实命令绑定仍由平台权威复验。
            require(
                original is None or _decode(original["command_ids_json"]) == command_ids,
                "INVALID_DECISION", "续行不能改变原命令",
            )
        else:
            require(
                connection.execute(
                    "SELECT 1 FROM scheduler_engine_intent WHERE domain_id=? AND attempt_id=?",
                    (domain_id, attempt_id),
                ).fetchone() is None,
                "IDEMPOTENCY_CONFLICT", "新准入不能复用另一意图的尝试身份",
            )
        connection.execute(
            "UPDATE scheduler_engine_intent SET status='admitted',attempt_id=?,command_ids_json=? "
            "WHERE domain_id=? AND intent_id=?",
            (attempt_id, _json(command_ids), domain_id, intent_id),
        )
        self.append_feedback(
            connection, domain_id,
            event_id=str(uuid5(UUID(intent_id), "admitted")), occurred_at_ms=occurred_at_ms,
            payload={
                "kind": "intent_result", "decision_id": decision_id, "intent_id": intent_id,
                "node": intent["node"], "attempt_no": intent["attempt_no"],
                "disposition": "admitted", "attempt_id": attempt_id,
                "command_ids": command_ids, "reason": None,
            },
        )

    def attempt_authorized(self, domain_id: str, run_epoch: str, attempt_id: str) -> bool:
        """A 后不受 D 到期影响；调用者仍须检查 S、暂停、取消及物理执行门禁。"""
        with self.store.read() as connection:
            domain = self._row(connection, domain_id)
            if domain["run_epoch"] != run_epoch or domain["lifecycle"] not in {"running", "draining"}:
                return False
            return connection.execute(
                "SELECT 1 FROM scheduler_engine_intent WHERE domain_id=? AND run_epoch=? "
                "AND attempt_id=? AND status IN ('admitted','submitted') LIMIT 1",
                (domain_id, run_epoch, attempt_id),
            ).fetchone() is not None

    def reject_intent(
        self, domain_id: str, intent_id: str, *, reason: dict[str, Any], occurred_at_ms: int,
    ) -> bool:
        """仅拒绝尚未 A 的候选；已准入身份不能退回新候选或因此释放占用。"""
        validate_value(reason, "Reason")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM scheduler_engine_intent WHERE domain_id=? AND intent_id=?",
                (domain_id, intent_id),
            ).fetchone()
            require(row is not None, "INVALID_DECISION", "拒绝反馈没有原意图")
            if row["attempt_id"] is not None:
                return False
            if row["status"] == "rejected":
                return True
            intent = _decode(row["intent_json"])
            connection.execute(
                "UPDATE scheduler_engine_intent SET status='rejected' WHERE intent_id=?", (intent_id,),
            )
            self.append_feedback(
                connection, domain_id, event_id=str(uuid5(UUID(intent_id), "rejected")),
                occurred_at_ms=occurred_at_ms, payload={
                    "kind": "intent_result", "decision_id": row["decision_id"], "intent_id": intent_id,
                    "node": intent["node"], "attempt_no": intent["attempt_no"], "disposition": "rejected",
                    "attempt_id": None, "command_ids": [], "reason": reason,
                },
            )
            return True

    def report_delivery(
        self, connection: sqlite3.Connection, domain_id: str, attempt_id: str, *,
        disposition: str, occurred_at_ms: int, reason: dict[str, Any] | None = None,
    ) -> bool:
        """交付事实沿用最近 A 的 attempt/command；不证明物理开始，不重新授权旧世代。"""
        require(connection.in_transaction, "PERSISTENCE_FAILED", "交付反馈必须在事务中提交")
        require(disposition in {"submitted", "uncertain"}, "INVALID_REQUEST", "交付反馈类型无效")
        row = connection.execute(
            "SELECT * FROM scheduler_engine_intent WHERE domain_id=? AND attempt_id=? ORDER BY rowid DESC LIMIT 1",
            (domain_id, attempt_id),
        ).fetchone()
        # 受控人工干预和迁入的旧执行可能没有引擎 A，不能给它们伪造调度意图。
        if row is None:
            return False
        if row["status"] in {disposition, "uncertain"}:
            return True
        require(row["status"] in {"admitted", "submitted"}, "INVALID_DECISION", "交付反馈缺少有效准入")
        intent = _decode(row["intent_json"])
        connection.execute(
            "UPDATE scheduler_engine_intent SET status=? WHERE intent_id=?", (disposition, row["intent_id"]),
        )
        self.append_feedback(
            connection, domain_id, event_id=str(uuid5(UUID(row["intent_id"]), disposition)),
            occurred_at_ms=occurred_at_ms, payload={
                "kind": "intent_result", "decision_id": row["decision_id"], "intent_id": row["intent_id"],
                "node": intent["node"], "attempt_no": intent["attempt_no"], "disposition": disposition,
                "attempt_id": attempt_id, "command_ids": _decode(row["command_ids_json"]), "reason": reason,
            },
        )
        return True

    def intents(self, domain_id: str) -> list[dict[str, Any]]:
        with self.store.read() as connection:
            rows = connection.execute(
                "SELECT * FROM scheduler_engine_intent WHERE domain_id=? ORDER BY rowid", (domain_id,),
            ).fetchall()
            return [{
                "intent": _decode(row["intent_json"]), "decision_id": row["decision_id"],
                "run_epoch": row["run_epoch"], "status": row["status"],
                "attempt_id": row["attempt_id"], "command_ids": _decode(row["command_ids_json"]),
            } for row in rows]
