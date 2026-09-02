"""Backend-shaped Workflow 与 Authoring 事实的 SQLite Authority。"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple
from uuid import uuid4

from unilabos.protocol.utils.json_codec import decode_json_bytes, encode_json
from unilabos.protocol.runtime.workflow import WorkflowEdgeWrite, WorkflowNodeWrite
from unilabos.protocol.utils.workflow_validation import (
    GraphValidationError,
    MissingTemplateError,
    validate_graph,
)
from unilabos.server.services.runtime.workflow.errors import (
    StoreAuthoringConflict,
    StoreConflict,
    StoreNotFound,
    StoreRevisionConflict,
)
from unilabos.server.database.schema import initialize_database
from unilabos.server.database.sqlite_domain import SqliteDomain
from unilabos.server.database.tables.runtime import RUNTIME_DATABASE

_STORE_SQLITE_BUSY_TIMEOUT_MS = 5000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return encode_json(value, sort_keys=True).decode("utf-8")


def _load(value: Optional[str], fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    return decode_json_bytes(value.encode("utf-8"))


class WorkflowStore:
    """SQLite Workflow Authority（表落 ``runtime.db``）。

    ``WorkflowService`` 直接继承本类：持连接、写事务与业务 API 在同一个
    实例上。Store 方法用一个进程内可重入锁串行化事务。

    生产组合根把 RuntimeService 实例作为 ``database`` 传入，共享其
    connection 与 write_lock（同库单连接单写者）；测试可传路径或
    ``:memory:`` 独立建库。
    """

    def __init__(
        self,
        database: "str | Path | sqlite3.Connection | SqliteDomain | WorkflowStore",
        *,
        lock: Optional[threading.RLock] = None,
    ):
        if isinstance(database, (SqliteDomain, WorkflowStore)):
            # 同库共存域：共享宿主的连接与写锁
            if lock is None:
                lock = database.write_lock
            database = database.connection
        self._lock = lock if lock is not None else threading.RLock()
        if isinstance(database, sqlite3.Connection):
            self.path = ""
            self._conn = database
            self._owns_connection = False
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        else:
            self.path = str(database)
            self._owns_connection = True
            if self.path == ":memory:":
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA foreign_keys = ON")
                with self._conn:
                    for statement in RUNTIME_DATABASE.statements():
                        self._conn.execute(statement)
            else:
                self._conn = initialize_database(Path(self.path), RUNTIME_DATABASE)
            self._conn.execute(
                f"PRAGMA busy_timeout = {_STORE_SQLITE_BUSY_TIMEOUT_MS}"
            )

    @property
    def connection(self) -> sqlite3.Connection:
        """底层 SQLite 连接（与其他域 Service 的属性面一致）。"""

        return self._conn

    @property
    def write_lock(self) -> threading.RLock:
        """runtime.db 的进程内唯一写锁（同库共存域共享同一实例）。"""

        return self._lock

    def close(self) -> None:
        with self._lock:
            if self._owns_connection:
                self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    # Workflow 与 Graph --------------------------------------------------

    def create_workflow(
        self,
        *,
        workflow_uuid: str,
        name: str,
        tags: List[Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = utc_now()
        try:
            with self.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO workflow(
                        uuid, create_time, update_time, deleted_at,
                        description, meta_data, name, tags, revision
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 1)
                    """,
                    (
                        workflow_uuid,
                        now,
                        now,
                        description,
                        _json(meta_data),
                        name,
                        _json(tags),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StoreConflict(f"workflow {workflow_uuid} already exists") from exc
        return WorkflowStore.get_workflow(self, workflow_uuid)

    def get_workflow(
        self,
        workflow_uuid: str,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        database = conn or self._conn
        with self._lock:
            row = database.execute(
                "SELECT * FROM workflow WHERE uuid = ? AND deleted_at IS NULL",
                (workflow_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"workflow {workflow_uuid} not found")
        return self._workflow_row(row)

    def list_workflows(
        self,
        *,
        page: int,
        page_size: int,
        name: str = "",
    ) -> Dict[str, Any]:
        where = "deleted_at IS NULL"
        values: List[Any] = []
        if name:
            where += " AND name LIKE ?"
            values.append(f"%{name}%")
        offset = (page - 1) * page_size
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM workflow WHERE {where}",
                values,
            ).fetchone()[0]
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow WHERE {where}
                ORDER BY create_time DESC, uuid
                LIMIT ? OFFSET ?
                """,
                (*values, page_size, offset),
            ).fetchall()
        return {
            "items": [self._workflow_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def update_workflow(
        self,
        workflow_uuid: str,
        *,
        name: str,
        tags: List[Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self.transaction() as conn:
            WorkflowStore.get_workflow(self, workflow_uuid, conn=conn)
            conn.execute(
                """
                UPDATE workflow
                SET name = ?, tags = ?, description = ?, meta_data = ?,
                    update_time = ?
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (
                    name,
                    _json(tags),
                    description,
                    _json(meta_data),
                    utc_now(),
                    workflow_uuid,
                ),
            )
        return WorkflowStore.get_workflow(self, workflow_uuid)

    def delete_workflow(self, workflow_uuid: str) -> None:
        now = utc_now()
        with self.transaction() as conn:
            WorkflowStore.get_workflow(self, workflow_uuid, conn=conn)
            conn.execute(
                "UPDATE workflow SET deleted_at = ?, update_time = ? WHERE uuid = ?",
                (now, now, workflow_uuid),
            )
            conn.execute(
                "UPDATE workflow_node SET deleted_at = ?, update_time = ? "
                "WHERE workflow_uuid = ? AND deleted_at IS NULL",
                (now, now, workflow_uuid),
            )
            conn.execute(
                "UPDATE workflow_edge SET deleted_at = ?, update_time = ? "
                "WHERE workflow_uuid = ? AND deleted_at IS NULL",
                (now, now, workflow_uuid),
            )

    def get_graph(
        self,
        workflow_uuid: str,
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        database = conn or self._conn
        workflow = WorkflowStore.get_workflow(self, workflow_uuid, conn=database)
        with self._lock:
            node_rows = database.execute(
                """
                SELECT * FROM workflow_node
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                ORDER BY create_time, uuid
                """,
                (workflow_uuid,),
            ).fetchall()
            edge_rows = database.execute(
                """
                SELECT * FROM workflow_edge
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                ORDER BY create_time, uuid
                """,
                (workflow_uuid,),
            ).fetchall()
            template_uuids = [
                row["workflow_node_template_uuid"]
                for row in node_rows
                if row["workflow_node_template_uuid"]
            ]
            node_templates: List[Dict[str, Any]] = []
            handle_templates: List[Dict[str, Any]] = []
            if template_uuids:
                marks = ",".join("?" for _ in template_uuids)
                template_rows = database.execute(
                    f"""
                    SELECT * FROM workflow_node_template
                    WHERE uuid IN ({marks}) AND deleted_at IS NULL
                    ORDER BY create_time, uuid
                    """,
                    template_uuids,
                ).fetchall()
                handle_rows = database.execute(
                    f"""
                    SELECT * FROM workflow_handle_template
                    WHERE workflow_node_template_uuid IN ({marks})
                      AND deleted_at IS NULL
                    ORDER BY create_time, uuid
                    """,
                    template_uuids,
                ).fetchall()
                node_templates = [self._node_template_row(row) for row in template_rows]
                handle_templates = [
                    self._handle_template_row(row) for row in handle_rows
                ]
        return {
            "workflow": workflow,
            "nodes": [self._node_row(row) for row in node_rows],
            "edges": [self._edge_row(row) for row in edge_rows],
            "node_templates": node_templates,
            "handle_templates": handle_templates,
        }

    def get_published_workflow_snapshot(
        self,
        workflow_uuid: str,
    ) -> Dict[str, Any]:
        """在同一 SQLite 锁视图中冻结应用图和 Authoring 源码事实。"""

        with self._lock:
            graph = WorkflowStore.get_graph(self, workflow_uuid, conn=self._conn)
            row = self._conn.execute(
                "SELECT applied_source FROM workflow_authoring "
                "WHERE workflow_uuid = ?",
                (workflow_uuid,),
            ).fetchone()
            applied_source = (
                _load(row["applied_source"], None) if row is not None else None
            )
            return {**graph, "applied_source": applied_source}

    def save_graph(
        self,
        workflow_uuid: str,
        *,
        revision: int,
        nodes: List[WorkflowNodeWrite],
        edges: List[WorkflowEdgeWrite],
        protect_reserved_metadata: bool = False,
    ) -> Dict[str, Any]:
        with self.transaction() as conn:
            self._reconcile_graph(
                conn,
                workflow_uuid=workflow_uuid,
                expected_revision=revision,
                nodes=nodes,
                edges=edges,
                advance_revision=True,
                protect_reserved_metadata=protect_reserved_metadata,
            )
        return WorkflowStore.get_graph(self, workflow_uuid)

    def _reconcile_graph(
        self,
        conn: sqlite3.Connection,
        *,
        workflow_uuid: str,
        expected_revision: int,
        nodes: List[WorkflowNodeWrite],
        edges: List[WorkflowEdgeWrite],
        advance_revision: bool,
        protect_reserved_metadata: bool = False,
        semantic_workflow_meta_data: Optional[Dict[str, Any]] = None,
    ) -> int:
        workflow = WorkflowStore.get_workflow(self, workflow_uuid, conn=conn)
        if workflow["revision"] != expected_revision:
            raise StoreRevisionConflict(
                f"workflow revision {workflow['revision']} does not match "
                f"expected {expected_revision}"
            )
        node_by_uuid = {node.uuid: node for node in nodes}
        edge_by_uuid = {edge.uuid: edge for edge in edges}
        if len(node_by_uuid) != len(nodes):
            raise StoreConflict("duplicate workflow node UUID")
        if len(edge_by_uuid) != len(edges):
            raise StoreConflict("duplicate workflow edge UUID")
        for edge in edges:
            if (
                edge.source_node_uuid not in node_by_uuid
                or edge.target_node_uuid not in node_by_uuid
            ):
                raise StoreConflict(
                    f"edge {edge.uuid} references a node outside the submitted graph"
                )
        template_uuids = sorted(
            {
                node.workflow_node_template_uuid
                for node in nodes
                if node.workflow_node_template_uuid is not None
            }
        )
        templates: Dict[str, Dict[str, Any]] = {}
        handles: Dict[str, Dict[str, Any]] = {}
        if template_uuids:
            marks = ",".join("?" for _ in template_uuids)
            template_rows = conn.execute(
                f"""
                SELECT * FROM workflow_node_template
                WHERE uuid IN ({marks}) AND deleted_at IS NULL
                """,
                template_uuids,
            ).fetchall()
            templates = {
                row["uuid"]: self._node_template_row(row) for row in template_rows
            }
            handle_rows = conn.execute(
                f"""
                SELECT * FROM workflow_handle_template
                WHERE workflow_node_template_uuid IN ({marks})
                  AND deleted_at IS NULL
                """,
                template_uuids,
            ).fetchall()
            handles = {
                row["uuid"]: self._handle_template_row(row) for row in handle_rows
            }
        effective_params = {
            node.uuid: self._graph_node_param(conn, node) for node in nodes
        }
        effective_node_meta_data: Dict[str, Dict[str, Any]] = {}
        for node in nodes:
            existing_node = conn.execute(
                "SELECT meta_data FROM workflow_node WHERE uuid = ?",
                (node.uuid,),
            ).fetchone()
            effective_node_meta_data[node.uuid] = self._protected_metadata(
                node.meta_data,
                (existing_node["meta_data"] if existing_node is not None else None),
                enabled=protect_reserved_metadata,
            )
        try:
            validate_graph(
                nodes=nodes,
                edges=edges,
                templates=templates,
                handles=handles,
                effective_params=effective_params,
                workflow_meta_data=(
                    semantic_workflow_meta_data
                    if semantic_workflow_meta_data is not None
                    else workflow["meta_data"]
                ),
                node_meta_data=effective_node_meta_data,
            )
        except MissingTemplateError as exc:
            raise StoreNotFound(str(exc)) from exc
        except GraphValidationError as exc:
            raise StoreConflict(str(exc)) from exc
        now = utc_now()
        for node in nodes:
            self._upsert_node(
                conn,
                workflow_uuid,
                node,
                now,
                protect_reserved_metadata=protect_reserved_metadata,
            )
        for edge in edges:
            self._upsert_edge(
                conn,
                workflow_uuid,
                edge,
                now,
                protect_reserved_metadata=protect_reserved_metadata,
            )
        self._soft_delete_omitted(
            conn,
            table="workflow_edge",
            workflow_uuid=workflow_uuid,
            retained=edge_by_uuid,
            now=now,
        )
        self._soft_delete_omitted(
            conn,
            table="workflow_node",
            workflow_uuid=workflow_uuid,
            retained=node_by_uuid,
            now=now,
        )
        next_revision = expected_revision + 1 if advance_revision else expected_revision
        conn.execute(
            "UPDATE workflow SET revision = ?, update_time = ? "
            "WHERE uuid = ? AND deleted_at IS NULL",
            (next_revision, now, workflow_uuid),
        )
        return next_revision

    def _upsert_node(
        self,
        conn: sqlite3.Connection,
        workflow_uuid: str,
        node: WorkflowNodeWrite,
        now: str,
        *,
        protect_reserved_metadata: bool,
    ) -> None:
        existing = conn.execute(
            "SELECT workflow_uuid, create_time, meta_data "
            "FROM workflow_node WHERE uuid = ?",
            (node.uuid,),
        ).fetchone()
        if existing is not None and existing["workflow_uuid"] != workflow_uuid:
            raise StoreConflict(
                f"workflow node {node.uuid} belongs to another workflow"
            )
        meta_data = self._protected_metadata(
            node.meta_data,
            existing["meta_data"] if existing is not None else None,
            enabled=protect_reserved_metadata,
        )
        values = (
            node.description,
            _json(meta_data),
            workflow_uuid,
            node.workflow_node_template_uuid,
            node.parent_uuid,
            node.material_uuid,
            node.name,
            node.status,
            node.type,
            node.icon,
            _json(node.pose),
            _json(self._graph_node_param(conn, node)),
            node.footer,
            node.action_name,
            node.action_type,
            _json(node.execution_policy),
            int(node.disabled),
            int(node.minimized),
            node.script,
        )
        if existing is None:
            conn.execute(
                """
                INSERT INTO workflow_node(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_uuid, workflow_node_template_uuid,
                    parent_uuid, material_uuid, name, status, type, icon, pose,
                    param, footer, action_name, action_type, execution_policy,
                    disabled, minimized, script
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?)
                """,
                (node.uuid, now, now, *values),
            )
            return
        conn.execute(
            """
            UPDATE workflow_node
            SET update_time = ?, deleted_at = NULL, description = ?,
                meta_data = ?, workflow_uuid = ?,
                workflow_node_template_uuid = ?, parent_uuid = ?,
                material_uuid = ?, name = ?, status = ?, type = ?, icon = ?,
                pose = ?, param = ?, footer = ?, action_name = ?,
                action_type = ?, execution_policy = ?, disabled = ?,
                minimized = ?, script = ?
            WHERE uuid = ?
            """,
            (now, *values, node.uuid),
        )

    @staticmethod
    def _graph_node_param(
        conn: sqlite3.Connection,
        node: WorkflowNodeWrite,
    ) -> Dict[str, Any]:
        if node.param is not None:
            return node.param
        if node.workflow_node_template_uuid is None:
            return {}
        template = conn.execute(
            """
            SELECT goal_default, goal
            FROM workflow_node_template
            WHERE uuid = ? AND deleted_at IS NULL
            """,
            (node.workflow_node_template_uuid,),
        ).fetchone()
        if template is None:
            return {}
        for field in ("goal_default", "goal"):
            fallback = _load(template[field], {})
            if isinstance(fallback, dict) and fallback:
                return fallback
        return {}

    def _upsert_edge(
        self,
        conn: sqlite3.Connection,
        workflow_uuid: str,
        edge: WorkflowEdgeWrite,
        now: str,
        *,
        protect_reserved_metadata: bool,
    ) -> None:
        existing = conn.execute(
            "SELECT workflow_uuid, meta_data FROM workflow_edge WHERE uuid = ?",
            (edge.uuid,),
        ).fetchone()
        if existing is not None and existing["workflow_uuid"] != workflow_uuid:
            raise StoreConflict(
                f"workflow edge {edge.uuid} belongs to another workflow"
            )
        meta_data = self._protected_metadata(
            edge.meta_data,
            existing["meta_data"] if existing is not None else None,
            enabled=protect_reserved_metadata,
        )
        values = (
            edge.description,
            _json(meta_data),
            workflow_uuid,
            edge.source_node_uuid,
            edge.target_node_uuid,
            edge.source_handle_uuid,
            edge.target_handle_uuid,
        )
        if existing is None:
            conn.execute(
                """
                INSERT INTO workflow_edge(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_uuid, source_node_uuid,
                    target_node_uuid, source_handle_uuid, target_handle_uuid
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (edge.uuid, now, now, *values),
            )
            return
        conn.execute(
            """
            UPDATE workflow_edge
            SET update_time = ?, deleted_at = NULL, description = ?,
                meta_data = ?, workflow_uuid = ?, source_node_uuid = ?,
                target_node_uuid = ?, source_handle_uuid = ?,
                target_handle_uuid = ?
            WHERE uuid = ?
            """,
            (now, *values, edge.uuid),
        )

    @staticmethod
    def _protected_metadata(
        submitted: Dict[str, Any],
        existing_json: Optional[str],
        *,
        enabled: bool,
    ) -> Dict[str, Any]:
        result = dict(submitted)
        if not enabled:
            return result
        result.pop("unilab", None)
        existing = _load(existing_json, {}) if existing_json is not None else {}
        if isinstance(existing, dict) and "unilab" in existing:
            result["unilab"] = existing["unilab"]
        return result

    @staticmethod
    def _soft_delete_omitted(
        conn: sqlite3.Connection,
        *,
        table: str,
        workflow_uuid: str,
        retained: Iterable[str],
        now: str,
    ) -> None:
        retained_values = list(retained)
        if retained_values:
            marks = ",".join("?" for _ in retained_values)
            conn.execute(
                f"""
                UPDATE {table}
                SET deleted_at = ?, update_time = ?
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                  AND uuid NOT IN ({marks})
                """,
                (now, now, workflow_uuid, *retained_values),
            )
        else:
            conn.execute(
                f"""
                UPDATE {table}
                SET deleted_at = ?, update_time = ?
                WHERE workflow_uuid = ? AND deleted_at IS NULL
                """,
                (now, now, workflow_uuid),
            )

    # Task 与 Job --------------------------------------------------------

    def create_task_with_jobs(
        self,
        *,
        workflow_uuid: str,
        task_uuid: str,
        run_mode: str,
        target_node_uuid: Optional[str],
        input_value: Dict[str, Any],
        description: Optional[str],
        meta_data: Dict[str, Any],
        plan_builder: Callable[
            [Dict[str, Any]], Tuple[Dict[str, Any], List[Dict[str, Any]]]
        ],
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.transaction() as conn:
            graph = WorkflowStore.get_graph(self, workflow_uuid, conn=conn)
            plan, jobs = plan_builder(graph)
            effective_run_mode = str(plan["run_mode"])
            effective_target = plan.get("target_node_uuid")
            control_status = "paused" if effective_run_mode == "step" else "active"
            conn.execute(
                """
                INSERT INTO workflow_task(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_uuid, status, workflow_snapshot,
                    execution_plan, run_mode, target_node_uuid, control_status,
                    cleanup_status, trace_context, input, output, error_info
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, 'pending', ?, ?, ?, ?, ?,
                          'none', '{}', ?, '{}', '[]')
                """,
                (
                    task_uuid,
                    now,
                    now,
                    description,
                    _json(meta_data),
                    workflow_uuid,
                    _json(graph),
                    _json(plan),
                    effective_run_mode,
                    effective_target,
                    control_status,
                    _json(input_value),
                ),
            )
            for spec in jobs:
                self._insert_node_run(conn, task_uuid=task_uuid, spec=spec, now=now)
        return self.get_task(task_uuid)

    # 节点运行（run）与 attempt（job）-------------------------------------------

    _RUN_TERMINAL = frozenset({"succeeded", "failed", "skipped", "canceled", "timeout"})
    _RUN_IN_FLIGHT = frozenset(
        {"dispatched", "running", "intervention_required", "cancel_requested", "execution_unknown"}
    )

    def _insert_node_run(
        self,
        conn: sqlite3.Connection,
        *,
        task_uuid: str,
        spec: Dict[str, Any],
        now: str,
    ) -> str:
        """写入一个节点运行及其 attempt 1；整图任务与单点任务共用。

        ``spec["uuid"]`` 是节点运行（DAG 节点）的稳定身份，attempt 另取新 uuid。
        """

        run_uuid = str(spec["uuid"])
        job_uuid = str(uuid4())
        conn.execute(
            """
            INSERT INTO workflow_node_run(
                uuid, create_time, update_time, deleted_at, description, meta_data,
                workflow_task_uuid, workflow_node_uuid, topological_index,
                executor_kind, execution_policy, execution_timeout_seconds, param,
                material_uuid, status, current_job_uuid, attempt_count,
                return_info, error_info, feedback_data, control_data
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 1,
                      '{}', '[]', '{}', '{}')
            """,
            (
                run_uuid,
                now,
                now,
                task_uuid,
                spec["workflow_node_uuid"],
                int(spec.get("topological_index") or 0),
                spec["executor_kind"],
                _json(spec.get("execution_policy") or {}),
                int(spec.get("execution_timeout_seconds") or 0),
                _json(spec.get("param") or {}),
                spec.get("material_uuid"),
                job_uuid,
            ),
        )
        self._insert_attempt(
            conn,
            job_uuid=job_uuid,
            run_uuid=run_uuid,
            task_uuid=task_uuid,
            node_uuid=str(spec["workflow_node_uuid"]),
            attempt_no=1,
            retry_of_job_uuid=None,
            trigger="initial",
            param=spec.get("param") or {},
            now=now,
        )
        return run_uuid

    @staticmethod
    def _insert_attempt(
        conn: sqlite3.Connection,
        *,
        job_uuid: str,
        run_uuid: str,
        task_uuid: str,
        node_uuid: str,
        attempt_no: int,
        retry_of_job_uuid: Optional[str],
        trigger: str,
        param: Dict[str, Any],
        now: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO workflow_node_job(
                uuid, create_time, update_time, deleted_at, description, meta_data,
                workflow_node_run_uuid, workflow_task_uuid, workflow_node_uuid,
                attempt_no, retry_of_job_uuid, trigger, feedback_sequence, status, param,
                feedback_data, return_info, error_resolution, control_data, error_info
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, ?, ?, ?, ?, 0, 'pending', ?,
                      '{}', '{}', '{}', '{}', '[]')
            """,
            (
                job_uuid,
                now,
                now,
                run_uuid,
                task_uuid,
                node_uuid,
                int(attempt_no),
                retry_of_job_uuid,
                trigger,
                _json(param),
            ),
        )

    def _sync_run_projection(
        self, conn: sqlite3.Connection, run_uuid: str, now: str
    ) -> Dict[str, Any]:
        """把当前 attempt 的状态/结果投影到节点运行，并发节点运行变更事件。"""

        run = conn.execute(
            "SELECT * FROM workflow_node_run WHERE uuid=? AND deleted_at IS NULL",
            (run_uuid,),
        ).fetchone()
        if run is None:
            raise StoreNotFound(f"workflow node run {run_uuid} not found")
        job = conn.execute(
            "SELECT * FROM workflow_node_job WHERE uuid=? AND deleted_at IS NULL",
            (run["current_job_uuid"],),
        ).fetchone()
        if job is None:
            raise StoreConflict(f"workflow node run {run_uuid} has no current attempt")
        finished_at = job["finished_at"] if job["status"] in self._RUN_TERMINAL else None
        conn.execute(
            """
            UPDATE workflow_node_run
            SET status=?, return_info=?, error_info=?, feedback_data=?,
                started_at=COALESCE(started_at, ?), finished_at=?, update_time=?
            WHERE uuid=?
            """,
            (
                job["status"],
                job["return_info"],
                job["error_info"],
                job["feedback_data"],
                job["started_at"],
                finished_at,
                now,
                run_uuid,
            ),
        )
        self._append_event(
            conn,
            event="workflow.node_run.changed",
            data={
                "workflow_node_run_uuid": run_uuid,
                "workflow_task_uuid": run["workflow_task_uuid"],
                "workflow_node_uuid": run["workflow_node_uuid"],
                "status": job["status"],
                "current_job_uuid": job["uuid"],
                "attempt_count": int(run["attempt_count"]),
            },
            now=now,
        )
        return self._run_row(
            conn.execute(
                "SELECT * FROM workflow_node_run WHERE uuid=?", (run_uuid,)
            ).fetchone()
        )

    def _emit_job_changed(
        self,
        conn: sqlite3.Connection,
        job: sqlite3.Row,
        status: str,
        now: str,
        **extra: Any,
    ) -> None:
        self._append_event(
            conn,
            event="workflow.node_job.changed",
            data={
                "workflow_node_job_uuid": job["uuid"],
                "workflow_node_run_uuid": job["workflow_node_run_uuid"],
                "attempt_no": int(job["attempt_no"]),
                "status": status,
                **extra,
            },
            now=now,
        )

    def create_ad_hoc_task_with_job(
        self,
        *,
        task_uuid: str,
        node_uuid: str,
        device_id: str,
        action_name: str,
        action_type: str,
        param: Dict[str, Any],
        execution_policy: Dict[str, Any],
        execution_timeout_seconds: int,
        description: Optional[str],
        meta_data: Dict[str, Any],
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Dict[str, Any]:
        """单点设备动作任务：无 workflow 定义，task + 单个节点运行（attempt 1）一次落库。

        snapshot/plan 按调度器 `_build_dag` 的消费面构造（snapshot.nodes 提供
        action_name/action_type/target_device_id，plan.nodes 提供 param），
        与整图任务共用同一执行/历史/异常链路。幂等键唯一索引兜底并发重复。
        """

        now = utc_now()
        snapshot = {
            "nodes": [
                {
                    "uuid": node_uuid,
                    "action_name": action_name,
                    "action_type": action_type,
                    "meta_data": {"target_device_id": device_id},
                }
            ],
            "edges": [],
        }
        plan = {
            "run_mode": "normal",
            "target_node_uuid": None,
            "nodes": [{"uuid": node_uuid, "param": param}],
            "edges": [],
        }
        try:
            with self.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO workflow_task(
                        uuid, create_time, update_time, deleted_at, description,
                        meta_data, workflow_uuid, status, workflow_snapshot,
                        execution_plan, run_mode, target_node_uuid, control_status,
                        cleanup_status, trace_context, input, output, error_info,
                        execution_kind, idempotency_key, request_fingerprint
                    ) VALUES (?, ?, ?, NULL, ?, ?, NULL, 'pending', ?, ?, 'normal',
                              NULL, 'active', 'none', '{}', '{}', '{}', '[]',
                              'ad_hoc_device_action', ?, ?)
                    """,
                    (
                        task_uuid,
                        now,
                        now,
                        description,
                        _json(meta_data),
                        _json(snapshot),
                        _json(plan),
                        idempotency_key,
                        request_fingerprint,
                    ),
                )
                self._insert_node_run(
                    conn,
                    task_uuid=task_uuid,
                    spec={
                        "uuid": str(uuid4()),
                        "workflow_node_uuid": node_uuid,
                        "topological_index": 0,
                        "executor_kind": "device_action",
                        "execution_policy": execution_policy,
                        "execution_timeout_seconds": execution_timeout_seconds,
                        "param": param,
                    },
                    now=now,
                )
        except sqlite3.IntegrityError as exc:
            # 幂等键唯一索引命中：并发同键提交由 service 读回先到者
            raise StoreConflict(str(exc)) from exc
        return self.get_task(task_uuid)

    def find_task_by_idempotency_key(
        self, execution_kind: str, idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        """按 (execution_kind, idempotency_key) 命中既有任务（幂等复用）。"""

        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM workflow_task
                WHERE execution_kind = ? AND idempotency_key = ?
                  AND deleted_at IS NULL
                """,
                (execution_kind, idempotency_key),
            ).fetchone()
        return None if row is None else self._task_row(row)

    def get_task(self, task_uuid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"workflow task {task_uuid} not found")
        return self._task_row(row)

    def list_tasks(
        self,
        *,
        page: int,
        page_size: int,
        workflow_uuid: Optional[str] = None,
        status: str = "",
        cleanup_status: str = "",
    ) -> Dict[str, Any]:
        clauses = ["deleted_at IS NULL"]
        values: List[Any] = []
        for field, value in (
            ("workflow_uuid", workflow_uuid),
            ("status", status),
            ("cleanup_status", cleanup_status),
        ):
            if value:
                clauses.append(f"{field} = ?")
                values.append(value)
        where = " AND ".join(clauses)
        offset = (page - 1) * page_size
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM workflow_task WHERE {where}",
                values,
            ).fetchone()[0]
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow_task WHERE {where}
                ORDER BY create_time DESC, uuid
                LIMIT ? OFFSET ?
                """,
                (*values, page_size, offset),
            ).fetchall()
        return {
            "items": [self._task_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def list_node_runs(
        self, task_uuid: str, *, include_attempts: bool = True
    ) -> List[Dict[str, Any]]:
        """任务的节点运行，每个工作流节点一条，按拓扑序；可内嵌 attempt 历史。"""

        self.get_task(task_uuid)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM workflow_node_run
                WHERE workflow_task_uuid = ? AND deleted_at IS NULL
                ORDER BY topological_index, create_time, uuid
                """,
                (task_uuid,),
            ).fetchall()
            runs = [self._run_row(row) for row in rows]
            if include_attempts:
                attempts = self._attempts_by_run(
                    self._conn,
                    "job.workflow_task_uuid = ?",
                    (task_uuid,),
                )
                for run in runs:
                    run["attempts"] = attempts.get(run["uuid"], [])
        return runs

    def get_node_run(
        self, run_uuid: str, *, include_attempts: bool = True
    ) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_node_run WHERE uuid = ? AND deleted_at IS NULL",
                (run_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound(f"workflow node run {run_uuid} not found")
            run = self._run_row(row)
            if include_attempts:
                run["attempts"] = self._attempts_by_run(
                    self._conn,
                    "job.workflow_node_run_uuid = ?",
                    (run_uuid,),
                ).get(run_uuid, [])
        return run

    def _attempts_by_run(
        self,
        conn: sqlite3.Connection,
        where: str,
        params: tuple,
    ) -> Dict[str, List[Dict[str, Any]]]:
        rows = conn.execute(
            f"""
            SELECT job.* FROM workflow_node_job AS job
            WHERE {where} AND job.deleted_at IS NULL
            ORDER BY job.workflow_node_run_uuid, job.attempt_no
            """,
            params,
        ).fetchall()
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["workflow_node_run_uuid"], []).append(self._job_row(row))
        return grouped

    def list_jobs(self, task_uuid: str) -> List[Dict[str, Any]]:
        """任务的全部 attempt（物理执行）平铺：按节点拓扑序、attempt 序号。"""

        self.get_task(task_uuid)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT job.* FROM workflow_node_job AS job
                JOIN workflow_node_run AS run ON run.uuid = job.workflow_node_run_uuid
                WHERE job.workflow_task_uuid = ? AND job.deleted_at IS NULL
                ORDER BY run.topological_index, run.create_time, run.uuid, job.attempt_no
                """,
                (task_uuid,),
            ).fetchall()
        return [self._job_row(row) for row in rows]

    def get_job(self, job_uuid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM workflow_node_job
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (job_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"workflow node job {job_uuid} not found")
        return self._job_row(row)

    @staticmethod
    def _runtime_limit_clause(limit: Optional[int], offset: int) -> tuple[str, tuple[int, ...]]:
        if limit is None:
            return "LIMIT -1 OFFSET ?", (offset,)
        return "LIMIT ? OFFSET ?", (limit, offset)

    def list_manual_confirmations(
        self, task_uuid: str, *, limit: Optional[int] = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        self.get_task(task_uuid)
        clause, paging = self._runtime_limit_clause(limit, offset)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow_manual_confirmation
                WHERE workflow_task_uuid = ? AND deleted_at IS NULL
                ORDER BY create_time, uuid
                {clause}
                """,
                (task_uuid, *paging),
            ).fetchall()
        return [self._manual_confirmation_row(row) for row in rows]

    def list_interventions(
        self, task_uuid: str, *, limit: Optional[int] = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        self.get_task(task_uuid)
        clause, paging = self._runtime_limit_clause(limit, offset)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow_intervention
                WHERE workflow_task_uuid = ? AND deleted_at IS NULL
                ORDER BY opened_at, uuid
                {clause}
                """,
                (task_uuid, *paging),
            ).fetchall()
        return [self._intervention_row(row) for row in rows]

    def list_job_results(
        self, job_uuid: str, *, limit: Optional[int] = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        self.get_job(job_uuid)
        clause, paging = self._runtime_limit_clause(limit, offset)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow_node_job_result
                WHERE workflow_node_job_uuid = ? AND deleted_at IS NULL
                ORDER BY committed_at, uuid
                {clause}
                """,
                (job_uuid, *paging),
            ).fetchall()
        return [self._job_result_row(row) for row in rows]

    def list_job_feedback_history(
        self, job_uuid: str, *, limit: Optional[int] = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        self.get_job(job_uuid)
        clause, paging = self._runtime_limit_clause(limit, offset)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM workflow_node_job_feedback_history
                WHERE workflow_node_job_uuid = ? AND deleted_at IS NULL
                ORDER BY sequence, uuid
                {clause}
                """,
                (job_uuid, *paging),
            ).fetchall()
        return [self._job_feedback_row(row) for row in rows]

    def list_template_action_references(self) -> List[Dict[str, Any]]:
        """活跃 workflow 节点对模板 action 的引用明细行。

        Registry Authority 用这些引用检测模板变更冲突，并定位受影响的画布
        节点。被引用的 action 删除或定义变化时，对应模板版本进入待确认状态。
        """

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT t.resource_template_uuid AS template_uuid,
                       t.name AS action_name,
                       n.uuid AS node_uuid,
                       n.name AS node_name,
                       w.uuid AS workflow_uuid,
                       w.name AS workflow_name
                FROM workflow_node n
                JOIN workflow_node_template t
                  ON t.uuid = n.workflow_node_template_uuid
                JOIN workflow w
                  ON w.uuid = n.workflow_uuid
                WHERE n.deleted_at IS NULL
                  AND t.deleted_at IS NULL
                  AND w.deleted_at IS NULL
                ORDER BY w.name, w.uuid, n.name, n.uuid
                """
            ).fetchall()
        return [
            {
                "template_uuid": str(row["template_uuid"]),
                "action": str(row["action_name"]),
                "node_uuid": str(row["node_uuid"]),
                "node_name": str(row["node_name"]),
                "workflow_uuid": str(row["workflow_uuid"]),
                "workflow_name": str(row["workflow_name"]),
            }
            for row in rows
        ]

    def list_recoverable_tasks(self) -> List[Dict[str, Any]]:
        """返回规范本地执行器可恢复的任务，不改写任何状态。"""

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM workflow_task
                WHERE deleted_at IS NULL
                  AND execution_kind = 'workflow'
                  AND status IN ('pending', 'running', 'canceling')
                ORDER BY create_time, uuid
                """
            ).fetchall()
        return [self._task_row(row) for row in rows]

    def prepare_task_execution(self, task_uuid: str) -> Dict[str, Any]:
        """认领/恢复任务，并以节点运行终态作为持久 completed cursor。

        判定单元是节点运行（当前 attempt 的投影），历史 failed attempt 不参与。
        已成功节点不会重跑。崩溃时处于 dispatched/running 的 attempt 无法证明设备
        是否已经产生副作用，attempt 与节点运行同时转 execution_unknown 等待对账，
        禁止盲目重放。
        """

        now = utc_now()
        with self.transaction() as conn:
            task_row = conn.execute(
                "SELECT * FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
            if task_row is None:
                raise StoreNotFound(f"workflow task {task_uuid} not found")
            task = self._task_row(task_row)
            run_rows = conn.execute(
                "SELECT * FROM workflow_node_run WHERE workflow_task_uuid = ? "
                "AND deleted_at IS NULL ORDER BY topological_index, create_time, uuid",
                (task_uuid,),
            ).fetchall()
            runs = [self._run_row(row) for row in run_rows]
            if task["status"] in {
                "succeeded",
                "failed",
                "canceled",
                "timeout",
            }:
                return {"state": "terminal", "task": task, "runs": runs}
            if task["control_status"] != "active":
                return {"state": task["control_status"], "task": task, "runs": runs}

            statuses = {run["status"] for run in runs}
            if statuses & self._RUN_IN_FLIGHT:
                reason = "process restarted with an in-flight workflow node job"
                in_flight_runs = [
                    run for run in runs if run["status"] in self._RUN_IN_FLIGHT
                ]
                for run in in_flight_runs:
                    conn.execute(
                        """
                        UPDATE workflow_node_job
                        SET status = 'execution_unknown', uncertainty_reason = ?,
                            update_time = ?
                        WHERE uuid = ? AND deleted_at IS NULL
                          AND status IN ('dispatched', 'running', 'cancel_requested')
                        """,
                        (reason, now, run["current_job_uuid"]),
                    )
                    self._sync_run_projection(conn, run["uuid"], now)
                conn.execute(
                    """
                    UPDATE workflow_task
                    SET control_status = 'waiting_reconciliation',
                        attention_reason = ?, update_time = ?
                    WHERE uuid = ?
                    """,
                    (reason, now, task_uuid),
                )
                self._append_event(
                    conn,
                    event="workflow.task.changed",
                    data={
                        "workflow_task_uuid": task_uuid,
                        "status": task["status"],
                        "control_status": "waiting_reconciliation",
                    },
                    now=now,
                )
                state = "waiting_reconciliation"
            elif not runs or statuses <= {"succeeded", "skipped"}:
                conn.execute(
                    "UPDATE workflow_task SET status='succeeded', output='{}', "
                    "finished_at=?, update_time=? WHERE uuid=?",
                    (now, now, task_uuid),
                )
                state = "terminal"
            elif statuses & {"failed", "timeout", "canceled"}:
                status = (
                    "failed"
                    if "failed" in statuses
                    else ("timeout" if "timeout" in statuses else "canceled")
                )
                conn.execute(
                    "UPDATE workflow_task SET status=?, finished_at=?, update_time=? "
                    "WHERE uuid=?",
                    (status, now, now, task_uuid),
                )
                state = "terminal"
            else:
                conn.execute(
                    """
                    UPDATE workflow_task
                    SET status = 'running', started_at = COALESCE(started_at, ?),
                        update_time = ?
                    WHERE uuid = ?
                    """,
                    (now, now, task_uuid),
                )
                state = "ready"
        return {
            "state": state,
            "task": self.get_task(task_uuid),
            "runs": self.list_node_runs(task_uuid, include_attempts=False),
        }

    def mark_job_running(self, job_uuid: str) -> Dict[str, Any]:
        """在发送设备副作用前持久化 attempt running，并投影到节点运行。"""

        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_node_job WHERE uuid=? AND deleted_at IS NULL",
                (job_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound(f"workflow node job {job_uuid} not found")
            if row["status"] in self._RUN_TERMINAL:
                return self._job_row(row)
            if row["status"] not in {"pending", "dispatched", "running"}:
                raise StoreConflict(
                    f"workflow node job {job_uuid} cannot run from {row['status']}"
                )
            conn.execute(
                """
                UPDATE workflow_node_job
                SET status='running', started_at=COALESCE(started_at, ?),
                    update_time=? WHERE uuid=?
                """,
                (now, now, job_uuid),
            )
            self._emit_job_changed(conn, row, "running", now)
            self._sync_run_projection(conn, row["workflow_node_run_uuid"], now)
        return self.get_job(job_uuid)

    def record_job_terminal(
        self,
        job_uuid: str,
        *,
        status: str,
        return_info: Dict[str, Any],
        error_info: List[Dict[str, Any]],
        error_resolution: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """持久化 attempt 终态并投影到节点运行；``retry`` 决策在同一事务里追加新 attempt。

        返回 ``{"job", "run", "next_job"}``：``next_job`` 非空表示节点运行没有终结，
        调度器应以它为新的执行器 job 重新申请资源并下发；否则以 ``run["status"]``
        收敛 DAG 节点。已终态的 attempt 幂等返回，不重复投影。
        """

        if status not in self._RUN_TERMINAL:
            raise ValueError(f"invalid terminal workflow node job status: {status}")
        resolution = dict(error_resolution or {})
        retry_requested = (
            status == "failed" and str(resolution.get("selected_action") or "") == "retry"
        )
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_node_job WHERE uuid=? AND deleted_at IS NULL",
                (job_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound(f"workflow node job {job_uuid} not found")
            run_uuid = row["workflow_node_run_uuid"]
            if row["status"] in self._RUN_TERMINAL:
                run = self.get_node_run(run_uuid, include_attempts=False)
                return {"job": self._job_row(row), "run": run, "next_job": None}
            conn.execute(
                """
                UPDATE workflow_node_job
                SET status=?, return_info=?, error_info=?, error_resolution=?,
                    finished_at=?, update_time=?
                WHERE uuid=?
                """,
                (
                    status,
                    _json(return_info),
                    _json(error_info),
                    _json(resolution),
                    now,
                    now,
                    job_uuid,
                ),
            )
            self._emit_job_changed(conn, row, status, now)
            next_job_uuid: Optional[str] = None
            if retry_requested:
                # 失败 attempt 保留为事实；同一节点运行追加下一 attempt 并切换 current，
                # 节点运行回到 pending 而不是 failed——任务不中断。
                next_job_uuid = str(uuid4())
                self._insert_attempt(
                    conn,
                    job_uuid=next_job_uuid,
                    run_uuid=run_uuid,
                    task_uuid=row["workflow_task_uuid"],
                    node_uuid=row["workflow_node_uuid"],
                    attempt_no=int(row["attempt_no"]) + 1,
                    retry_of_job_uuid=job_uuid,
                    trigger="retry_decision",
                    param=_load(row["param"], {}),
                    now=now,
                )
                conn.execute(
                    """
                    UPDATE workflow_node_run
                    SET current_job_uuid=?, attempt_count=attempt_count + 1, update_time=?
                    WHERE uuid=?
                    """,
                    (next_job_uuid, now, run_uuid),
                )
            run = self._sync_run_projection(conn, run_uuid, now)
            job = self._job_row(
                conn.execute(
                    "SELECT * FROM workflow_node_job WHERE uuid=?", (job_uuid,)
                ).fetchone()
            )
            next_job = (
                self._job_row(
                    conn.execute(
                        "SELECT * FROM workflow_node_job WHERE uuid=?", (next_job_uuid,)
                    ).fetchone()
                )
                if next_job_uuid is not None
                else None
            )
        return {"job": job, "run": run, "next_job": next_job}

    def close_node_run(self, run_uuid: str, *, status: str) -> Dict[str, Any]:
        """DAG 收敛（取消/上游失败）时给尚未终结的节点运行记终态：作用于当前 attempt。"""

        if status not in self._RUN_TERMINAL:
            raise ValueError(f"invalid terminal workflow node run status: {status}")
        current = self.get_node_run(run_uuid, include_attempts=False)
        if current["status"] in self._RUN_TERMINAL:
            return current
        outcome = self.record_job_terminal(
            str(current["current_job_uuid"]),
            status=status,
            return_info={},
            error_info=([] if status == "succeeded" else [{"code": status}]),
        )
        return outcome["run"]

    def finish_task(
        self,
        task_uuid: str,
        *,
        status: str,
        output: Dict[str, Any],
        error_info: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if status not in {"succeeded", "failed", "canceled", "timeout"}:
            raise ValueError(f"invalid terminal workflow task status: {status}")
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT status FROM workflow_task WHERE uuid=? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound(f"workflow task {task_uuid} not found")
            if row["status"] in {"succeeded", "failed", "canceled", "timeout"}:
                return self.get_task(task_uuid)
            conn.execute(
                """
                UPDATE workflow_task
                SET status=?, output=?, error_info=?, finished_at=?, update_time=?
                WHERE uuid=?
                """,
                (status, _json(output), _json(error_info), now, now, task_uuid),
            )
            self._append_event(
                conn,
                event="workflow.task.changed",
                data={"workflow_task_uuid": task_uuid, "status": status},
                now=now,
            )
        return self.get_task(task_uuid)

    # Authoring ----------------------------------------------------------

    def register_source(
        self,
        *,
        workflow_uuid: str,
        package_id: str,
        package_root: str,
        relative_path: str,
        source_uri: str,
    ) -> Dict[str, Any]:
        now = utc_now()
        try:
            with self.transaction() as conn:
                WorkflowStore.get_workflow(self, workflow_uuid, conn=conn)
                conn.execute(
                    """
                    INSERT INTO workflow_source_registration(
                        workflow_uuid, package_id, package_root, relative_path,
                        source_uri, create_time, update_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workflow_uuid) DO UPDATE SET
                        package_id = excluded.package_id,
                        package_root = excluded.package_root,
                        relative_path = excluded.relative_path,
                        source_uri = excluded.source_uri,
                        update_time = excluded.update_time
                    """,
                    (
                        workflow_uuid,
                        package_id,
                        package_root,
                        relative_path,
                        source_uri,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO workflow_authoring(
                        workflow_uuid, diagnostics, update_time
                    ) VALUES (?, '[]', ?)
                    ON CONFLICT(workflow_uuid) DO NOTHING
                    """,
                    (workflow_uuid, now),
                )
        except sqlite3.IntegrityError as exc:
            raise StoreConflict("工作流源码身份已被占用") from exc
        return self.get_source_registration(workflow_uuid)

    def get_source_registration(self, workflow_uuid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM workflow_source_registration
                WHERE workflow_uuid = ?
                """,
                (workflow_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(
                f"authoring source for workflow {workflow_uuid} is not registered"
            )
        return dict(row)

    def list_source_registrations(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT registration.*
                FROM workflow_source_registration AS registration
                JOIN workflow
                  ON workflow.uuid = registration.workflow_uuid
                WHERE workflow.deleted_at IS NULL
                ORDER BY registration.workflow_uuid
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_authoring_record(self, workflow_uuid: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_authoring WHERE workflow_uuid = ?",
                (workflow_uuid,),
            ).fetchone()
        if row is None:
            return {
                "workflow_uuid": workflow_uuid,
                "observed_draft_hash": None,
                "draft_update_time": None,
                "diagnostics": [],
                "candidate_hash": None,
                "candidate": None,
                "applied_source": None,
                "writeback_status": "settled",
                "writeback_source": None,
                "writeback_expected_hash": None,
                "writeback_generation": None,
                "update_time": None,
            }
        result = dict(row)
        result["diagnostics"] = _load(result["diagnostics"], [])
        result["candidate"] = _load(result["candidate"], None)
        result["applied_source"] = _load(result["applied_source"], None)
        return result

    def record_draft_compilation(
        self,
        *,
        workflow_uuid: str,
        draft_hash: Optional[str],
        draft_update_time: Optional[str],
        diagnostics: List[Dict[str, Any]],
        candidate_hash: Optional[str],
        candidate: Optional[Dict[str, Any]],
        event_data: Dict[str, Any],
    ) -> int:
        now = utc_now()
        with self.transaction() as conn:
            WorkflowStore.get_workflow(self, workflow_uuid, conn=conn)
            conn.execute(
                """
                INSERT INTO workflow_authoring(
                    workflow_uuid, observed_draft_hash, draft_update_time,
                    diagnostics, candidate_hash, candidate, update_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_uuid) DO UPDATE SET
                    observed_draft_hash = excluded.observed_draft_hash,
                    draft_update_time = excluded.draft_update_time,
                    diagnostics = excluded.diagnostics,
                    candidate_hash = excluded.candidate_hash,
                    candidate = excluded.candidate,
                    writeback_status = 'settled',
                    writeback_source = NULL,
                    writeback_expected_hash = NULL,
                    writeback_generation = NULL,
                    update_time = excluded.update_time
                """,
                (
                    workflow_uuid,
                    draft_hash,
                    draft_update_time,
                    _json(diagnostics),
                    candidate_hash,
                    _json(candidate) if candidate is not None else None,
                    now,
                ),
            )
            return self._append_event(
                conn,
                event="workflow.authoring.changed",
                data=event_data,
                now=now,
            )

    def apply_authoring_candidate(
        self,
        *,
        workflow_uuid: str,
        expected_revision: int,
        expected_draft_hash: str,
        expected_candidate_hash: str,
        expected_catalog_fingerprint: str,
        candidate: Dict[str, Any],
        applied_source: Dict[str, Any],
        event_data: Dict[str, Any],
    ) -> Tuple[int, str]:
        changeset = candidate["changeset"]
        kind = changeset["kind"]
        graph = candidate["graph"]
        now = utc_now()
        with self.transaction() as conn:
            writeback_generation = str(uuid4())
            authoring = conn.execute(
                """
                SELECT observed_draft_hash, candidate_hash, candidate
                FROM workflow_authoring
                WHERE workflow_uuid = ?
                """,
                (workflow_uuid,),
            ).fetchone()
            if (
                authoring is None
                or authoring["observed_draft_hash"] != expected_draft_hash
            ):
                raise StoreAuthoringConflict("draft_hash_conflict")
            workflow = WorkflowStore.get_workflow(self, workflow_uuid, conn=conn)
            if workflow["revision"] != expected_revision:
                raise StoreRevisionConflict("workflow revision changed before apply")
            stored_candidate = _load(authoring["candidate"], None)
            if not isinstance(stored_candidate, dict):
                raise StoreAuthoringConflict("candidate_not_ready")
            if (
                stored_candidate.get("template_catalog_fingerprint")
                != expected_catalog_fingerprint
            ):
                raise StoreAuthoringConflict("template_catalog_conflict")
            if authoring["candidate_hash"] != expected_candidate_hash:
                raise StoreAuthoringConflict("candidate_hash_conflict")
            if kind == "graph":
                graph_workflow = graph.get("workflow")
                if not isinstance(graph_workflow, dict):
                    raise StoreConflict("Candidate 缺少 Workflow 根对象")
                if (
                    graph_workflow.get("uuid") != workflow_uuid
                    or graph_workflow.get("revision") != expected_revision
                ):
                    raise StoreConflict("Candidate Workflow 身份或版本不匹配")
                candidate_meta = graph_workflow.get("meta_data")
                if not isinstance(candidate_meta, dict):
                    raise StoreConflict("Candidate Workflow meta_data 必须是对象")
                nodes = [
                    WorkflowNodeWrite.model_validate(
                        {
                            field: item[field]
                            for field in WorkflowNodeWrite.model_fields
                            if field in item
                        }
                    )
                    for item in graph.get("nodes", [])
                ]
                edges = [
                    WorkflowEdgeWrite.model_validate(
                        {
                            field: item[field]
                            for field in WorkflowEdgeWrite.model_fields
                            if field in item
                        }
                    )
                    for item in graph.get("edges", [])
                ]
                resulting_revision = self._reconcile_graph(
                    conn,
                    workflow_uuid=workflow_uuid,
                    expected_revision=expected_revision,
                    nodes=nodes,
                    edges=edges,
                    advance_revision=True,
                    protect_reserved_metadata=False,
                    semantic_workflow_meta_data=candidate_meta,
                )
                workflow_meta = dict(workflow["meta_data"])
                workflow_meta.pop("unilab", None)
                if "unilab" in candidate_meta:
                    if candidate_meta["unilab"] is not None:
                        workflow_meta["unilab"] = candidate_meta["unilab"]
                conn.execute(
                    """
                    UPDATE workflow
                    SET meta_data = ?, update_time = ?
                    WHERE uuid = ? AND deleted_at IS NULL
                    """,
                    (_json(workflow_meta), now, workflow_uuid),
                )
            elif kind == "source_only":
                resulting_revision = expected_revision
            else:
                raise StoreConflict(f"unsupported Authoring changeset kind {kind!r}")
            applied_source = {
                **applied_source,
                "workflow_revision": resulting_revision,
                "update_time": now,
            }
            conn.execute(
                """
                UPDATE workflow_authoring
                SET diagnostics = '[]', candidate_hash = NULL,
                    candidate = NULL, applied_source = ?,
                    writeback_status = 'pending',
                    writeback_source = ?,
                    writeback_expected_hash = observed_draft_hash,
                    writeback_generation = ?,
                    update_time = ?
                WHERE workflow_uuid = ?
                """,
                (
                    _json(applied_source),
                    applied_source["python_source"],
                    writeback_generation,
                    now,
                    workflow_uuid,
                ),
            )
            self._append_event(
                conn,
                event="workflow.authoring.changed",
                data={
                    **event_data,
                    "workflow_revision": resulting_revision,
                },
                now=now,
            )
            return resulting_revision, writeback_generation

    def settle_writeback(
        self,
        *,
        workflow_uuid: str,
        expected_writeback_source: str,
        expected_writeback_hash: str,
        expected_writeback_generation: str,
        observed_draft_hash: str,
        draft_update_time: str,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        with self.transaction() as conn:
            now = utc_now()
            updated = conn.execute(
                """
                UPDATE workflow_authoring
                SET observed_draft_hash = ?, draft_update_time = ?,
                    writeback_status = 'settled', writeback_source = NULL,
                    writeback_expected_hash = NULL,
                    writeback_generation = NULL, update_time = ?
                WHERE workflow_uuid = ?
                  AND writeback_status = 'pending'
                  AND writeback_source = ?
                  AND writeback_expected_hash = ?
                  AND writeback_generation = ?
                """,
                (
                    observed_draft_hash,
                    draft_update_time,
                    now,
                    workflow_uuid,
                    expected_writeback_source,
                    expected_writeback_hash,
                    expected_writeback_generation,
                ),
            )
            if updated.rowcount != 1:
                return False
            if event_data is not None:
                self._append_event(
                    conn,
                    event="workflow.authoring.changed",
                    data=event_data,
                    now=now,
                )
            return True

    def mark_writeback_pending(
        self,
        *,
        workflow_uuid: str,
        expected_writeback_source: str,
        expected_writeback_hash: str,
        expected_writeback_generation: str,
    ) -> bool:
        with self.transaction() as conn:
            updated = conn.execute(
                """
                UPDATE workflow_authoring
                SET writeback_status = 'pending', update_time = ?
                WHERE workflow_uuid = ?
                  AND writeback_source = ?
                  AND writeback_expected_hash = ?
                  AND writeback_generation = ?
                """,
                (
                    utc_now(),
                    workflow_uuid,
                    expected_writeback_source,
                    expected_writeback_hash,
                    expected_writeback_generation,
                ),
            )
            return updated.rowcount == 1

    # 事件与诊断 --------------------------------------------------------

    def list_events(
        self,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM frontend_event
                WHERE sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (after_id, limit),
            ).fetchall()
        return [
            {
                "id": row["sequence"],
                "uuid": row["uuid"],
                "event": row["type"],
                "aggregate_uuid": row["aggregate_uuid"],
                "data": _load(row["payload"], {}),
                "create_time": row["create_time"],
            }
            for row in rows
        ]

    @staticmethod
    def _append_event(
        conn: sqlite3.Connection,
        *,
        event: str,
        data: Dict[str, Any],
        now: str,
    ) -> int:
        aggregate_uuid = next(
            (
                str(data[key])
                for key in ("workflow_uuid", "task_uuid", "uuid")
                if data.get(key)
            ),
            "00000000-0000-0000-0000-000000000000",
        )
        cursor = conn.execute(
            """
            INSERT INTO frontend_event(
                uuid, create_time, type, aggregate_uuid, payload
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid4()), now, event, aggregate_uuid, _json(data)),
        )
        return int(cursor.lastrowid)

    def count_rows(self, table: str, *, include_deleted: bool = False) -> int:
        allowed = {
            "workflow",
            "workflow_node",
            "workflow_edge",
            "workflow_task",
            "workflow_node_run",
            "workflow_node_job",
            "workflow_task_command",
            "workflow_node_job_feedback_history",
            "workflow_node_job_result",
            "workflow_intervention",
            "workflow_manual_confirmation",
            "execution_lock_lease",
            "workflow_authoring",
            "frontend_event",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table {table!r}")
        where = (
            ""
            if include_deleted
            or table
            in {
                "workflow_authoring",
                "frontend_event",
            }
            else " WHERE deleted_at IS NULL"
        )
        with self._lock:
            return int(
                self._conn.execute(f"SELECT COUNT(*) FROM {table}{where}").fetchone()[0]
            )

    # 行投影 ------------------------------------------------------------

    @staticmethod
    def _base(row: sqlite3.Row) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "uuid": row["uuid"],
            "create_time": row["create_time"],
            "update_time": row["update_time"],
            "meta_data": _load(row["meta_data"], {}),
        }
        if row["description"] is not None:
            result["description"] = row["description"]
        return result

    @classmethod
    def _workflow_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            **cls._base(row),
            "name": row["name"],
            "tags": _load(row["tags"], []),
            "revision": row["revision"],
        }

    @classmethod
    def _node_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_uuid": row["workflow_uuid"],
            "name": row["name"],
            "status": row["status"],
            "type": row["type"],
            "pose": _load(row["pose"], {}),
            "param": _load(row["param"], {}),
            "execution_policy": _load(row["execution_policy"], {}),
            "disabled": bool(row["disabled"]),
            "minimized": bool(row["minimized"]),
        }
        cls._add_optional(
            result,
            row,
            "workflow_node_template_uuid",
            "parent_uuid",
            "material_uuid",
            "icon",
            "footer",
            "action_name",
            "action_type",
            "script",
        )
        return result

    @classmethod
    def _edge_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            **cls._base(row),
            "source_node_uuid": row["source_node_uuid"],
            "target_node_uuid": row["target_node_uuid"],
            "source_handle_uuid": row["source_handle_uuid"],
            "target_handle_uuid": row["target_handle_uuid"],
        }

    @classmethod
    def _node_template_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "resource_template_uuid": row["resource_template_uuid"],
            "name": row["name"],
            "display_name": row["display_name"],
            "goal": _load(row["goal"], {}),
            "goal_default": _load(row["goal_default"], {}),
            "feedback": _load(row["feedback"], {}),
            "result": _load(row["result"], {}),
            "type": row["type"],
            "node_type": row["node_type"],
        }
        cls._add_optional(
            result,
            row,
            "class",
            "schema",
            "icon",
            "header",
            "footer",
        )
        return result

    @classmethod
    def _handle_template_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_node_template_uuid": row["workflow_node_template_uuid"],
            "handle_key": row["handle_key"],
            "io_type": row["io_type"],
            "display_name": row["display_name"],
            "type": row["type"],
            "required": bool(row["required"]),
        }
        cls._add_optional(result, row, "data_source", "data_key")
        return result

    @classmethod
    def _task_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_uuid": row["workflow_uuid"],
            "execution_kind": row["execution_kind"],
            "status": row["status"],
            "workflow_snapshot": _load(row["workflow_snapshot"], {}),
            "execution_plan": _load(row["execution_plan"], {}),
            "run_mode": row["run_mode"],
            "control_status": row["control_status"],
            "cleanup_status": row["cleanup_status"],
            "trace_context": _load(row["trace_context"], {}),
            "input": _load(row["input"], {}),
            "output": _load(row["output"], {}),
            "error_info": _load(row["error_info"], []),
        }
        cls._add_optional(
            result,
            row,
            "target_node_uuid",
            "timeout_at",
            "attention_reason",
            "terminal_ghost_detected_at",
            "reconciliation_resume_control_status",
            "started_at",
            "finished_at",
            "idempotency_key",
        )
        # ad_hoc 幂等复用需要指纹比对；workflow 任务恒为空串，不额外暴露
        fingerprint = row["request_fingerprint"]
        if fingerprint:
            result["request_fingerprint"] = fingerprint
        return result

    @classmethod
    def _run_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_task_uuid": row["workflow_task_uuid"],
            "workflow_node_uuid": row["workflow_node_uuid"],
            "topological_index": row["topological_index"],
            "executor_kind": row["executor_kind"],
            "execution_policy": _load(row["execution_policy"], {}),
            "execution_timeout_seconds": row["execution_timeout_seconds"],
            "param": _load(row["param"], {}),
            "status": row["status"],
            "current_job_uuid": row["current_job_uuid"],
            "attempt_count": row["attempt_count"],
            "return_info": _load(row["return_info"], {}),
            "error_info": _load(row["error_info"], []),
            "feedback_data": _load(row["feedback_data"], {}),
            "control_data": _load(row["control_data"], {}),
        }
        cls._add_optional(result, row, "material_uuid", "started_at", "finished_at")
        return result

    @classmethod
    def _job_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_node_run_uuid": row["workflow_node_run_uuid"],
            "workflow_task_uuid": row["workflow_task_uuid"],
            "workflow_node_uuid": row["workflow_node_uuid"],
            "attempt_no": row["attempt_no"],
            "trigger": row["trigger"],
            "feedback_sequence": row["feedback_sequence"],
            "status": row["status"],
            "param": _load(row["param"], {}),
            "feedback_data": _load(row["feedback_data"], {}),
            "return_info": _load(row["return_info"], {}),
            "error_resolution": _load(row["error_resolution"], {}),
            "control_data": _load(row["control_data"], {}),
            "error_info": _load(row["error_info"], []),
        }
        cls._add_optional(
            result,
            row,
            "retry_of_job_uuid",
            ("edge_agent_uuid", "edge_uuid"),
            "edge_command_uuid",
            "dispatch_deadline_at",
            "execution_deadline_at",
            "cancel_command_uuid",
            "cancel_ack_deadline_at",
            "cancel_complete_deadline_at",
            "uncertainty_reason",
            "started_at",
            "finished_at",
        )
        return result

    @classmethod
    def _manual_confirmation_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_task_uuid": row["workflow_task_uuid"],
            "workflow_node_job_uuid": row["workflow_node_job_uuid"],
            "status": row["status"],
            "assignee_user_ids": _load(row["assignee_user_ids"], []),
            "param": _load(row["param"], {}),
            "opened_at": row["opened_at"],
        }
        cls._add_optional(
            result,
            row,
            "confirmed_by",
            "comment",
            "decision_idempotency_key",
            "deadline_at",
            "decided_at",
        )
        return result

    @classmethod
    def _intervention_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_task_uuid": row["workflow_task_uuid"],
            "workflow_node_job_uuid": row["workflow_node_job_uuid"],
            "edge_agent_uuid": row["edge_agent_uuid"],
            "revision": row["revision"],
            "status": row["status"],
            "options": _load(row["options"], []),
            "resume_control_status": row["resume_control_status"],
            "selected_option": _load(row["selected_option"], {}),
            "opened_at": row["opened_at"],
        }
        cls._add_optional(
            result,
            row,
            "selected_option_id",
            "decision_idempotency_key",
            "edge_command_uuid",
            "decided_at",
        )
        return result

    @classmethod
    def _job_result_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_node_job_uuid": row["workflow_node_job_uuid"],
            "edge_command_uuid": row["edge_command_uuid"],
            "idempotency_key": row["idempotency_key"],
            "outcome": row["outcome"],
            "return_info": _load(row["return_info"], {}),
            "error_info": _load(row["error_info"], []),
            "committed_at": row["committed_at"],
        }
        cls._add_optional(result, row, "consumed_at")
        return result

    @classmethod
    def _job_feedback_row(cls, row: sqlite3.Row) -> Dict[str, Any]:
        result = {
            **cls._base(row),
            "workflow_node_job_uuid": row["workflow_node_job_uuid"],
            "sequence": row["sequence"],
            "feedback_type": row["feedback_type"],
            "data": _load(row["data"], {}),
            "observed_at": row["observed_at"],
            "received_at": row["received_at"],
            "idempotency_key": row["idempotency_key"],
        }
        cls._add_optional(result, row, "published_at")
        return result

    @staticmethod
    def _add_optional(
        result: Dict[str, Any],
        row: sqlite3.Row,
        *fields: str | Tuple[str, str],
    ) -> None:
        for field in fields:
            column, output = field if isinstance(field, tuple) else (field, field)
            value = row[column]
            if value is not None:
                result[output] = value


__all__ = [
    "StoreAuthoringConflict",
    "StoreConflict",
    "StoreNotFound",
    "StoreRevisionConflict",
    "WorkflowStore",
    "utc_now",
]
