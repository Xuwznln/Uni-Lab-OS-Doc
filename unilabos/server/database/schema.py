"""四个独立 SQLite 文件共用的声明式 schema 与建库入口。

未发布阶段不维护 migration 链：库内 checksum 与当前代码声明不一致时，
直接删除数据库文件重建。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class DatabaseIdentityConflict(RuntimeError):
    """物理 SQLite 文件已属于其他职责，拒绝打开以免误清数据。"""


@dataclass(frozen=True)
class TableSpec:
    """一张表及其索引的不可变建表规格。"""

    name: str
    create_sql: str
    indexes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatabaseSpec:
    """一个物理 SQLite 文件的完整 schema。"""

    key: str
    filename: str
    role: str
    synchronous: str
    tables: tuple[TableSpec, ...]

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(table.name for table in self.tables)

    def statements(self) -> Iterable[str]:
        for table in self.tables:
            yield table.create_sql.strip()
            yield from (index.strip() for index in table.indexes)

    @property
    def checksum(self) -> str:
        canonical = "\n\n".join(self.statements()).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


SCHEMA_IDENTITY_TABLE = TableSpec(
    name="schema_identity",
    create_sql="""
        CREATE TABLE IF NOT EXISTS schema_identity (
            database_key TEXT PRIMARY KEY CHECK (TRIM(database_key) <> ''),
            checksum TEXT NOT NULL CHECK (TRIM(checksum) <> ''),
            applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
        )
    """,
)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        if not str(row[0]).startswith("sqlite_")
    }


def _open_connection(
    path: Path, spec: DatabaseSpec, timeout: float
) -> sqlite3.Connection:
    connection = sqlite3.connect(
        str(path),
        timeout=timeout,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(f"PRAGMA synchronous = {spec.synchronous}")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _needs_rebuild(connection: sqlite3.Connection, spec: DatabaseSpec) -> bool:
    """判断现有库能否直接复用；身份属于其他库时抛错兜底。"""

    tables = _table_names(connection)
    if not tables:
        return False
    if "schema_identity" not in tables:
        # 旧格式（schema_migration 时代）或未知来源，未发布期直接重建
        return True
    rows = connection.execute(
        "SELECT database_key, checksum FROM schema_identity"
    ).fetchall()
    keys = {str(row[0]) for row in rows}
    if keys - {spec.key}:
        conflict = ", ".join(sorted(keys))
        raise DatabaseIdentityConflict(
            f"database file belongs to {conflict!r}, cannot open it as {spec.key!r}"
        )
    if not rows:
        # 有 domain 表但身份行缺失，视为不完整初始化
        return bool(tables - {"schema_identity"})
    return str(rows[0][1]) != spec.checksum


def _delete_database_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{path}{suffix}")
        if target.exists():
            target.unlink()


def _apply_schema(connection: sqlite3.Connection, spec: DatabaseSpec) -> None:
    with connection:
        for statement in spec.statements():
            connection.execute(statement)
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_identity(
                database_key, checksum, applied_at_ms
            ) VALUES (?, ?, CAST(strftime('%s', 'now') AS INTEGER) * 1000)
            """,
            (spec.key, spec.checksum),
        )


def initialize_database(
    path: str | Path,
    spec: DatabaseSpec,
    *,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    """创建或打开一个独立后端数据库；schema 变化时删除文件重建。"""

    database_path = Path(path).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = _open_connection(database_path, spec, timeout)
    try:
        rebuild = _needs_rebuild(connection, spec)
    except BaseException:
        connection.close()
        raise
    if rebuild:
        connection.close()
        logger.warning(
            "数据库 %s 的 schema 与当前代码不一致，删除重建（未发布期约定）",
            database_path,
        )
        _delete_database_files(database_path)
        connection = _open_connection(database_path, spec, timeout)
    try:
        _apply_schema(connection, spec)
        return connection
    except BaseException:
        connection.close()
        raise


__all__ = [
    "DatabaseIdentityConflict",
    "DatabaseSpec",
    "SCHEMA_IDENTITY_TABLE",
    "TableSpec",
    "initialize_database",
]
