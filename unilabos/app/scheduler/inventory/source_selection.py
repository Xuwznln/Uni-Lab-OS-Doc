"""在既有来源准入事务内按部署 Site 层级选择物料，不维护额外分配账。"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from unilabos.app.scheduler.inventory.domain import (
    CommandRejected,
    InsufficientStock,
    MaterialClaimedByOtherTask,
    MaterialRequirement,
)


def claimed_by_other_task(
    connection: sqlite3.Connection,
    instance_uuids: list[str],
    workflow_id: str | None,
) -> bool:
    """判断候选物料是否只是被其他任务预留或绑定（技术方案 4.2：不可用不等于缺料）。"""

    if not instance_uuids or not workflow_id:
        # 不知道请求方任务身份时无法区分“别人占用”与“自己占用”，保持缺料语义，
        # 避免任务等待自己的预留。
        return False
    placeholders = ",".join("?" for _ in instance_uuids)
    owner = workflow_id
    row = connection.execute(
        f"""SELECT 1 FROM material_instance AS i WHERE i.edge_uuid IN ({placeholders})
        AND (EXISTS (SELECT 1 FROM inventory_reservation AS r,
                     json_each(r.amounts_json, '$.instances') AS reserved
                     WHERE r.status='active' AND reserved.value=i.edge_uuid
                       AND r.workflow_id<>?)
             OR EXISTS (SELECT 1 FROM inventory_material_source_binding AS b
                        WHERE b.material_uuid=i.edge_uuid AND b.status='active'
                          AND b.workflow_id<>?))
        LIMIT 1""",
        (*instance_uuids, owner, owner),
    ).fetchone()
    return row is not None


def select_descendant_instance(
    connection: sqlite3.Connection,
    requirement: MaterialRequirement,
    *,
    selected_sites: list[str],
    exclude_active_shared: bool,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """深度优先按各层 Site 顺序选料；根库位约束限定完整子树。

    只遍历权威 Site 实际占位。离架、在途和缺失物料不在候选集合；任意循环或
    过深层级失败关闭。返回实例后由调用方原有预留事务核对状态、模板与占用。
    """
    rows = connection.execute(
        """WITH RECURSIVE mounts(uuid) AS (
            SELECT uuid FROM material WHERE uuid=? AND deleted_at IS NULL
            UNION
            SELECT s.occupied_material_uuid FROM site s JOIN mounts p ON p.uuid=s.material_uuid
            JOIN material m ON m.uuid=s.occupied_material_uuid AND m.deleted_at IS NULL
            WHERE s.deleted_at IS NULL
        )
        SELECT s.*, m.resource_template_uuid AS child_template,
               i.status AS instance_status,
               EXISTS(SELECT 1 FROM inventory_material_source_binding b
                 WHERE b.material_uuid=m.uuid AND b.custody_policy='shared_source'
                   AND b.status='active') AS shared_binding
        FROM site s JOIN mounts p ON p.uuid=s.material_uuid
        JOIN material m ON m.uuid=s.occupied_material_uuid AND m.deleted_at IS NULL
        LEFT JOIN material_instance i ON i.edge_uuid=m.uuid
        WHERE s.deleted_at IS NULL
        ORDER BY s.sort_order, s.create_time, s.uuid, m.create_time, m.uuid""",
        (requirement.mount_uuid,),
    ).fetchall()
    by_parent: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_parent[row["material_uuid"]].append(row)
    candidates: list[str] = []
    # ``claimable`` 是模板匹配、仍在库或已预留的物料，用于区分“被占用”与“缺料”。
    claimable: list[str] = []

    def visit(parent: str, ancestors: tuple[str, ...]) -> None:
        if parent in ancestors or len(ancestors) >= 64:
            raise CommandRejected("物料选料层级存在循环或超过 64 层")
        for row in by_parent.get(parent, ()):
            if (
                parent == requirement.mount_uuid
                and selected_sites
                and row["uuid"] not in selected_sites
            ):
                continue
            child = row["occupied_material_uuid"]
            if (
                row["child_template"] == requirement.template_id
                and row["instance_status"] == "warehouse"
                and not (exclude_active_shared and row["shared_binding"])
            ):
                candidates.append(child)
            elif (
                row["child_template"] == requirement.template_id
                and row["instance_status"] in {"warehouse", "reserved"}
            ):
                claimable.append(child)
            visit(child, (*ancestors, parent))

    visit(requirement.mount_uuid, ())
    if not candidates:
        if claimed_by_other_task(connection, claimable, workflow_id):
            raise MaterialClaimedByOtherTask(
                f"挂载资源 {requirement.mount_uuid} 的匹配物料正被其他任务占用，等待释放"
            )
        raise InsufficientStock(
            f"挂载资源 {requirement.mount_uuid} 的可达库位没有可用物料"
        )
    return dict(
        connection.execute(
            "SELECT * FROM material_instance WHERE edge_uuid=?", (candidates[0],)
        ).fetchone()
    )
