"""社区设备使用稳定完整命名空间，不再建立本地 alias。"""

from unilabos.app.package_cli import resolve_class_namespace


def test_default_community_namespace_is_derived_from_normalized_project_name():
    assert resolve_class_namespace("Vendor Liquid-Handler", None) == "community.vendor_liquid_handler"


def test_explicit_namespace_gets_community_prefix():
    assert resolve_class_namespace("ignored", "vendor.lh") == "community.vendor.lh"


def test_explicit_full_namespace_is_preserved():
    assert resolve_class_namespace("ignored", "community.vendor.lh") == "community.vendor.lh"
