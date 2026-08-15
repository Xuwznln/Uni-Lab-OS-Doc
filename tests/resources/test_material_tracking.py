"""物料（Material）根字段快照与差异追踪契约测试。"""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
import yaml
from pylabrobot.resources import Container, Coordinate
from pylabrobot.resources.barcode import Barcode

from unilabos.devices.workstation.coin_cell_assembly.YB_YH_materials import (
    MaterialPlate,
)
from unilabos.resources.bioyond.bottle_carriers import (
    BIOYOND_PolymerStation_1BottleCarrier,
)
from unilabos.resources.bioyond.bottles import (
    BIOYOND_PolymerStation_Reagent_Bottle,
)
from unilabos.resources.material_tracking import (
    MATERIAL_ROOT_FIELDS,
    capture_material_snapshot,
    diff_material_snapshots,
    diff_material_snapshot_sets,
)


REGISTRY_ROOT = Path(__file__).parents[2] / "unilabos" / "registry" / "resources"
KNOWN_OPTIONAL_IMPORTS = frozenset({"opentrons_shared_data", "rclpy"})


def _registry_resources() -> list[tuple[str, dict[str, Any]]]:
    """读取全部资源注册项并返回带来源文件的稳定参数列表。"""
    resources: list[tuple[str, dict[str, Any]]] = []
    for registry_file in sorted(REGISTRY_ROOT.rglob("*.yaml")):
        entries = yaml.safe_load(registry_file.read_text(encoding="utf-8")) or {}
        for resource_id, raw_entry in sorted(entries.items()):
            entry = dict(raw_entry or {})
            class_info = entry.get("class") or {}
            if not class_info.get("module"):
                continue
            entry["_source"] = str(registry_file.relative_to(REGISTRY_ROOT))
            resources.append((resource_id, entry))
    return resources


REGISTRY_RESOURCES = _registry_resources()


def _load_registry_resource(resource_id: str, entry: dict[str, Any]) -> Any:
    """按注册项构造一个隔离资源实例。

    Args:
        resource_id: 注册表中的稳定资源类型 ID，用作测试实例名称。
        entry: 对应 YAML 注册项，包含 Python 符号和运行类型。

    Returns:
        可由物料追踪模块读取的 PLR 资源或兼容 Python 对象。
    """
    class_info = entry["class"]
    module_name, symbol_name = class_info["module"].split(":", 1)
    symbol = getattr(import_module(module_name), symbol_name)
    if class_info.get("type") == "python":
        resource = symbol(rotation={"x": 0.0, "y": 0.0, "z": 0.0})
        resource.name = resource_id
        return resource
    return symbol(f"tracking-{resource_id}")


def _known_optional_failure(error: Exception, entry: dict[str, Any]) -> str | None:
    """识别当前测试机缺少的已知可选运行依赖，其他异常保持失败。

    Args:
        error: 注册资源导入或实例化抛出的原始异常。
        entry: 当前注册项，用于限制 PLR Opentrons loader 的已知兼容错误。

    Returns:
        已知缺失依赖名称；不是受控环境缺项时返回 ``None``。
    """
    if isinstance(error, ModuleNotFoundError):
        missing_name = str(error.name or "")
        for optional_name in KNOWN_OPTIONAL_IMPORTS:
            if missing_name == optional_name or missing_name.startswith(f"{optional_name}."):
                return optional_name
    if (
        isinstance(error, NameError)
        and "opentrons_shared_data" in str(error)
        and str(entry.get("_source", "")).startswith("opentrons/")
    ):
        return "opentrons_shared_data"
    return None


def test_diff_detects_native_and_unilabos_injected_root_fields() -> None:
    """证明原生属性、UniLab extra 和注入状态能映射为独立根字段差异。"""
    material = Container(
        name="sample-vial",
        size_x=10,
        size_y=10,
        size_z=20,
        category="container",
    )
    material.unilabos_uuid = "material-uuid-1"
    material.unilabos_extra = {"source": "registry"}
    material._unilabos_state = {"temperature": 20}

    before = capture_material_snapshot(material)

    material.name = "sample-vial-updated"
    material.unilabos_extra["source"] = "runtime"
    material._unilabos_state["temperature"] = 25

    after = capture_material_snapshot(material)
    change = diff_material_snapshots(before, after)

    assert change is not None
    assert change.material_uuid == "material-uuid-1"
    assert change.changed_fields == ("data", "extra", "name")
    assert change.values["data"]["temperature"] == 25
    assert change.values["extra"] == {"source": "runtime"}
    assert change.values["name"] == "sample-vial-updated"


def test_default_snapshot_covers_every_supported_material_root_field() -> None:
    """证明默认同步不会因调用方漏传字段而忽略任一受支持的物料根字段。"""
    material = Container("all-roots", 10, 10, 20, category="container")
    material.unilabos_uuid = "material-all-roots"

    snapshot = capture_material_snapshot(material)

    assert tuple(snapshot.fields) == MATERIAL_ROOT_FIELDS


def test_diff_promotes_tracker_state_without_requiring_live_callback() -> None:
    """证明同步时重新快照可发现未实时通知的液体根字段变化。"""
    material = Container(
        name="generic-container",
        size_x=10,
        size_y=10,
        size_z=20,
        category="container",
    )
    material.unilabos_uuid = "material-uuid-liquid"
    tracked_fields = ("data", "liquids", "liquid_history", "unknown_counter")

    before = capture_material_snapshot(material, fields=tracked_fields)
    material.tracker.add_liquid(12.5)
    after = capture_material_snapshot(material, fields=tracked_fields)
    change = diff_material_snapshots(before, after)

    assert change is not None
    assert change.changed_fields == ("data", "liquid_history", "liquids", "unknown_counter")
    assert change.values["data"]["volume"] == 12.5
    assert change.values["liquids"] == [("Unknown1", 12.5, "ul")]
    assert change.values["liquid_history"] == [("Unknown1", 12.5, "ul")]
    assert change.values["unknown_counter"] == 1
    assert "liquids" not in after.fields["data"]
    assert "liquid_history" not in after.fields["data"]


def test_diff_detects_serialized_site_occupancy_with_stable_site_uuid() -> None:
    """证明载架在操作完成后可按根字段 sites 识别占用变化并保持 Site 身份。"""
    carrier = BIOYOND_PolymerStation_1BottleCarrier("carrier")
    carrier.unilabos_uuid = "carrier-uuid"
    bottle = BIOYOND_PolymerStation_Reagent_Bottle("reagent-bottle")
    bottle.unilabos_uuid = "bottle-uuid"

    carrier.unassign_child_resource(carrier.sites[0])
    before = capture_material_snapshot(carrier, fields=("sites",))
    carrier.assign_resource_to_site(bottle, 0)
    after = capture_material_snapshot(carrier, fields=("sites",))
    change = diff_material_snapshots(before, after)

    assert change is not None
    assert change.changed_fields == ("sites",)
    assert before.fields["sites"][0]["uuid"] == after.fields["sites"][0]["uuid"]
    assert after.fields["sites"][0]["occupied_by"] == "reagent-bottle"


def test_diff_keeps_parent_barcode_class_and_config_as_separate_root_fields() -> None:
    """证明关系、条码、注入类型和配置按指定根字段独立比较。"""
    parent_before = Container("parent-before", 100, 100, 100, category="carrier")
    parent_before.unilabos_uuid = "parent-before-uuid"
    parent_after = Container("parent-after", 100, 100, 100, category="carrier")
    parent_after.unilabos_uuid = "parent-after-uuid"
    material = Container(
        "tracked-container",
        10,
        10,
        20,
        category="container",
        model="model-v1",
    )
    material.barcode = Barcode("BC-1", "Code 128", "front")
    material.unilabos_uuid = "tracked-container-uuid"
    material.unilabos_extra = {"unilabos_resource_class": "class-v1"}
    tracked_fields = (
        "uuid",
        "parent_uuid",
        "type",
        "class",
        "config",
        "barcode",
        "barcode_symbology",
    )

    parent_before.assign_child_resource(material, Coordinate(0, 0, 0))
    before = capture_material_snapshot(material, fields=tracked_fields)

    parent_after.assign_child_resource(material, Coordinate(1, 2, 3))
    material.model = "model-v2"
    material.barcode = Barcode("BC-2", "Code 39", "front")
    material.unilabos_extra["unilabos_resource_class"] = "class-v2"
    after = capture_material_snapshot(material, fields=tracked_fields)
    change = diff_material_snapshots(before, after)

    assert change is not None
    assert change.changed_fields == (
        "barcode",
        "barcode_symbology",
        "class",
        "config",
        "parent_uuid",
    )
    assert change.values["barcode"] == "BC-2"
    assert change.values["barcode_symbology"] == "Code 39"
    assert change.values["class"] == "class-v2"
    assert change.values["config"]["model"] == "model-v2"
    assert change.values["parent_uuid"] == "parent-after-uuid"
    assert after.fields["uuid"] == "tracked-container-uuid"
    assert after.fields["type"] == "container"


def test_diff_reads_injected_state_from_materialplate_without_state_serializer() -> None:
    """证明自定义料盘即使未覆写 serialize_state，也不会漏掉 UniLab 注入状态。"""
    material = MaterialPlate(
        name="coin-cell-material-plate",
        size_x=120,
        size_y=100,
        size_z=10,
        fill=True,
    )
    material.unilabos_uuid = "coin-cell-material-plate-uuid"

    before = capture_material_snapshot(material, fields=("data",))
    material._unilabos_state["info"] = "loaded"
    after = capture_material_snapshot(material, fields=("data",))
    change = diff_material_snapshots(before, after)

    assert change is not None
    assert change.changed_fields == ("data",)
    assert change.values["data"]["info"] == "loaded"


def test_snapshot_set_diff_emits_atomic_add_update_delete_by_material_uuid() -> None:
    """证明整批同步可按物料 UUID 拆成新增、修改、删除三类原子操作。"""
    removed = Container("removed", 10, 10, 20, category="container")
    removed.unilabos_uuid = "material-removed"
    updated = Container("updated", 10, 10, 20, category="container")
    updated.unilabos_uuid = "material-updated"
    added = Container("added", 10, 10, 20, category="container")
    added.unilabos_uuid = "material-added"

    before = [
        capture_material_snapshot(removed),
        capture_material_snapshot(updated),
    ]
    updated._unilabos_state = {"status": "changed"}
    after = [
        capture_material_snapshot(updated),
        capture_material_snapshot(added),
    ]

    changes = diff_material_snapshot_sets(before, after)

    assert [snapshot.material_uuid for snapshot in changes.added] == ["material-added"]
    assert changes.deleted_material_uuids == ("material-removed",)
    assert [change.material_uuid for change in changes.updated] == ["material-updated"]
    assert changes.updated[0].changed_fields == ("data",)
    assert changes.updated[0].values["data"]["status"] == "changed"


def test_registry_parameter_set_covers_every_declared_resource() -> None:
    """证明参数集覆盖全部 YAML 资源项且稳定 ID 没有重复。"""
    resource_ids = [resource_id for resource_id, _entry in REGISTRY_RESOURCES]

    assert len(REGISTRY_RESOURCES) == 135
    assert len(resource_ids) == len(set(resource_ids))


@pytest.mark.parametrize(
    ("resource_id", "entry"),
    REGISTRY_RESOURCES,
    ids=[resource_id for resource_id, _entry in REGISTRY_RESOURCES],
)
def test_every_registry_resource_tracks_unilabos_injected_fields(
    resource_id: str,
    entry: dict[str, Any],
) -> None:
    """证明每个注册资源都能按 UUID 识别 UniLab extra 与 state 根字段变化。"""
    try:
        material = _load_registry_resource(resource_id, entry)
    except Exception as error:
        optional_dependency = _known_optional_failure(error, entry)
        if optional_dependency:
            pytest.skip(f"{entry['_source']}:{resource_id} 缺少可选依赖 {optional_dependency}")
        raise

    material.unilabos_uuid = f"tracking-uuid-{resource_id}"
    material.unilabos_extra = {
        **deepcopy(getattr(material, "unilabos_extra", {}) or {}),
        "tracking_probe": "before",
        "unilabos_resource_class": f"tracking-class-before-{resource_id}",
    }
    injected_state = deepcopy(getattr(material, "_unilabos_state", {}) or {})
    injected_state["tracking_probe"] = "before"
    material._unilabos_state = injected_state

    config_serializer = getattr(material, "serialize", None)
    serialized_config = config_serializer() if callable(config_serializer) else {}
    tracks_serialized_model = isinstance(serialized_config, dict) and "model" in serialized_config
    tracked_fields = ["name", "class", "data", "extra"]
    if tracks_serialized_model:
        tracked_fields.append("config")

    before = capture_material_snapshot(material, fields=tracked_fields)
    material.name = f"tracking-updated-{resource_id}"
    material.unilabos_extra["tracking_probe"] = "after"
    material.unilabos_extra["unilabos_resource_class"] = f"tracking-class-after-{resource_id}"
    material._unilabos_state["tracking_probe"] = "after"
    if tracks_serialized_model:
        material.model = f"tracking-model-{resource_id}"
    after = capture_material_snapshot(material, fields=tracked_fields)
    change = diff_material_snapshots(before, after)

    assert change is not None
    assert change.material_uuid == f"tracking-uuid-{resource_id}"
    expected_fields = (
        ("class", "config", "data", "extra", "name")
        if tracks_serialized_model
        else ("class", "data", "extra", "name")
    )
    assert change.changed_fields == expected_fields
    assert change.values["name"] == f"tracking-updated-{resource_id}"
    assert change.values["class"] == f"tracking-class-after-{resource_id}"
    assert change.values["data"]["tracking_probe"] == "after"
    assert change.values["extra"]["tracking_probe"] == "after"
    if tracks_serialized_model:
        assert change.values["config"]["model"] == f"tracking-model-{resource_id}"
