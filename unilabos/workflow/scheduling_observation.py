"""原始计时证据与作业结果同事务保存；平台不拟合或更新算法模型。"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from unilabos.scheduling.protocol import EngineError, decode_message, encode_message, require, validate_value

OBSERVED_DISPATCH_BOUNDARY = "dispatch_to_observed_completion"


def ensure_observation_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS scheduler_engine_observation ("
        "sequence INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT NOT NULL UNIQUE,"
        "occurred_at_ms INTEGER NOT NULL, digest TEXT NOT NULL, payload_json TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS scheduler_engine_source_cursor ("
        "domain_id TEXT NOT NULL, source TEXT NOT NULL, sequence INTEGER NOT NULL DEFAULT 0,"
        "PRIMARY KEY(domain_id,source))"
    )


def append_observation(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    require(connection.in_transaction, "PERSISTENCE_FAILED", "原始样本必须与结果同事务提交")
    sample = record["observation"]
    validate_value(sample, "Observation")
    wire = encode_message(sample)
    digest = hashlib.sha256(wire).hexdigest()
    existing = connection.execute(
        "SELECT digest FROM scheduler_engine_observation WHERE observation_id=?", (sample["observation_id"],),
    ).fetchone()
    if existing:
        require(existing[0] == digest, "IDEMPOTENCY_CONFLICT", "同一计时样本已存在不同内容")
        return
    connection.execute(
        "INSERT INTO scheduler_engine_observation(observation_id,occurred_at_ms,digest,payload_json) VALUES (?,?,?,?)",
        (sample["observation_id"], record["occurred_at_ms"], digest, wire.decode()),
    )


def import_legacy_observations(
    connection, domain_id: str, *, occurred_at_ms: int, rebuild: bool = False, allow_model_reset: bool = False,
) -> int:
    """旧表只作为原始证据读取；不导入模型，也不按现行规则补猜旧元数据。"""
    require(connection.in_transaction, "PERSISTENCE_FAILED", "历史导入必须在权威事务内提交")
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scheduler_timing_observations'",
    ).fetchone() is None:
        return 0
    source = connection.execute(
        "SELECT sequence FROM scheduler_engine_source_cursor WHERE domain_id=? AND source='legacy_timing'",
        (domain_id,),
    ).fetchone()
    after = source[0] if source and not rebuild else 0
    cursor = connection.execute(
        "SELECT * FROM scheduler_timing_observations WHERE sequence>? ORDER BY sequence", (after,),
    )
    last = after
    skipped = 0
    while rows := cursor.fetchmany(1000):
        for row in rows:
            try:
                wire = row["payload_json"].encode()
                require(hashlib.sha256(wire).hexdigest() == row["payload_hash"], "STATE_CORRUPT", "旧样本摘要不一致")
                old = decode_message(wire)
                require(old["observation_id"] == row["observation_id"], "STATE_CORRUPT", "旧样本身份不一致")
                seconds = old["actual_s"]
                require(
                    isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                    and math.isfinite(seconds) and seconds >= 0,
                    "STATE_CORRUPT", "旧样本时长无效",
                )
                sample = {
                    "observation_id": old["observation_id"], "node": None, "attempt_id": None,
                    "action_id": old["device_action_key"], "action_revision": old["action_version"],
                    "device_id": old["device_id"] or None, "parameters": old["params"],
                    "elapsed_ms": round(seconds * 1000), "timing_boundary": old["timing_boundary"],
                    "outcome": old["outcome"], "complete": old["outcome"] == "success",
                    "calibration": old["calibration"], "provenance": "legacy_import",
                    "source_ref": "scheduler_timing_observations:" + old["observation_id"],
                    "timing_spec_id": old["device_action_key"], "timing_spec_version": old["spec_version"],
                }
                validate_value(sample, "Observation", code="STATE_CORRUPT")
            except (EngineError, KeyError, TypeError, ValueError, OverflowError) as error:
                if not (rebuild and allow_model_reset):
                    raise EngineError(
                        "STATE_INCOMPATIBLE", "历史计时证据不完整或损坏，不能静默忽略",
                        details={"source": "scheduler_timing_observations", "sequence": row["sequence"]},
                    ) from error
                # 原表完整保留；调用方必须与新基线一起提交明确的模型重置审计。
                skipped += 1
            else:
                # 新账中同身份异内容的冲突不属于可忽略的历史缺失。
                append_observation(connection, {"occurred_at_ms": occurred_at_ms, "observation": sample})
            last = row["sequence"]
    if last != after or rebuild:
        connection.execute(
            "INSERT INTO scheduler_engine_source_cursor(domain_id,source,sequence) VALUES (?,'legacy_timing',?) "
            "ON CONFLICT(domain_id,source) DO UPDATE SET sequence=excluded.sequence", (domain_id, last),
        )
    return skipped


def publish_observations(
    connection, domain_id: str, publish, *, replay_id: str | None = None, limit: int | None = None,
) -> bool:
    """同事务发布证据和游标，返回是否有余量；启动/重建默认完整回放原样本。"""
    require(connection.in_transaction, "PERSISTENCE_FAILED", "样本回放必须在权威事务内提交")
    require(
        limit is None or (isinstance(limit, int) and not isinstance(limit, bool) and limit > 0),
        "INVALID_REQUEST", "样本回放批次上限无效",
    )
    require(replay_id is None or limit is None, "INVALID_REQUEST", "显式重建必须完整回放原样本")
    source = connection.execute(
        "SELECT sequence FROM scheduler_engine_source_cursor WHERE domain_id=? AND source='observations'",
        (domain_id,),
    ).fetchone()
    after = source[0] if source and replay_id is None else 0
    cursor = connection.execute(
        "SELECT * FROM scheduler_engine_observation WHERE sequence>? ORDER BY sequence LIMIT ?",
        (after, -1 if limit is None else limit),
    )
    last = after
    while rows := cursor.fetchmany(1000):
        for row in rows:
            wire = row["payload_json"].encode()
            require(hashlib.sha256(wire).hexdigest() == row["digest"], "PERSISTENCE_FAILED", "原始计时证据摘要不一致")
            observation = decode_message(wire)
            validate_value(observation, "Observation", code="PERSISTENCE_FAILED")
            require(observation["observation_id"] == row["observation_id"], "PERSISTENCE_FAILED", "原始样本身份不一致")
            identity = f"unilab.scheduler.observation:{domain_id}:{row['observation_id']}"
            if replay_id is not None:
                identity += f":rebuild:{replay_id}"
            publish(
                connection, domain_id, event_id=str(uuid5(NAMESPACE_URL, identity)),
                occurred_at_ms=row["occurred_at_ms"],
                payload={"kind": "observation_recorded", "observation": observation},
            )
            last = row["sequence"]
    if last != after or replay_id is not None:
        connection.execute(
            "INSERT INTO scheduler_engine_source_cursor(domain_id,source,sequence) VALUES (?,'observations',?) "
            "ON CONFLICT(domain_id,source) DO UPDATE SET sequence=excluded.sequence", (domain_id, last),
        )
    return connection.execute(
        "SELECT 1 FROM scheduler_engine_observation WHERE sequence>? LIMIT 1", (last,),
    ).fetchone() is not None
