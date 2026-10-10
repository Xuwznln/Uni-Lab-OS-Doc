"""测试 WareHouse 序列化/反序列化奔曜仓库与库位 UUID（扫码上料）"""

from unilabos.resources.bioyond.YB_warehouses import bioyond_warehouse_5x3x1
from unilabos.resources.warehouse import WareHouse


def test_serialize_without_uuids_unchanged():
    warehouse = bioyond_warehouse_5x3x1("手动堆栈左", row_offset=5)
    data = warehouse.serialize()

    assert "warehouse_uuid" not in data
    assert all("location_uuid" not in site for site in data["sites"])


def test_serialize_with_uuids():
    warehouse = bioyond_warehouse_5x3x1("手动堆栈左", row_offset=5)
    warehouse.warehouse_uuid = "wh-uuid"
    warehouse.site_location_uuids = {"F01": "loc-f01", "F03": "loc-f03"}
    data = warehouse.serialize()

    assert data["warehouse_uuid"] == "wh-uuid"
    sites = {site["label"]: site for site in data["sites"]}
    assert sites["F01"]["location_uuid"] == "loc-f01"
    assert sites["F03"]["location_uuid"] == "loc-f03"
    assert "location_uuid" not in sites["F02"]


def test_uuids_survive_sites_list_roundtrip():
    warehouse = bioyond_warehouse_5x3x1("手动堆栈左", row_offset=5)
    warehouse.warehouse_uuid = "wh-uuid"
    warehouse.site_location_uuids = {"F03": "loc-f03"}
    data = warehouse.serialize()

    restored = WareHouse(
        name=data["name"],
        size_x=data["size_x"],
        size_y=data["size_y"],
        size_z=data["size_z"],
        num_items_x=data["num_items_x"],
        num_items_y=data["num_items_y"],
        num_items_z=data["num_items_z"],
        sites=data["sites"],
        ordering_layout=data["ordering_layout"],
        warehouse_uuid=data["warehouse_uuid"],
    )

    assert restored.warehouse_uuid == "wh-uuid"
    assert restored.site_location_uuids == {"F03": "loc-f03"}
