"""Registry Authority 协议对象：条目状态、挂起冲突与上报批次统计。

Edge 全量上报后，每个模板条目独立维护版本；被活跃 workflow 节点引用的
action 发生删除/变化时条目挂起（pending），由前端"升级"按钮确认。本模块
是 backend API 与 edge-ui 之间的冻结契约：服务端构造这些模型后输出，
前端 `@openlab/protocol` 的 registry 域按同名字段消费。
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import Field

from unilabos.protocol.base import ServerObject

#: 条目冲突原因：候选版本删除或修改了被引用的 action。
RegistryConflictReason = Literal["action-removed", "action-changed"]

#: 条目组合状态标签（一个条目可同时携带多个，如 active+pending）。
RegistryEntryStatus = Literal["active", "pending", "removed", "unusable"]


class RegistryConflict(ServerObject):
    """挂起冲突明细：哪个 action、因何冲突。"""

    action: str
    reason: RegistryConflictReason


class RegistryEntrySummary(ServerObject):
    """条目状态行（列表/详情共用）。"""

    name: str
    template_uuid: str
    active_version: Optional[int] = None
    pending_version: Optional[int] = None
    pending_conflicts: List[RegistryConflict] = Field(default_factory=list)
    unusable_reason: str = ""
    removed_at_ms: Optional[int] = None
    updated_at_ms: int = 0
    status: List[RegistryEntryStatus] = Field(default_factory=list)


class RegistryAffectedNode(ServerObject):
    """被挂起条目影响的 workflow 画布节点（前端徽标定位依据）。"""

    workflow_uuid: str
    workflow_name: str
    node_uuid: str
    node_name: str
    action: str


class RegistryPendingImpact(ServerObject):
    """一个挂起条目的影响面：冲突明细 + 受影响节点清单。"""

    name: str
    template_uuid: str
    active_version: Optional[int] = None
    pending_version: int
    conflicts: List[RegistryConflict] = Field(default_factory=list)
    affected_nodes: List[RegistryAffectedNode] = Field(default_factory=list)


class RegistryPendingItem(ServerObject):
    """上报批次里新挂起的条目明细。"""

    name: str
    conflicts: List[RegistryConflict] = Field(default_factory=list)


class RegistryUnusableItem(ServerObject):
    """上报批次里的不可用定义（id 缺失/类型非法等，不进版本历史）。"""

    id: str = ""
    reason: str


class RegistryReportCounts(ServerObject):
    """上报批次计数。"""

    total: int = Field(ge=0)
    added: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    revived: int = Field(default=0, ge=0)
    unusable: int = Field(default=0, ge=0)


class RegistryReportSummary(ServerObject):
    """一次 Edge 全量上报的批次统计（计数 + 明细）。"""

    counts: RegistryReportCounts
    added: List[str] = Field(default_factory=list)
    updated: List[str] = Field(default_factory=list)
    pending: List[RegistryPendingItem] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    revived: List[str] = Field(default_factory=list)
    unusable: List[RegistryUnusableItem] = Field(default_factory=list)


__all__ = [
    "RegistryAffectedNode",
    "RegistryConflict",
    "RegistryConflictReason",
    "RegistryEntryStatus",
    "RegistryEntrySummary",
    "RegistryPendingImpact",
    "RegistryPendingItem",
    "RegistryReportCounts",
    "RegistryReportSummary",
    "RegistryUnusableItem",
]
