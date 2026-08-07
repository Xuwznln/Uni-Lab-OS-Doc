"""Backend-shaped inventory schema migration and legacy adapter coverage."""

from __future__ import annotations

import sqlite3

from unilabos.app.scheduler.inventory import store as store_module
from unilabos.app.scheduler.inventory.store import InventoryStore, SCHEMA_VERSION


def _create_v4_database(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(store_module._SCHEMA)
    connection.executescript(store_module._SCHEMA_V2)
    connection.execute(store_module._SCHEMA_V3_ADD_PARENT)
    connection.execute(store_module._SCHEMA_V3_INDEX)
    for table, columns in store_module._SCHEMA_V4_COLUMNS.items():
        existing = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for column, definition in columns.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
    connection.execute(
        "INSERT INTO resource_template VALUES (?,?,?,?,?)",
        ("template-a", "Template A", "device", "{}", 2),
    )
    connection.execute(
        """
        INSERT INTO material_instance(
            edge_uuid,legacy_cloud_id,lot_id,template_id,barcode,status,
            version,parent_uuid
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        ("owner", "", "", "template-a", "OWNER", "warehouse", 2, ""),
    )
    connection.execute(
        """
        INSERT INTO material_instance(
            edge_uuid,legacy_cloud_id,lot_id,template_id,barcode,status,
            version,parent_uuid
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            "occupant",
            "legacy-cloud-id",
            "",
            "template-a",
            "OCCUPANT",
            "reserved",
            4,
            "owner",
        ),
    )
    connection.execute(
        "INSERT INTO resource_relation VALUES (?,?,?,?)",
        ("owner", "A1", "occupant", 4),
    )
    connection.execute(
        "INSERT INTO substance_content VALUES (?,?,?)",
        ("occupant", '{"temperature":25}', 1),
    )
    connection.execute("PRAGMA user_version=4")
    connection.commit()
    connection.close()


def test_v4_migrates_to_backend_tables_without_losing_edge_inventory(tmp_path):
    database = tmp_path / "inventory.db"
    _create_v4_database(str(database))

    store = InventoryStore(str(database))
    assert store.query_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION

    table_names = {
        row["name"]
        for row in store.query_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "resource_template",
        "resource_handle_template",
        "material",
        "relative_position",
        "site",
        "material_state_history",
    } <= table_names
    view_names = {
        row["name"]
        for row in store.query_all(
            "SELECT name FROM sqlite_master WHERE type='view'"
        )
    }
    assert {
        "inventory_resource_template",
        "material_instance",
        "resource_relation",
        "substance_content",
    } <= view_names
    material_columns = {
        row["name"] for row in store.query_all("PRAGMA table_info(material)")
    }
    assert {
        "uuid",
        "create_time",
        "update_time",
        "deleted_at",
        "description",
        "meta_data",
        "resource_template_uuid",
        "parent_uuid",
        "barcode",
        "type",
    } <= material_columns

    occupant = store.query_one("SELECT * FROM material WHERE uuid='occupant'")
    assert occupant["parent_uuid"] == "owner"
    assert occupant["data"] == '{"temperature":25}'
    assert occupant["type"] == "device"
    assert store.query_one(
        "SELECT inventory_status FROM material_inventory "
        "WHERE material_uuid='occupant'"
    )["inventory_status"] == "reserved"
    assert store.get_instance("occupant")["status"] == "reserved"
    assert store.query_one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert store.query_all("PRAGMA foreign_key_check") == []

    site = store.query_one("SELECT * FROM site WHERE name='A1'")
    assert site["uuid"] not in {"owner", "occupant"}
    assert site["material_uuid"] == "owner"
    assert site["occupied_material_uuid"] == "occupant"
    store.close()


def test_v5_database_backfills_backend_material_type_and_rebuilds_legacy_view(
    tmp_path,
):
    database = tmp_path / "inventory-v5.db"
    _create_v4_database(str(database))
    connection = sqlite3.connect(database)
    connection.executescript(store_module._SCHEMA_V5_BACKEND_CONTRACT)
    connection.execute(
        "UPDATE resource_template SET config_info=? WHERE uuid='template-a'",
        (
            '[{"name":"template-a","type":"container"},'
            '{"name":"pipette-tip","type":"tip"}]',
        ),
    )
    connection.execute(
        "UPDATE material SET class='template-a',name='template-a' WHERE uuid='owner'"
    )
    connection.execute(
        "UPDATE material SET class='component',name='pipette-tip' "
        "WHERE uuid='occupant'"
    )
    connection.commit()
    connection.close()

    store = InventoryStore(str(database))
    assert store.query_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    assert store.query_one("SELECT type FROM material WHERE uuid='owner'")["type"] == "container"
    assert store.query_one("SELECT type FROM material WHERE uuid='occupant'")["type"] == "tip"
    assert store.get_instance("occupant")["type"] == "tip"
    assert "type" in {
        row["name"] for row in store.query_all("PRAGMA table_info(material_instance)")
    }
    assert "idx_material_type_active" in {
        row["name"] for row in store.query_all("PRAGMA index_list(material)")
    }
    store.close()


def test_divergent_edge_v5_migrates_without_losing_instance_type(tmp_path):
    """旧 Edge-local v5 与 canonical v5 版本号冲突时也必须无损升级。"""

    database = tmp_path / "inventory-edge-v5.db"
    _create_v4_database(str(database))
    connection = sqlite3.connect(database)
    connection.execute(
        "ALTER TABLE material_instance ADD COLUMN type "
        "TEXT NOT NULL DEFAULT 'resource'"
    )
    connection.execute(
        "CREATE INDEX idx_instance_type ON material_instance(type)"
    )
    connection.execute(
        "UPDATE material_instance SET type='workcell' WHERE edge_uuid='owner'"
    )
    connection.execute(
        "UPDATE material_instance SET type='tip' WHERE edge_uuid='occupant'"
    )
    connection.execute("PRAGMA user_version=5")
    connection.commit()
    connection.close()

    store = InventoryStore(str(database))

    assert store.query_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    assert store.query_one("SELECT type FROM material WHERE uuid='owner'")[
        "type"
    ] == "workcell"
    assert store.query_one("SELECT type FROM material WHERE uuid='occupant'")[
        "type"
    ] == "tip"
    assert store.get_instance("owner")["type"] == "workcell"
    assert store.query_one(
        "SELECT name FROM sqlite_master "
        "WHERE name='_edge_v5_material_type_backup'"
    ) is None
    assert store.query_one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert store.query_all("PRAGMA foreign_key_check") == []
    store.close()


def test_divergent_v5_type_backup_survives_restart_between_v5_and_v6(tmp_path):
    """canonical v5 已提交后退出，下一进程仍须用持久 backup 恢复 type。"""

    database = tmp_path / "inventory-edge-v5-restart.db"
    _create_v4_database(str(database))
    connection = sqlite3.connect(database)
    connection.execute(
        "ALTER TABLE material_instance ADD COLUMN type "
        "TEXT NOT NULL DEFAULT 'resource'"
    )
    connection.execute(
        "UPDATE material_instance SET type='workcell' WHERE edge_uuid='owner'"
    )
    connection.execute(
        "UPDATE material_instance SET type='tip' WHERE edge_uuid='occupant'"
    )
    connection.execute(
        "CREATE TABLE _edge_v5_material_type_backup ("
        "material_uuid TEXT PRIMARY KEY, type TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO _edge_v5_material_type_backup "
        "SELECT edge_uuid,type FROM material_instance"
    )
    connection.commit()
    connection.executescript(store_module._SCHEMA_V5_BACKEND_CONTRACT)
    connection.close()

    store = InventoryStore(str(database))

    assert store.query_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    assert store.query_one("SELECT type FROM material WHERE uuid='owner'")[
        "type"
    ] == "workcell"
    assert store.query_one("SELECT type FROM material WHERE uuid='occupant'")[
        "type"
    ] == "tip"
    assert store.query_one(
        "SELECT name FROM sqlite_master "
        "WHERE name='_edge_v5_material_type_backup'"
    ) is None
    assert store.query_one("PRAGMA integrity_check")["integrity_check"] == "ok"
    assert store.query_all("PRAGMA foreign_key_check") == []
    store.close()


def test_fresh_v6_legacy_views_write_the_canonical_material_once(tmp_path):
    store = InventoryStore(str(tmp_path / "inventory.db"))
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO inventory_resource_template VALUES (?,?,?,?,?)",
            ("template-a", "Template A", "device", "{}", 1),
        )
        connection.execute(
            """
            INSERT INTO material_instance(
                edge_uuid,legacy_cloud_id,lot_id,template_id,barcode,status,
                version,parent_uuid
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            ("material-a", "", "", "template-a", "A", "warehouse", 1, ""),
        )

    assert store.query_one("SELECT COUNT(*) AS n FROM material")["n"] == 1
    assert store.query_one(
        "SELECT COUNT(*) AS n FROM material_inventory"
    )["n"] == 1
    assert store.query_one("SELECT type FROM material WHERE uuid='material-a'")[
        "type"
    ] == "resource"
    store.close()
