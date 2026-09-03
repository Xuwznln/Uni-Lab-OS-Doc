"""实验室布局协议对象：区域 / 围墙像素格文档（``/api/v1/lab/layout``）。

布局叠在物料权威的设备位置之上：格子边长 ``cell_size`` 与 position 同单位，格子键
``"col,row"``（允许负数），格子左上角 = ``(col * cell_size, row * cell_size)``。一个格子
至多属于一个区域，区域格子与围墙格子互斥——服务端在写入时校验这些不变量，前端
``@openlab/protocol`` 的 lab-v1 域按同名字段消费。
"""

from __future__ import annotations

import re
from typing import List

from pydantic import Field, field_validator, model_validator

from unilabos.protocol.base import ServerObject

CELL_KEY_PATTERN = re.compile(r"^-?\d{1,6},-?\d{1,6}$")
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")

#: 单份布局的规模上限（防止误操作把几十万格子写进权威）。
MAX_ZONES = 200
MAX_CELLS = 100_000
DEFAULT_LAYOUT_KEY = "default"


def _validate_cells(cells: List[str], where: str) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for key in cells:
        if not isinstance(key, str) or CELL_KEY_PATTERN.fullmatch(key) is None:
            raise ValueError(f"{where} 含非法格子键 {key!r}（应为 'col,row' 整数对）")
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


class LabZone(ServerObject):
    """一个区域：稳定 id、展示名、颜色与所占格子。"""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#2e5bff")
    cells: List[str] = Field(default_factory=list)

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if COLOR_PATTERN.fullmatch(value) is None:
            raise ValueError("区域颜色必须是 #rrggbb")
        return value.lower()

    @field_validator("cells")
    @classmethod
    def _cells(cls, value: List[str]) -> List[str]:
        return _validate_cells(value, "区域格子")


class LabLayoutWrite(ServerObject):
    """``PUT /api/v1/lab/layout`` 请求体：整份替换 + revision 乐观锁。

    ``revision`` 是客户端读到的版本：从未保存过时为 0；不匹配返回 409。
    """

    revision: int = Field(ge=0)
    cell_size: float = Field(gt=0, le=100_000)
    zones: List[LabZone] = Field(default_factory=list)
    walls: List[str] = Field(default_factory=list)

    @field_validator("walls")
    @classmethod
    def _walls(cls, value: List[str]) -> List[str]:
        return _validate_cells(value, "围墙")

    @model_validator(mode="after")
    def _invariants(self) -> "LabLayoutWrite":
        if len(self.zones) > MAX_ZONES:
            raise ValueError(f"区域数不能超过 {MAX_ZONES}")
        ids = [zone.id for zone in self.zones]
        if len(set(ids)) != len(ids):
            raise ValueError("区域 id 重复")
        owner: dict[str, str] = {}
        total = len(self.walls)
        for zone in self.zones:
            total += len(zone.cells)
            for key in zone.cells:
                if key in owner:
                    raise ValueError(f"格子 {key} 同时属于区域 {owner[key]} 与 {zone.id}")
                owner[key] = zone.id
        for key in self.walls:
            if key in owner:
                raise ValueError(f"格子 {key} 既是围墙又属于区域 {owner[key]}")
        if total > MAX_CELLS:
            raise ValueError(f"格子总数不能超过 {MAX_CELLS}")
        return self


class LabLayoutRead(ServerObject):
    """``GET /api/v1/lab/layout`` 响应：从未保存时 ``revision = 0`` 且区域 / 围墙为空。"""

    layout_key: str
    revision: int = Field(ge=0)
    cell_size: float = Field(gt=0)
    zones: List[LabZone] = Field(default_factory=list)
    walls: List[str] = Field(default_factory=list)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)


__all__ = [
    "DEFAULT_LAYOUT_KEY",
    "LabLayoutRead",
    "LabLayoutWrite",
    "LabZone",
    "MAX_CELLS",
    "MAX_ZONES",
]
