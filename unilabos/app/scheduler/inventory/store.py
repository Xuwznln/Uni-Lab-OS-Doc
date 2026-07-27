"""SQLite WAL 持久化层.

单仓储分区单写者：所有写事务经进程内锁 + BEGIN IMMEDIATE 串行化，
业务变更、ledger、outbox 必须在同一事务提交（由 service 层保证，
store 只提供 transaction() 原语与行级 helper）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_template (
    template_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT '',
    spec_json     TEXT NOT NULL DEFAULT '{}',
    version       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS inventory_lot (
    lot_id             TEXT PRIMARY KEY,
    template_id        TEXT NOT NULL DEFAULT '',
    batch_no           TEXT NOT NULL DEFAULT '',
    unit               TEXT NOT NULL DEFAULT '',
    quantity_total     REAL NOT NULL DEFAULT 0,
    quantity_available REAL NOT NULL DEFAULT 0,
    quantity_reserved  REAL NOT NULL DEFAULT 0,
    expiry             TEXT NOT NULL DEFAULT '',
    quarantined        INTEGER NOT NULL DEFAULT 0,
    warehouse_zone_id  TEXT NOT NULL DEFAULT '',
    created_at         INTEGER NOT NULL DEFAULT 0,
    version            INTEGER NOT NULL DEFAULT 1,
    CHECK (quantity_total >= 0),
    CHECK (quantity_available >= 0),
    CHECK (quantity_reserved >= 0),
    CHECK (quantity_available + quantity_reserved <= quantity_total + 1e-9)
);
CREATE INDEX IF NOT EXISTS idx_lot_template ON inventory_lot(template_id, created_at);

CREATE TABLE IF NOT EXISTS material_instance (
    edge_uuid       TEXT PRIMARY KEY,
    legacy_cloud_id TEXT NOT NULL DEFAULT '',
    lot_id          TEXT NOT NULL DEFAULT '',
    template_id     TEXT NOT NULL DEFAULT '',
    barcode         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'warehouse',
    version         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_instance_barcode ON material_instance(barcode);
CREATE INDEX IF NOT EXISTS idx_instance_legacy ON material_instance(legacy_cloud_id);

CREATE TABLE IF NOT EXISTS resource_relation (
    parent_uuid TEXT NOT NULL,
    slot_id     TEXT NOT NULL DEFAULT '',
    child_uuid  TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (child_uuid)
);
CREATE INDEX IF NOT EXISTS idx_relation_parent ON resource_relation(parent_uuid);

CREATE TABLE IF NOT EXISTS substance_content (
    instance_uuid TEXT PRIMARY KEY,
    state_json    TEXT NOT NULL DEFAULT '{}',
    version       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS inventory_reservation (
    reservation_id TEXT PRIMARY KEY,
    workflow_id    TEXT NOT NULL,
    node_id        TEXT NOT NULL DEFAULT '',
    attempt        INTEGER NOT NULL DEFAULT 1,
    status         TEXT NOT NULL DEFAULT 'active',
    amounts_json   TEXT NOT NULL DEFAULT '{}',
    created_at     INTEGER NOT NULL DEFAULT 0,
    version        INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reservation_idem
    ON inventory_reservation(workflow_id, node_id, attempt);

CREATE TABLE IF NOT EXISTS inventory_ledger (
    ledger_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at   INTEGER NOT NULL,
    op_type       TEXT NOT NULL,
    aggregate_type TEXT NOT NULL DEFAULT '',
    aggregate_id  TEXT NOT NULL DEFAULT '',
    delta_json    TEXT NOT NULL DEFAULT '{}',
    actor         TEXT NOT NULL DEFAULT '',
    reason        TEXT NOT NULL DEFAULT '',
    causation_id  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_outbox (
    sequence          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL UNIQUE,
    edge_id           TEXT NOT NULL,
    lab_id            TEXT NOT NULL,
    aggregate_type    TEXT NOT NULL,
    aggregate_id      TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    event_type        TEXT NOT NULL,
    occurred_at       INTEGER NOT NULL,
    causation_id      TEXT NOT NULL DEFAULT '',
    payload_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS processed_command (
    command_id   TEXT PRIMARY KEY,
    result_json  TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'completed',
    processed_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sync_cursor (
    cursor_name    TEXT PRIMARY KEY,
    acked_sequence INTEGER NOT NULL DEFAULT 0,
    updated_at     INTEGER NOT NULL DEFAULT 0
);
"""

# v3：父物料列（云端 material.parent_material_uuid ≡ 资源树 parent_uuid，单一父）。
# 与 resource_relation 的关系：parent_uuid 列是唯一父层级事实；relation 行仅在
# 「父 + 具名位」时存在（slot_id = PLR site 名 ↔ 云端 sites.label，uuid 仅后端索引），
# 且 relation.parent_uuid 恒等于本列（_tx_upsert_relation 同步维护）。
# 空串表示顶层物料；单父由列语义天然保证（树形父）。
_SCHEMA_V3_ADD_PARENT = "ALTER TABLE material_instance ADD COLUMN parent_uuid TEXT NOT NULL DEFAULT ''"
_SCHEMA_V3_INDEX = "CREATE INDEX IF NOT EXISTS idx_instance_parent ON material_instance(parent_uuid)"

# v2：实验室操作系统布局层（元信息 / 分区 / 2D 摆放）。
# 只增表不改旧表，v1 库可原地升级。
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS lab_meta (
    meta_key   TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lab_zone (
    zone_id   TEXT PRIMARY KEY,
    name      TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL DEFAULT 'bench',
    x         REAL NOT NULL DEFAULT 0,
    y         REAL NOT NULL DEFAULT 0,
    w         REAL NOT NULL DEFAULT 100,
    h         REAL NOT NULL DEFAULT 100,
    meta_json TEXT NOT NULL DEFAULT '{}',
    version   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lab_placement (
    subject_id   TEXT PRIMARY KEY,
    subject_kind TEXT NOT NULL DEFAULT 'container',
    zone_id      TEXT NOT NULL DEFAULT '',
    x            REAL NOT NULL DEFAULT 0,
    y            REAL NOT NULL DEFAULT 0,
    w            REAL NOT NULL DEFAULT 40,
    h            REAL NOT NULL DEFAULT 40,
    rotation     REAL NOT NULL DEFAULT 0,
    label        TEXT NOT NULL DEFAULT '',
    meta_json    TEXT NOT NULL DEFAULT '{}',
    version      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_placement_zone ON lab_placement(zone_id);
"""


class InventoryStore:
    """SQLite WAL 存储：单连接 + 进程内写锁（单写者）."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            current = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if current < 1:
                self._conn.executescript(_SCHEMA)
            if current < 2:
                self._conn.executescript(_SCHEMA_V2)
            if current < 3:
                # ALTER 前先查列（半途中断的迁移可安全重放）
                cols = {
                    r[1] for r in self._conn.execute(
                        "PRAGMA table_info(material_instance)"
                    ).fetchall()
                }
                if "parent_uuid" not in cols:
                    self._conn.execute(_SCHEMA_V3_ADD_PARENT)
                self._conn.execute(_SCHEMA_V3_INDEX)
            if current < SCHEMA_VERSION:
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- 事务原语 -----------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """串行化写事务：业务行 + ledger + outbox 在此上下文内一起提交."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    # -- 只读 helper ---------------------------------------------------------

    def query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def query_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # -- 常用读 -------------------------------------------------------------

    def get_lot(self, lot_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM inventory_lot WHERE lot_id = ?", (lot_id,))

    def get_instance(self, edge_uuid: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM material_instance WHERE edge_uuid = ?", (edge_uuid,))

    def find_instance_by_barcode_active(self, barcode: str, active_states: tuple) -> Optional[Dict[str, Any]]:
        placeholders = ",".join("?" for _ in active_states)
        return self.query_one(
            f"SELECT * FROM material_instance WHERE barcode = ? AND status IN ({placeholders})",
            (barcode, *active_states),
        )

    def find_instance_by_legacy_cloud_id(self, cloud_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM material_instance WHERE legacy_cloud_id = ?", (cloud_id,))

    def lots_by_template_fifo(self, template_id: str) -> List[Dict[str, Any]]:
        """FIFO：按 created_at 升序（同毫秒按 rowid 插入序）返回可用批次."""
        return self.query_all(
            "SELECT * FROM inventory_lot WHERE template_id = ? AND quarantined = 0 "
            "AND quantity_available > 0 ORDER BY created_at ASC, rowid ASC",
            (template_id,),
        )

    def get_reservation(self, workflow_id: str, node_id: str, attempt: int) -> Optional[Dict[str, Any]]:
        return self.query_one(
            "SELECT * FROM inventory_reservation WHERE workflow_id = ? AND node_id = ? AND attempt = ?",
            (workflow_id, node_id, attempt),
        )

    def reservations_for_workflow(self, workflow_id: str) -> List[Dict[str, Any]]:
        return self.query_all(
            "SELECT * FROM inventory_reservation WHERE workflow_id = ? ORDER BY created_at ASC, reservation_id ASC",
            (workflow_id,),
        )

    def get_relation(self, child_uuid: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM resource_relation WHERE child_uuid = ?", (child_uuid,))

    def children_of(self, parent_uuid: str) -> List[Dict[str, Any]]:
        return self.query_all(
            "SELECT * FROM resource_relation WHERE parent_uuid = ? ORDER BY slot_id ASC", (parent_uuid,)
        )

    def get_content(self, instance_uuid: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM substance_content WHERE instance_uuid = ?", (instance_uuid,))

    def component_children_of(self, parent_uuid: str) -> List[Dict[str, Any]]:
        """组成父子（material_instance.parent_uuid）下的直接子物料；与 site 放置无关."""
        return self.query_all(
            "SELECT * FROM material_instance WHERE parent_uuid = ? ORDER BY edge_uuid ASC",
            (parent_uuid,),
        )

    def get_processed_command(self, command_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM processed_command WHERE command_id = ?", (command_id,))

    # -- 实验室布局（lab_meta / lab_zone / lab_placement） --------------------

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.query_one("SELECT meta_value FROM lab_meta WHERE meta_key = ?", (key,))
        return str(row["meta_value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO lab_meta(meta_key, meta_value) VALUES (?, ?) "
                "ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value",
                (key, value),
            )

    def list_zones(self) -> List[Dict[str, Any]]:
        return self.query_all("SELECT * FROM lab_zone ORDER BY zone_id ASC")

    def list_placements(self, zone_id: str = "") -> List[Dict[str, Any]]:
        if zone_id:
            return self.query_all(
                "SELECT * FROM lab_placement WHERE zone_id = ? ORDER BY subject_id ASC", (zone_id,)
            )
        return self.query_all("SELECT * FROM lab_placement ORDER BY subject_id ASC")

    def get_placement(self, subject_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM lab_placement WHERE subject_id = ?", (subject_id,))

    # -- outbox / cursor -----------------------------------------------------

    def pending_outbox(self, after_sequence: int, limit: int = 100) -> List[Dict[str, Any]]:
        return self.query_all(
            "SELECT * FROM sync_outbox WHERE sequence > ? ORDER BY sequence ASC LIMIT ?",
            (after_sequence, limit),
        )

    def get_cursor(self, name: str = "cloud") -> int:
        row = self.query_one("SELECT acked_sequence FROM sync_cursor WHERE cursor_name = ?", (name,))
        return int(row["acked_sequence"]) if row else 0

    def set_cursor(self, name: str, acked_sequence: int, now_ms: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO sync_cursor(cursor_name, acked_sequence, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(cursor_name) DO UPDATE SET acked_sequence = excluded.acked_sequence, "
                "updated_at = excluded.updated_at",
                (name, acked_sequence, now_ms),
            )

    def max_outbox_sequence(self) -> int:
        row = self.query_one("SELECT COALESCE(MAX(sequence), 0) AS s FROM sync_outbox")
        return int(row["s"]) if row else 0

    # -- 事务内写 helper（必须在 transaction() 上下文中调用） -----------------

    @staticmethod
    def tx_insert_ledger(
        conn: sqlite3.Connection,
        occurred_at: int,
        op_type: str,
        aggregate_type: str,
        aggregate_id: str,
        delta: Dict[str, Any],
        actor: str = "",
        reason: str = "",
        causation_id: str = "",
    ) -> None:
        conn.execute(
            "INSERT INTO inventory_ledger(occurred_at, op_type, aggregate_type, aggregate_id, "
            "delta_json, actor, reason, causation_id) VALUES (?,?,?,?,?,?,?,?)",
            (occurred_at, op_type, aggregate_type, aggregate_id,
             json.dumps(delta, ensure_ascii=False), actor, reason, causation_id),
        )

    @staticmethod
    def tx_insert_outbox(
        conn: sqlite3.Connection,
        event_id: str,
        edge_id: str,
        lab_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        occurred_at: int,
        causation_id: str,
        payload: Dict[str, Any],
    ) -> int:
        cur = conn.execute(
            "INSERT INTO sync_outbox(event_id, edge_id, lab_id, aggregate_type, aggregate_id, "
            "aggregate_version, event_type, occurred_at, causation_id, payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_id, edge_id, lab_id, aggregate_type, aggregate_id, aggregate_version,
             event_type, occurred_at, causation_id, json.dumps(payload, ensure_ascii=False)),
        )
        return int(cur.lastrowid or 0)
