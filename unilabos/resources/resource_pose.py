"""资源与 Site 共用的静态 2D/3D 几何和布局描述。"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict


class ResourceDictPositionSizeType(TypedDict):
    depth: float
    width: float
    height: float


class ResourceDictPositionSize(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    depth: float = Field(description="Depth", default=0.0)  # z
    width: float = Field(description="Width", default=0.0)  # x
    height: float = Field(description="Height", default=0.0)  # y


class ResourceDictPositionScaleType(TypedDict):
    x: float
    y: float
    z: float


class ResourceDictPositionScale(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    x: float = Field(description="x scale", default=0.0)
    y: float = Field(description="y scale", default=0.0)
    z: float = Field(description="z scale", default=0.0)


class ResourceDictPositionObjectType(TypedDict):
    x: float
    y: float
    z: float


class ResourceDictPositionObject(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    x: float = Field(description="X coordinate", default=0.0)
    y: float = Field(description="Y coordinate", default=0.0)
    z: float = Field(description="Z coordinate", default=0.0)


class ResourceDictPositionType(TypedDict, total=False):
    """序列化/兼容输入形状；各项可省略，由 Pydantic 模型补默认值。"""

    size: ResourceDictPositionSizeType
    scale: ResourceDictPositionScaleType
    layout: Literal["2d", "x-y", "z-y", "x-z"]
    position: ResourceDictPositionObjectType
    position3d: ResourceDictPositionObjectType
    rotation: ResourceDictPositionObjectType
    cross_section_type: Literal["rectangle", "circle", "rounded_rectangle"]
    extra: Optional[Dict[str, Any]]


class ResourceDictPosition(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    size: ResourceDictPositionSize = Field(
        description="Resource size", default_factory=ResourceDictPositionSize
    )
    scale: ResourceDictPositionScale = Field(
        description="Resource scale", default_factory=ResourceDictPositionScale
    )
    layout: Literal["2d", "x-y", "z-y", "x-z"] = Field(
        description="Resource layout", default="x-y"
    )
    position: ResourceDictPositionObject = Field(
        description="Static 2D/layout position", default_factory=ResourceDictPositionObject
    )
    position3d: ResourceDictPositionObject = Field(
        description="Static position in 3D layout", default_factory=ResourceDictPositionObject
    )
    rotation: ResourceDictPositionObject = Field(
        description="Resource rotation", default_factory=ResourceDictPositionObject
    )
    cross_section_type: Literal["rectangle", "circle", "rounded_rectangle"] = Field(
        description="Cross section type", default="rectangle"
    )
    extra: Optional[Dict[str, Any]] = Field(description="Extra data", default=None)


__all__ = [
    "ResourceDictPosition",
    "ResourceDictPositionObject",
    "ResourceDictPositionObjectType",
    "ResourceDictPositionScale",
    "ResourceDictPositionScaleType",
    "ResourceDictPositionSize",
    "ResourceDictPositionSizeType",
    "ResourceDictPositionType",
]
