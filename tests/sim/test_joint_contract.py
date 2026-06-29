"""Plan 20 关节名契约工具单测(纯函数,无 ROS 依赖)。"""

from __future__ import annotations

from unilabos.device_mesh.joint_contract import (
    ContractIssue,
    extract_urdf_movable_joints,
    get_joint_map,
    normalize_joints,
    published_names,
    to_local,
    validate_contract,
)


class _FakeRegistry:
    def __init__(self, device_type_registry: dict):
        self.device_type_registry = device_type_registry


# --------------------------------------------------------------------------
# normalize_joints
# --------------------------------------------------------------------------

def test_normalize_joints_string_form():
    out = normalize_joints({"joints": {"carousel": "0_carousel_joint"}})
    assert out == {"carousel": {"urdf": "0_carousel_joint"}}


def test_normalize_joints_dict_form_with_type_unit():
    out = normalize_joints(
        {"joints": {"carousel": {"urdf": "0_carousel_joint", "type": "continuous", "unit": "rad"}}}
    )
    assert out == {"carousel": {"urdf": "0_carousel_joint", "type": "continuous", "unit": "rad"}}


def test_normalize_joints_name_alias():
    out = normalize_joints({"joints": {"tray": {"name": "tray_joint"}}})
    assert out == {"tray": {"urdf": "tray_joint"}}


def test_normalize_joints_empty_and_missing():
    assert normalize_joints(None) == {}
    assert normalize_joints({}) == {}
    assert normalize_joints({"mesh": "x"}) == {}
    # 缺 urdf 的 dict 形态被跳过
    assert normalize_joints({"joints": {"bad": {"type": "continuous"}}}) == {}
    # 空字符串被跳过
    assert normalize_joints({"joints": {"bad": ""}}) == {}


# --------------------------------------------------------------------------
# to_local / published_names
# --------------------------------------------------------------------------

def test_to_local_mapped_and_identity():
    jm = {"carousel": {"urdf": "0_carousel_joint"}}
    assert to_local(jm, "carousel") == "0_carousel_joint"
    # 未登记语义名 → identity(兼容直接传局部名)
    assert to_local(jm, "0_carousel_joint") == "0_carousel_joint"
    assert to_local({}, "anything") == "anything"


def test_published_names_adds_prefix():
    jm = {"carousel": {"urdf": "0_carousel_joint"}}
    assert published_names("incubator_liconic_stx110", jm) == {
        "carousel": "incubator_liconic_stx110_0_carousel_joint"
    }


# --------------------------------------------------------------------------
# extract_urdf_movable_joints
# --------------------------------------------------------------------------

_URDF = """<?xml version="1.0"?>
<robot name="full_dev">
  <link name="world"/>
  <joint name="incubator_liconic_stx110_base_link_joint" type="fixed">
    <parent link="world"/><child link="a"/>
  </joint>
  <joint name="incubator_liconic_stx110_0_carousel_joint" type="continuous">
    <parent link="a"/><child link="b"/><axis xyz="0 0 1"/>
  </joint>
  <joint name="other_device_tray_joint" type="prismatic">
    <parent link="c"/><child link="d"/>
  </joint>
</robot>
"""


def test_extract_excludes_fixed_and_filters_by_device():
    all_movable = extract_urdf_movable_joints(_URDF)
    assert all_movable == {
        "incubator_liconic_stx110_0_carousel_joint": "continuous",
        "other_device_tray_joint": "prismatic",
    }
    only_stx = extract_urdf_movable_joints(_URDF, "incubator_liconic_stx110")
    assert only_stx == {"incubator_liconic_stx110_0_carousel_joint": "continuous"}


def test_extract_handles_empty_and_garbage():
    assert extract_urdf_movable_joints("") == {}
    assert extract_urdf_movable_joints("<not xml") == {}


# --------------------------------------------------------------------------
# get_joint_map (injected registry)
# --------------------------------------------------------------------------

def test_get_joint_map_from_registry():
    reg = _FakeRegistry(
        {"incubator_liconic_stx110": {"model": {"mesh": "liconic_stx110", "joints": {"carousel": "0_carousel_joint"}}}}
    )
    assert get_joint_map("incubator_liconic_stx110", reg) == {"carousel": {"urdf": "0_carousel_joint"}}
    # 未知 class / 无 model / 无 joints → {}
    assert get_joint_map("unknown", reg) == {}
    reg2 = _FakeRegistry({"x": {"model": {"mesh": "m"}}})
    assert get_joint_map("x", reg2) == {}


# --------------------------------------------------------------------------
# validate_contract
# --------------------------------------------------------------------------

def _devices():
    return {
        "incubator_liconic_stx110": {
            "id": "incubator_liconic_stx110",
            "type": "device",
            "class": "incubator_liconic_stx110",
        }
    }


def test_validate_contract_green():
    reg = _FakeRegistry(
        {"incubator_liconic_stx110": {"model": {"joints": {"carousel": "0_carousel_joint"}}}}
    )
    issues = validate_contract(_URDF, _devices(), reg)
    assert issues == []


def test_validate_contract_red_missing():
    # registry 声明的局部名与 URDF 不一致 → missing
    reg = _FakeRegistry(
        {"incubator_liconic_stx110": {"model": {"joints": {"carousel": "turntable_joint"}}}}
    )
    issues = validate_contract(_URDF, _devices(), reg)
    assert len(issues) == 1
    assert issues[0].reason == "missing"
    assert issues[0].expected == "incubator_liconic_stx110_turntable_joint"
    assert "incubator_liconic_stx110_0_carousel_joint" in issues[0].urdf_joints
    assert isinstance(str(issues[0]), str)


def test_validate_contract_red_type_mismatch():
    reg = _FakeRegistry(
        {
            "incubator_liconic_stx110": {
                "model": {"joints": {"carousel": {"urdf": "0_carousel_joint", "type": "revolute"}}}
            }
        }
    )
    issues = validate_contract(_URDF, _devices(), reg)
    assert len(issues) == 1
    assert issues[0].reason == "type_mismatch"


def test_validate_contract_skips_undeclared_and_non_device():
    reg = _FakeRegistry({"incubator_liconic_stx110": {"model": {"mesh": "liconic_stx110"}}})
    # 未声明 joints → 跳过(无 issue)
    assert validate_contract(_URDF, _devices(), reg) == []
    # 非 device 节点跳过
    res_nodes = {"p1": {"id": "p1", "type": "plate", "class": "plate_96"}}
    assert validate_contract(_URDF, res_nodes, reg) == []
    # 空设备
    assert validate_contract(_URDF, {}, reg) == []
