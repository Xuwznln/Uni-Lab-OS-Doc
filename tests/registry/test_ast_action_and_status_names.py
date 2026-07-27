import ast
from types import SimpleNamespace

from unilabos.registry.ast_registry_scanner import (
    _collect_imports,
    _extract_class_body,
    _parse_file,
)
from unilabos.registry.decorators import (
    action,
    device,
    get_action_meta,
    get_device_meta,
    get_resource_meta,
    resource,
)


def _extract(source: str) -> dict:
    tree = ast.parse(source)
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef)
    )
    return _extract_class_body(class_node, _collect_imports(tree))


def test_action_decorator_records_public_action_name():
    @action(
        action_name="read_status",
        displayname="读取状态",
        lock_resource=["sample"],
        estimate_duration_fixed=30,
        estimate_duration_express="{cycles} * 2",
    )
    def get_status():
        return "Ready"

    meta = get_action_meta(get_status)
    assert meta["action_name"] == "read_status"
    assert meta["displayname"] == "读取状态"
    assert meta["lock_resource"] == ["sample"]
    assert meta["estimate_duration_fixed"] == 30
    assert meta["estimate_duration_express"] == "{cycles} * 2"


def test_action_duration_defaults_to_one_minute():
    @action()
    def reset():
        return True

    meta = get_action_meta(reset)
    assert meta["lock_resource"] == []
    assert meta["estimate_duration_fixed"] == 60.0
    assert meta["estimate_duration_express"] == ""

    @action(materials_lock="sample")
    def legacy_lock_name():
        return True

    assert get_action_meta(legacy_lock_name)["lock_resource"] == ["sample"]


def test_explicit_action_wins_over_get_and_topic_status_inference():
    result = _extract(
        """
from unilabos.registry.decorators import action, topic_config

class Driver:
    @action(
        action_name="read_status",
        displayname="读取状态",
        lock_resource=["sample"],
        estimate_duration_fixed=30,
        estimate_duration_express="{cycles} * 2",
    )
    @topic_config(name="status")
    def get_status(self) -> str:
        return "Ready"
"""
    )

    assert result["status_properties"] == {}
    action_args = result["actions"]["get_status"]["action_args"]
    assert action_args["action_name"] == "read_status"
    assert action_args["displayname"] == "读取状态"
    assert action_args["lock_resource"] == ["sample"]
    assert action_args["estimate_duration_fixed"] == 30


def test_topic_name_and_implicit_get_prefix_define_public_status_names():
    result = _extract(
        """
from unilabos.registry.decorators import topic_config

class Driver:
    @topic_config(name="temperature")
    def read_temperature(self) -> float:
        return 25.0

    def get_pressure(self) -> float:
        return 1.0
"""
    )

    assert (
        result["status_properties"]["temperature"]["method_name"]
        == "read_temperature"
    )
    assert (
        result["status_properties"]["pressure"]["method_name"]
        == "get_pressure"
    )


def test_registry_maps_public_action_name_to_driver_method():
    from unilabos.registry.registry import Registry
    from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode

    result = _extract(
        """
from unilabos.registry.decorators import action

class Driver:
    @action(
        action_name="read_status",
        displayname="读取状态",
        lock_resource=["sample"],
        estimate_duration_fixed=30,
        estimate_duration_express="{cycles} * 2",
    )
    def get_status(self) -> str:
        return "Ready"

    @action(action_name="reset")
    def perform_reset(self) -> bool:
        return True

    def ping(self) -> bool:
        return True
"""
    )
    result.update({"module": "example:Driver", "file_path": "example.py"})

    entry = Registry()._build_device_entry_from_ast("example", result)
    actions = entry["class"]["action_value_mappings"]

    assert "get_status" not in actions
    assert actions["read_status"]["method_name"] == "get_status"
    assert actions["read_status"]["displayname"] == "读取状态"
    assert actions["read_status"]["lock_resource"] == ["sample"]
    assert actions["read_status"]["estimate_duration_fixed"] == 30
    assert (
        actions["read_status"]["estimate_duration_express"] == "{cycles} * 2"
    )
    assert actions["reset"]["displayname"] == "reset"
    assert actions["reset"]["lock_resource"] == []
    assert actions["reset"]["estimate_duration_fixed"] == 60.0
    assert actions["reset"]["estimate_duration_express"] == ""
    assert actions["auto-ping"]["displayname"] == "auto-ping"
    assert actions["auto-ping"]["estimate_duration_fixed"] == 60.0
    node = SimpleNamespace(_action_value_mappings=actions)
    assert (
        BaseROS2DeviceNode._resolve_driver_method_name(node, "read_status")
        == "get_status"
    )


def test_device_and_resource_metadata_runtime_and_ast(tmp_path):
    device_metadata = {
        "vendor": "UniLab",
        "specification": "D-100",
        "sites": 8,
    }
    resource_metadata = {"vendor": "UniLab", "capacity_ul": 2000, "wells": 96}

    @device(
        id="metadata_test_device",
        category=["test"],
        metadata=device_metadata,
    )
    class MetadataDevice:
        pass

    @resource(
        id="metadata_test_resource",
        category=["test"],
        metadata=resource_metadata,
    )
    def metadata_resource(name: str):
        return name

    assert get_device_meta(MetadataDevice)["metadata"] == device_metadata
    assert (
        get_resource_meta(metadata_resource)["metadata"] == resource_metadata
    )

    source_file = tmp_path / "metadata_plugin.py"
    source_file.write_text(
        """
from unilabos.registry.decorators import device, resource

@device(
    ids=["metadata_ast_a", "metadata_ast_b"],
    id_meta={"metadata_ast_b": {"metadata": {"sites": 16}}},
    category=["test"],
    metadata={"vendor": "UniLab", "sites": 8},
)
class MetadataAstDevice:
    pass

@resource(
    id="metadata_ast_resource",
    category=["test"],
    metadata={"capacity_ul": 2000, "wells": 96},
)
def metadata_ast_resource(name: str):
    return name
""",
        encoding="utf-8",
    )

    devices, resources = _parse_file(source_file, tmp_path)
    by_id = {item["device_id"]: item for item in devices}
    assert by_id["metadata_ast_a"]["metadata"] == {
        "vendor": "UniLab",
        "sites": 8,
    }
    assert by_id["metadata_ast_b"]["metadata"] == {
        "vendor": "UniLab",
        "sites": 16,
    }
    assert resources[0]["metadata"] == {"capacity_ul": 2000, "wells": 96}

    from unilabos.registry.registry import Registry

    registry = Registry()
    device_entry = registry._build_device_entry_from_ast(
        "metadata_ast_b", by_id["metadata_ast_b"]
    )
    resource_entry = registry._build_resource_entry_from_ast(
        "metadata_ast_resource", resources[0]
    )
    assert device_entry["metadata"] == {"vendor": "UniLab", "sites": 16}
    assert resource_entry["metadata"] == {"capacity_ul": 2000, "wells": 96}
