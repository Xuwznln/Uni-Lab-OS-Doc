"""本地库存副作用提交前复验平台授权，跨库恢复继续使用库存原幂等身份。"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from unilabos.workflow.scheduling_local_authority import LocalControlAuthority
from unilabos.workflow.store import WorkflowStore


@contextmanager
def inventory_effect_transaction(
    store: WorkflowStore, inventory: Any, authority: LocalControlAuthority,
) -> Iterator[None]:
    """保持工作流授权不变，库存业务、幂等结果与事件在末次复验后一起提交。

    库存先提交；随后进程中断时，已存在的 command/source 身份供恢复读取，不推断
    两个数据库具有共同提交点。此作用域内只允许读取工作流事实，不嵌套工作流写事务。
    """
    with store.transaction() as connection:
        authority.before_effect(connection)
        with inventory.command_transaction():
            try:
                yield
            finally:
                # 领域拒绝也会生成等待/缺料事实，不能让过期的失败结果绕过复验。
                authority(connection)
