"""UniLabOS 唯一的 canonical Site 实例模型。"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import Field, JsonValue, ValidationInfo, field_validator, model_validator
from typing_extensions import NotRequired, TypedDict

from unilabos.resources.objects.base import ResourceObject
from unilabos.resources.objects.pose import (
    ResourceDictPosition,
    ResourceDictPositionType,
    normalize_site_pose_payload,
)


class ResourceSiteType(TypedDict):
    """物料根字段 ``sites`` 中的规范 Site 结构。"""

    schema_version: Literal[1]
    uuid: str
    template_name: str
    material_uuid: str
    index: Union[int, str]
    label: str
    visible: NotRequired[bool]
    occupied_material_uuid: NotRequired[Optional[str]]
    pose: NotRequired[ResourceDictPositionType]
    allowed_resource_categories: NotRequired[List[str]]
    parent_link: NotRequired[str]
    description: NotRequired[str]
    meta_data: NotRequired[Dict[str, Any]]
    extra: NotRequired[Dict[str, Any]]


class ResourceSite(ResourceObject):
    """Edge、微后端和 PLR Adapter 共用的唯一 Site 实例模型。

    PLR 特有但需要往返保留的字段进入显式 ``extra``；canonical v1 仍拒绝
    未声明的顶层字段，避免 Adapter 形状反向污染微后端协议。
    """

    schema_version: Literal[1] = 1
    uuid: str
    template_name: str
    material_uuid: str
    index: Union[int, str]
    label: str
    visible: bool = True
    occupied_material_uuid: Optional[str] = None
    pose: ResourceDictPosition = Field(default_factory=ResourceDictPosition)
    allowed_resource_categories: List[str] = Field(
        default_factory=list,
        description=(
            "ResourceTemplate category hints for frontend canvas filtering only; "
            "not an Edge/backend mount constraint"
        ),
    )
    parent_link: str = ""
    description: str = ""
    meta_data: Dict[str, JsonValue] = Field(default_factory=dict)
    extra: Dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_shape(cls, value: Any, info: ValidationInfo):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError("Site 必须是对象")

        site = normalize_site_pose_payload(value)
        is_legacy = "schema_version" not in site

        if "occupied_by" in site:
            raise ValueError(
                "Site.occupied_by 已停用；请直接提供 occupied_material_uuid"
            )

        # 旧协议的未知顶层字段继续显式归档；规范 v1 输入只允许声明过的字段。
        known = set(cls.model_fields)
        if is_legacy:
            legacy_fields = {
                key: site.pop(key) for key in list(site) if key not in known
            }
            if legacy_fields:
                metadata = site.get("meta_data")
                if metadata is None:
                    metadata = {}
                if not isinstance(metadata, dict):
                    raise ValueError("Site.meta_data 必须是对象")
                metadata = copy.deepcopy(metadata)
                existing = metadata.get("legacy_fields")
                if existing is not None and not isinstance(existing, dict):
                    raise ValueError("Site.meta_data.legacy_fields 必须是对象")
                metadata["legacy_fields"] = {**(existing or {}), **legacy_fields}
                site["meta_data"] = metadata

        return site

    @field_validator("uuid", "template_name", "material_uuid", "label")
    @classmethod
    def _require_non_empty_string(cls, value: str, info: ValidationInfo) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Site.{info.field_name} 不能为空")
        return value.strip()

    @field_validator("occupied_material_uuid")
    @classmethod
    def _normalize_occupied_uuid(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Site.occupied_material_uuid 必须是非空字符串或 null")
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
    def _normalize_string_list(
        cls, values: List[str], info: ValidationInfo
    ) -> List[str]:
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

    @model_validator(mode="after")
    def _validate_references(self) -> "ResourceSite":
        if self.occupied_material_uuid == self.material_uuid:
            raise ValueError("Site 不能承载拥有该 Site 的物料本身")
        return self

__all__ = ["ResourceSite", "ResourceSiteType"]
