"""实验室布局（区域 / 围墙像素格）的表记录与建表规格（落 ``runtime.db``）。

布局是叠在设备位置（materials.db 的物料权威 position）之上的人工标注：把地图按固定边长
切成格子，格子归属某个区域或标记为围墙。它属于"这台 Host 的实验室"，所有连接同一
微后端的浏览器共享，因此存服务端而不是 localStorage；一个 Host 一份（``layout_key``
预留多布局，当前只用 ``default``）。整份文档一行保存，``revision`` 乐观锁。
"""

from __future__ import annotations

from typing import ClassVar

from sqlmodel import Field

from unilabos.protocol.base import JsonArray
from unilabos.server.database.schema import TableSpec
from unilabos.server.database.tables.base import (
    NonEmptyStr,
    TableObject,
    json_text_column,
)


class LabLayoutRecord(TableObject, table=True):
    __tablename__: ClassVar[str] = "lab_layout"

    layout_key: NonEmptyStr = Field(primary_key=True)
    revision: int = Field(ge=1)
    # 格子边长，与物料权威 position 同单位（mm 时 100 = 10 cm）
    cell_size: float = Field(gt=0)
    zones: JsonArray = Field(
        default_factory=list,
        sa_column=json_text_column("zones", default_json="[]"),
    )
    walls: JsonArray = Field(
        default_factory=list,
        sa_column=json_text_column("walls", default_json="[]"),
    )
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)


LAB_TABLE_MODELS = (LabLayoutRecord,)


LAB_TABLES = (
    TableSpec(
        "lab_layout",
        """
        CREATE TABLE IF NOT EXISTS lab_layout (
            layout_key TEXT PRIMARY KEY,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            cell_size REAL NOT NULL CHECK (cell_size > 0),
            zones TEXT NOT NULL CHECK (
                json_valid(zones) AND json_type(zones) = 'array'
            ),
            walls TEXT NOT NULL CHECK (
                json_valid(walls) AND json_type(walls) = 'array'
            ),
            created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
            updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0)
        )
        """,
    ),
)


__all__ = ["LAB_TABLES", "LAB_TABLE_MODELS", "LabLayoutRecord"]
