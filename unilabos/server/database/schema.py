"""四个独立 SQLite 文件共用的声明式 schema 与建库入口。

数据库采用 checksum 驱动的重建策略：库内 schema 与当前声明不一致时，
把旧文件改名保留为 ``<file>.bak-<时间戳>``，再按声明重建空库。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

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
    """一个物理 SQLite 文件的完整 schema。

    ``contract_version`` 表示表结构之外的行内容契约（JSON 列的形状、幂等键的
    派生规则、枚举取值等）。这类变化不改 DDL，但旧行已经无法被当前代码正确
    解释，同样走删库重建；变更时把版本号 +1 并在旁边注明原因。
    """

    key: str
    filename: str
    role: str
    synchronous: str
    tables: tuple[TableSpec, ...]
    contract_version: int = 1

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(table.name for table in self.tables)

    def statements(self) -> Iterable[str]:
        for table in self.tables:
            yield table.create_sql.strip()
            yield from (index.strip() for index in table.indexes)

    @property
    def checksum(self) -> str:
        canonical = "\n\n".join(
            (f"-- contract_version={self.contract_version}", *self.statements())
        ).encode("utf-8")
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
        # 无法验证身份和 schema 的数据库按当前声明重建。
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


def _retire_database_files(path: Path) -> Optional[Path]:
    """把旧库改名为 ``<file>.bak-<时间戳>`` 而不是直接删除：schema 漂移重建不应该悄悄丢数据。

    WAL / SHM 跟着主文件一起改名（保持 ``<file>-wal`` 后缀约定），旧库仍可用 sqlite 直接打开。
    返回备份主文件路径；没有旧文件时返回 None。
    """

    if not path.exists():
        for suffix in ("-wal", "-shm"):
            stale = Path(f"{path}{suffix}")
            if stale.exists():
                stale.unlink()
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = Path(f"{path}.bak-{stamp}")
    counter = 1
    while backup.exists():
        backup = Path(f"{path}.bak-{stamp}-{counter}")
        counter += 1
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{path}{suffix}")
        if source.exists():
            source.replace(Path(f"{backup}{suffix}"))
    return backup


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
    """创建或打开一个领域数据库；schema 变化时删除文件并重建。"""

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
        backup = _retire_database_files(database_path)
        logger.warning(
            "数据库 %s 的 schema 与当前代码不一致，已重建；旧库保留为 %s（确认不需要后可删除）",
            database_path,
            backup or "（无旧文件）",
        )
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
