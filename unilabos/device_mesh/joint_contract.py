"""关节名契约工具 (Plan 20)。

统一"语义关节名 → URDF 局部关节名"的单一真源(registry 的 ``model.joints``),
并提供启动期 URDF 关节名校验,杜绝"仿真 backend 关节名与设备广场模型关节名漂移"
导致的静默失败(任务成功但 RViz/Isaac 不动)。

契约两端:
- 消费端(URDF/广场模型):``ResourceVisualization`` 用 ``device_name = node_id + "_"``
  实例化宏,最终 URDF 关节名 = ``<node_id>_<局部名>``。
- 生产端(仿真发布器):``SimpleJointPublisher`` 发布 ``f"{device_id}_{局部名}"``。

本模块把"语义名→局部名"收敛到 registry,backend 只引用语义名;校验器在 URDF
(xacro 展开后)解析实有可动关节,与发布器期望的全名比对。

引擎无关:同一份 ``model.joints`` 与 ``validate_contract`` 可服务 RViz/Isaac/Matterix。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

_FIXED = "fixed"


class JointContractError(RuntimeError):
    """关节名契约校验失败（``UNILAB_JOINT_CONTRACT=error`` 时抛出）。"""


def normalize_joints(model_config: Optional[dict]) -> dict[str, dict]:
    """把 ``model.joints`` 归一化为 ``{语义名: {"urdf": <局部名>, "type"?, "unit"?}}``。

    支持两种声明形态:

    精简::

        joints:
          carousel: 0_carousel_joint

    扩展::

        joints:
          carousel:
            urdf: 0_carousel_joint
            type: continuous
            unit: rad

    无 ``joints`` 声明返回 ``{}``(兼容未登记设备)。
    """
    if not model_config:
        return {}
    raw = model_config.get("joints") or {}
    out: dict[str, dict] = {}
    for sem, spec in raw.items():
        if isinstance(spec, str):
            if spec:
                out[sem] = {"urdf": spec}
        elif isinstance(spec, dict):
            urdf = spec.get("urdf") or spec.get("name")
            if not urdf:
                continue
            entry: dict = {"urdf": urdf}
            if spec.get("type"):
                entry["type"] = spec["type"]
            if spec.get("unit"):
                entry["unit"] = spec["unit"]
            out[sem] = entry
    return out


def _resolve_registry(registry):
    if registry is not None:
        return registry
    try:
        from unilabos.registry.registry import lab_registry

        return lab_registry
    except Exception:
        return None


def get_joint_map(device_class: str, registry=None) -> dict[str, dict]:
    """从 registry 读取设备 class 的归一化 joints 映射;无则 ``{}``。"""
    registry = _resolve_registry(registry)
    if registry is None:
        return {}
    try:
        entry = registry.device_type_registry.get(device_class) or {}
        model = entry.get("model")
    except Exception:
        return {}
    return normalize_joints(model)


def to_local(joint_map: dict[str, dict], name: str) -> str:
    """语义名 → 局部名;映射缺失按 identity 返回(兼容未登记/直接传局部名)。"""
    spec = joint_map.get(name)
    if spec and spec.get("urdf"):
        return spec["urdf"]
    return name


def published_names(device_id: str, joint_map: dict[str, dict]) -> dict[str, str]:
    """返回 ``{语义名: 发布器实际发出的全名 f"{device_id}_{局部名}"}``。"""
    return {sem: f"{device_id}_{spec['urdf']}" for sem, spec in joint_map.items()}


def extract_urdf_movable_joints(urdf_str: str, device_id: Optional[str] = None) -> dict[str, str]:
    """解析(xacro 展开后的)URDF,返回非 ``fixed`` 关节 ``{关节名: 类型}``。

    ``device_id`` 给定时只取前缀为 ``f"{device_id}_"`` 的关节。
    """
    from lxml import etree

    joints: dict[str, str] = {}
    if not urdf_str:
        return joints
    try:
        root = etree.fromstring(urdf_str.encode("utf-8"))
    except Exception:
        try:
            root = etree.fromstring(urdf_str)
        except Exception:
            return joints
    prefix = f"{device_id}_" if device_id is not None else None
    for j in root.iter("joint"):
        name = j.get("name")
        jtype = j.get("type", "")
        if not name or jtype == _FIXED:
            continue
        if prefix is not None and not name.startswith(prefix):
            continue
        joints[name] = jtype
    return joints


@dataclass
class ContractIssue:
    """单条关节契约不一致。"""

    device_id: str
    semantic: str
    expected: str  # 发布器期望发布的全名 f"{device_id}_{局部名}"
    reason: str  # "missing" | "type_mismatch"
    urdf_joints: list[str] = field(default_factory=list)  # 该设备 URDF 实有可动关节
    detail: str = ""

    def __str__(self) -> str:
        return (
            f"[关节契约] 设备 '{self.device_id}' 语义关节 '{self.semantic}' "
            f"期望发布 '{self.expected}' 但 {self.reason}; "
            f"URDF 该设备可动关节={self.urdf_joints}."
            + (f" {self.detail}" if self.detail else "")
        )


def validate_contract(urdf_str: str, devices: dict, registry=None) -> list[ContractIssue]:
    """核对每个声明了 ``model.joints`` 的设备,其发布器关节名是否存在于 URDF。

    Args:
        urdf_str: xacro 展开后的最终 URDF 文本。
        devices: graph 设备字典 ``{node_id: {"id","type","class",...}}``
            (与 ``ResourceVisualization`` 入参一致)。
        registry: 注册表(测试可注入);None 时用全局 ``lab_registry``。

    Returns:
        所有不一致 issue 列表(空=通过)。未声明 joints 的设备跳过。
    """
    issues: list[ContractIssue] = []
    registry = _resolve_registry(registry)
    if registry is None or not devices:
        return issues
    for node in devices.values():
        if not isinstance(node, dict):
            continue
        if node.get("type") != "device" or not node.get("class"):
            continue
        node_id = node.get("id")
        if not node_id:
            continue
        joint_map = get_joint_map(node["class"], registry)
        if not joint_map:
            continue
        urdf_joints = extract_urdf_movable_joints(urdf_str, node_id)
        urdf_names = sorted(urdf_joints.keys())
        for sem, full in published_names(node_id, joint_map).items():
            if full not in urdf_joints:
                issues.append(ContractIssue(node_id, sem, full, "missing", urdf_names))
                continue
            want_type = joint_map[sem].get("type")
            if want_type and urdf_joints[full] != want_type:
                issues.append(
                    ContractIssue(
                        node_id,
                        sem,
                        full,
                        "type_mismatch",
                        urdf_names,
                        detail=f"期望 type={want_type}, URDF type={urdf_joints[full]}",
                    )
                )
    return issues
