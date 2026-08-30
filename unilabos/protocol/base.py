"""协议层基础件：严格 DTO 基类、共享标量类型与规范 JSON 工具。

协议对象与微后端 SQLModel 表共用这些定义；表侧经
``unilabos.server.database.tables.base`` 转发引用，保证两侧约束一致。
本模块不依赖 ``unilabos.server``，契约层可独立加载。
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Dict, List

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints
from sqlalchemy import Integer, Text
from sqlmodel import Field


NonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(sa_type=Text),
]
UnixMilliseconds = Annotated[int, Field(ge=0, sa_type=Integer)]
PositiveVersion = Annotated[int, Field(ge=1, sa_type=Integer)]
JsonObject = Dict[str, JsonValue]
JsonArray = List[JsonValue]


class ServerObject(BaseModel):
    """协议 DTO 和内嵌值对象的严格 Pydantic 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
        protected_namespaces=(),
    )


def canonical_json(value: Any) -> str:
    """返回跨进程稳定的 JSON；哈希、幂等请求和快照均使用这一实现。"""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "JsonArray",
    "JsonObject",
    "NonEmptyStr",
    "PositiveVersion",
    "ServerObject",
    "UnixMilliseconds",
    "canonical_hash",
    "canonical_json",
]
