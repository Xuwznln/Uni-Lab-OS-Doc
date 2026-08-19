"""Site 模板定义与实例 Site 之间的适配。

``available_sites`` 属于设备/资源模板，只描述 Site 的固定规格，不携带实例
``uuid/material_uuid/occupied_material_uuid``。实例化设备或物料时，由微后端生成
最终身份；Edge 这里只规范化模板定义并校验实例快照，不补写身份。Site 的静态
规格变化必须显式迁移，占用变化走独立 place/clear 命令。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, TypeAlias, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationInfo,
    field_validator,
    model_validator,
)

from unilabos.resources.objects.pose import (
    ResourceDictPosition,
    _copy_mapping_payload,
    normalize_site_pose_payload,
)


class SiteDefinition(BaseModel):
    """不含实例身份的 Site 初始化定义。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    schema_version: Literal[1] = 1
    index: Union[int, str]
    label: str
    visible: bool = True
    pose: ResourceDictPosition = Field(default_factory=ResourceDictPosition)
    allowed_resource_categories: List[str] = Field(
        default_factory=list,
        description=(
            "ResourceTemplate category hints for frontend canvas filtering only; "
            "Edge and backend do not enforce compatibility"
        ),
    )
    parent_link: str = ""
    description: str = ""
    meta_data: Dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_nested_geometry(cls, value: Any):
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("Site 初始化定义必须是对象")

        return normalize_site_pose_payload(value)

    @field_validator("label")
    @classmethod
    def _require_label(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Site.label 不能为空")
        return value.strip()

    @field_validator("index")
    @classmethod
    def _validate_index(cls, value: Union[int, str]) -> Union[int, str]:
        if isinstance(value, bool):
            raise ValueError("Site.index 不能是布尔值")
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Site.index 不能为空")
        return value

    @field_validator("allowed_resource_categories")
    @classmethod
    def _normalize_string_list(cls, values: List[str], info: ValidationInfo) -> List[str]:
        result: List[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Site.{info.field_name} 只能包含非空字符串")
            normalized = value.strip()
            key = normalized.casefold()
            if key not in seen:
                result.append(normalized)
                seen.add(key)
        return result


SiteDefinitionInput: TypeAlias = Union[SiteDefinition, Mapping[str, Any]]
SITE_DEFINITION_FIELDS: tuple[str, ...] = tuple(SiteDefinition.model_fields)


def normalize_available_sites(
    value: Optional[Sequence[SiteDefinitionInput]],
) -> List[Dict[str, Any]]:
    """把装饰器/YAML 中的 ``available_sites`` 规范化为共用 pose 模型。"""

    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("available_sites 必须是数组")

    result: List[Dict[str, Any]] = []
    seen_indexes: set[tuple[str, Union[int, str]]] = set()
    seen_labels: set[str] = set()
    for ordinal, raw_site in enumerate(value):
        if isinstance(raw_site, SiteDefinition):
            site = raw_site
        else:
            if not isinstance(raw_site, Mapping):
                raise ValueError(f"available_sites[{ordinal}] 必须是对象")
            payload = _copy_mapping_payload(raw_site)
            payload.setdefault("index", ordinal)
            payload.setdefault("label", str(payload["index"]))
            site = SiteDefinition.model_validate(payload)

        index_key = (type(site.index).__name__, site.index)
        if index_key in seen_indexes:
            raise ValueError(f"available_sites 中存在重复 index: {site.index}")
        label_key = site.label.casefold()
        if label_key in seen_labels:
            raise ValueError(f"available_sites 中存在重复 label: {site.label}")
        seen_indexes.add(index_key)
        seen_labels.add(label_key)
        result.append(site.model_dump())
    return result


def _site_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    raise ValueError("实例 Site 必须是对象")


def _definition_from_instance(value: Any) -> Dict[str, Any]:
    payload = _site_payload(value)
    return SiteDefinition.model_validate(
        {key: payload[key] for key in SITE_DEFINITION_FIELDS if key in payload}
    ).model_dump()


def validate_instantiated_sites(
    definitions: Optional[Sequence[SiteDefinitionInput]],
    *,
    owner_uuid: str,
    template_name: str,
    current_sites: Optional[List[Any]] = None,
    sites_initialized: bool,
) -> List[Dict[str, Any]]:
    """校验微后端实例 Site 快照与 Registry 模板定义一致。

    Edge 不生成 Site UUID，也不把 Registry ``available_sites`` 复制进实例。
    ``sites_initialized=True`` 且 ``current_sites=[]`` 是权威空快照，必须保留；
    只有微后端 create/import/migration 边界可以把模板定义实例化为 Site。
    """

    normalized = normalize_available_sites(definitions)
    if not owner_uuid:
        raise ValueError("校验实例 Site 时 owner_uuid 不能为空")
    if not template_name:
        raise ValueError("校验实例 Site 时 template_name 不能为空")

    if not sites_initialized:
        if current_sites:
            raise ValueError("sites_initialized=false 时不能携带实例 Site")
        raise ValueError(
            f"资源 {owner_uuid} 的 Site 尚未由微后端实例化，Edge 不得本地补齐"
        )

    if current_sites is None:
        raise ValueError("sites_initialized=true 时 sites 必须是数组")

    existing_payloads = [_site_payload(site) for site in current_sites]
    if not existing_payloads:
        # 显式空数组是服务端权威事实；即使 Registry 后来出现定义也不能在 Edge 补写。
        return []
    if not normalized:
        raise ValueError(f"资源 {owner_uuid} 存在实例 Site，但 Registry 没有 available_sites 定义")

    existing_by_label = {
        str(site.get("label", "")).casefold(): site
        for site in existing_payloads
        if site.get("label")
    }
    existing_by_index = {
        (type(site.get("index")).__name__, site.get("index")): site
        for site in existing_payloads
        if site.get("index") is not None
    }
    used_site_ids: set[int] = set()
    result: List[Dict[str, Any]] = []

    for definition in normalized:
        existing = existing_by_label.get(str(definition["label"]).casefold())
        if existing is None:
            existing = existing_by_index.get(
                (type(definition["index"]).__name__, definition["index"])
            )

        if existing is None:
            raise ValueError(
                f"资源 {owner_uuid} 的实例快照缺少 available_sites 定义 {definition['label']}"
            )

        used_site_ids.add(id(existing))
        existing_definition = _definition_from_instance(existing)
        if existing_definition != definition:
            raise ValueError(
                f"设备 {owner_uuid} 的 Site {definition['label']} 固定定义与 available_sites 冲突"
            )
        existing_owner = existing.get("material_uuid")
        if existing_owner != owner_uuid:
            raise ValueError(
                f"Site {definition['label']} 的 material_uuid={existing_owner!r} 与 owner={owner_uuid!r} 冲突"
            )
        existing_template = existing.get("template_name")
        if existing_template != template_name:
            raise ValueError(
                f"Site {definition['label']} 的 template_name={existing_template!r} "
                f"与设备模板 {template_name!r} 冲突"
            )
        if not existing.get("uuid"):
            raise ValueError(f"Site {definition['label']} 缺少微后端分配的 UUID")
        result.append(copy.deepcopy(existing))

    unused = [site for site in existing_payloads if id(site) not in used_site_ids]
    if unused:
        labels = [site.get("label", site.get("index")) for site in unused]
        raise ValueError(f"资源 {owner_uuid} 存在 available_sites 未声明的实例 Site: {labels}")
    return result


__all__ = [
    "SITE_DEFINITION_FIELDS",
    "SiteDefinition",
    "SiteDefinitionInput",
    "normalize_site_pose_payload",
    "normalize_available_sites",
    "validate_instantiated_sites",
]
