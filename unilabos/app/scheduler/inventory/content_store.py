"""样品（Sample）与当前内容物（CurrentSubstance）的 SQLite 结构。"""

from __future__ import annotations

import sqlite3

_SCHEMA_V10_CONTAINER_CONTENT = r"""
CREATE TABLE IF NOT EXISTS sample (
    uuid TEXT PRIMARY KEY NOT NULL,
    create_time DATETIME NOT NULL,
    update_time DATETIME NOT NULL,
    deleted_at DATETIME,
    description TEXT,
    meta_data TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta_data)),
    material_uuid TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    sample_type TEXT,
    source TEXT,
    quantity REAL NOT NULL CHECK (quantity >= 0),
    quantity_unit TEXT NOT NULL CHECK (quantity_unit <> ''),
    collected_at DATETIME,
    expires_at DATETIME,
    CHECK (
        expires_at IS NULL OR collected_at IS NULL OR expires_at >= collected_at
    ),
    FOREIGN KEY (material_uuid) REFERENCES material (uuid) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sample_material_active
    ON sample (material_uuid) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_sample_code_active
    ON sample (LOWER(code)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sample_type_active
    ON sample (sample_type) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS current_substance (
    uuid TEXT PRIMARY KEY NOT NULL,
    create_time DATETIME NOT NULL,
    update_time DATETIME NOT NULL,
    deleted_at DATETIME,
    description TEXT,
    meta_data TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta_data)),
    material_uuid TEXT NOT NULL,
    name TEXT,
    composition TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(composition) AND json_type(composition) = 'array'
    ),
    quantity REAL NOT NULL CHECK (quantity >= 0),
    quantity_unit TEXT NOT NULL CHECK (quantity_unit <> ''),
    physical_state TEXT NOT NULL CHECK (physical_state <> ''),
    revision INTEGER NOT NULL CHECK (revision > 0),
    observed_at DATETIME NOT NULL,
    FOREIGN KEY (material_uuid) REFERENCES material (uuid) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_current_substance_material_active
    ON current_substance (material_uuid) WHERE deleted_at IS NULL;
"""


def migrate_container_content_schema(connection: sqlite3.Connection) -> None:
    """幂等创建 Backend 同形的样品与当前内容物表。"""

    connection.executescript(_SCHEMA_V10_CONTAINER_CONTENT)


__all__ = ["migrate_container_content_schema"]
