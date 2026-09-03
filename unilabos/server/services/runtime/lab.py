"""实验室布局服务：``lab_layout`` 单行文档的读写（``runtime.db``，与 RuntimeService 共用连接）。

- 读：从未保存过返回 ``revision = 0`` 的空布局（默认格子边长），前端不用特判 404；
- 写：整份替换，``revision`` 乐观锁——客户端带读到的版本，不匹配抛 ``LabLayoutConflict``
  （路由映射为 409，正文带当前版本），由前端重读后再决定覆盖还是合并；
- 重置：删除文档，下一次读回到 revision 0。
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from unilabos.protocol.runtime.lab import (
    DEFAULT_LAYOUT_KEY,
    LabLayoutRead,
    LabLayoutWrite,
    LabZone,
)
from unilabos.server.database.sqlite_domain import DomainDatabase, SqliteDomain
from unilabos.server.database.tables.runtime import RUNTIME_DATABASE

DEFAULT_CELL_SIZE = 100.0


class LabLayoutConflict(RuntimeError):
    """写入携带的 revision 与权威当前版本不一致。"""

    def __init__(self, expected: int, current: int) -> None:
        super().__init__(f"lab layout revision mismatch: expected {expected}, current {current}")
        self.expected = expected
        self.current = current


def _now_ms() -> int:
    return int(time.time() * 1000)


class LabLayoutService(SqliteDomain):
    def __init__(self, database: DomainDatabase) -> None:
        super().__init__(database, RUNTIME_DATABASE)

    def _row(self, layout_key: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM lab_layout WHERE layout_key = ?", (layout_key,)
        ).fetchone()

    @staticmethod
    def _read(row: sqlite3.Row) -> LabLayoutRead:
        return LabLayoutRead(
            layout_key=row["layout_key"],
            revision=int(row["revision"]),
            cell_size=float(row["cell_size"]),
            zones=[LabZone.model_validate(item) for item in json.loads(row["zones"])],
            walls=list(json.loads(row["walls"])),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
        )

    def get_layout(self, layout_key: str = DEFAULT_LAYOUT_KEY) -> LabLayoutRead:
        row = self._row(layout_key)
        if row is None:
            return LabLayoutRead(
                layout_key=layout_key,
                revision=0,
                cell_size=DEFAULT_CELL_SIZE,
                zones=[],
                walls=[],
                created_at_ms=0,
                updated_at_ms=0,
            )
        return self._read(row)

    def save_layout(self, value: LabLayoutWrite, layout_key: str = DEFAULT_LAYOUT_KEY) -> LabLayoutRead:
        zones_json = json.dumps(
            [zone.model_dump(mode="json") for zone in value.zones], ensure_ascii=False, separators=(",", ":")
        )
        walls_json = json.dumps(value.walls, separators=(",", ":"))
        now = _now_ms()
        with self.write():
            row = self._row(layout_key)
            current = int(row["revision"]) if row is not None else 0
            if value.revision != current:
                raise LabLayoutConflict(value.revision, current)
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO lab_layout(
                        layout_key, revision, cell_size, zones, walls, created_at_ms, updated_at_ms
                    ) VALUES (?, 1, ?, ?, ?, ?, ?)
                    """,
                    (layout_key, value.cell_size, zones_json, walls_json, now, now),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE lab_layout
                    SET revision = revision + 1, cell_size = ?, zones = ?, walls = ?, updated_at_ms = ?
                    WHERE layout_key = ?
                    """,
                    (value.cell_size, zones_json, walls_json, now, layout_key),
                )
        return self.get_layout(layout_key)

    def reset_layout(self, layout_key: str = DEFAULT_LAYOUT_KEY) -> bool:
        with self.write():
            cursor = self.connection.execute("DELETE FROM lab_layout WHERE layout_key = ?", (layout_key,))
            return cursor.rowcount > 0


__all__ = ["DEFAULT_CELL_SIZE", "LabLayoutConflict", "LabLayoutService"]
