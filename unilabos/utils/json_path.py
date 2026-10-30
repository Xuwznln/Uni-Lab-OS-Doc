"""无执行依赖的 JSON 路径读取，供参数解析与估时模型共用。"""

from __future__ import annotations

from typing import Any, List, Tuple

_MISSING = object()


def split_path(path: str) -> List[str]:
    """按未转义的 ``.`` 分段，支持 ``\\.`` 转义（gjson 语义）。"""
    segments: List[str] = []
    cur: List[str] = []
    escaped = False
    for ch in path:
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ".":
            segments.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    segments.append("".join(cur))
    return segments


def _get_segment(value: Any, segment: str) -> Any:
    if isinstance(value, dict):
        return value.get(segment, _MISSING)
    if isinstance(value, list):
        if segment == "#":
            return len(value)
        if segment.isdigit():
            idx = int(segment)
            return value[idx] if idx < len(value) else _MISSING
        return _MISSING
    return _MISSING


def json_get(value: Any, path: str) -> Any:
    """gjson.Get 等价：路径不存在时返回 _MISSING 哨兵（调用方判 exists）。"""
    if path == "":
        return _MISSING
    cur = value
    for segment in split_path(path):
        cur = _get_segment(cur, segment)
        if cur is _MISSING:
            return _MISSING
    return cur


def json_get_exists(value: Any, path: str) -> Tuple[bool, Any]:
    res = json_get(value, path)
    if res is _MISSING:
        return False, None
    return True, res
