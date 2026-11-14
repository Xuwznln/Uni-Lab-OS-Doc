"""``runtime.db`` 的版本迁移（v2 起）。

迁移里的 DDL 是该版本冻结的字面量，不引用会继续演进的表规格。
"""

from unilabos.server.database.schema import sql_migration

# v2：资源等待记录加入队时间，准入按老化优先级 + 入队时间排队（SC-N4）。等待记录是瞬态事实：
# 调度器启动时会对所有未派发的作业重新申请准入并重建记录，所以旧记录（没有入队时间）直接清空。
_V2_RESOURCE_WAITER_ENQUEUED_AT = sql_migration(
    2,
    "workflow_resource_waiter 增加 enqueued_at（持久等待顺序），清空旧等待记录",
    "DROP TABLE workflow_resource_waiter",
    """
        CREATE TABLE workflow_resource_waiter (
            job_uuid TEXT PRIMARY KEY REFERENCES workflow_node_job(uuid),
            task_uuid TEXT NOT NULL REFERENCES workflow_task(uuid),
            lock_keys TEXT NOT NULL CHECK(json_valid(lock_keys) AND json_type(lock_keys)='array'),
            blockers TEXT NOT NULL CHECK(json_valid(blockers) AND json_type(blockers)='array'),
            enqueued_at TEXT NOT NULL CHECK(enqueued_at <> '')
        )
    """,
)

# v3：attempt 记录派发时求出的预计时长（A01，``@action(estimate_duration_*)``）；``job_runs``
# 投影视图读这两列（旧行没有预计时长，投影为 0.0 / unknown，与之前的常量一致）。
_V3_JOB_ESTIMATED_DURATION = sql_migration(
    3,
    "workflow_node_job 增加 estimated_s / estimate_source，job_runs 视图改读这两列",
    "ALTER TABLE workflow_node_job ADD COLUMN estimated_s REAL",
    "ALTER TABLE workflow_node_job ADD COLUMN estimate_source TEXT",
    "DROP VIEW IF EXISTS job_runs",
    """
    CREATE VIEW job_runs AS
    SELECT
        job.rowid AS id,
        job.uuid AS job_id,
        job.workflow_task_uuid AS workflow_id,
        job.workflow_node_uuid AS node_id,
        COALESCE(job.edge_agent_uuid, run.material_uuid, '') AS device_id,
        COALESCE(json_extract(job.param, '$.action'), '') AS action_name,
        CASE
            WHEN COALESCE(job.edge_agent_uuid, run.material_uuid, '') = '' THEN ''
            ELSE COALESCE(job.edge_agent_uuid, run.material_uuid, '') || ':' ||
                 COALESCE(json_extract(job.param, '$.action'), '')
        END AS device_action_key,
        COALESCE(
            (julianday(REPLACE(job.started_at, 'Z', '+00:00')) - 2440587.5)
                * 86400.0,
            (julianday(REPLACE(job.create_time, 'Z', '+00:00')) - 2440587.5)
                * 86400.0
        ) AS started_at,
        COALESCE(
            (julianday(REPLACE(job.finished_at, 'Z', '+00:00')) - 2440587.5)
                * 86400.0,
            (julianday(REPLACE(job.update_time, 'Z', '+00:00')) - 2440587.5)
                * 86400.0
        ) AS ended_at,
        CASE WHEN job.finished_at IS NULL OR job.started_at IS NULL THEN 0.0 ELSE
            (julianday(REPLACE(job.finished_at, 'Z', '+00:00'))
             - julianday(REPLACE(job.started_at, 'Z', '+00:00'))) * 86400.0
            END AS actual_s,
        COALESCE(job.estimated_s, 0.0) AS estimated_s,
        COALESCE(job.estimate_source, 'unknown') AS estimate_source,
        CASE job.status WHEN 'succeeded' THEN 'success' ELSE job.status END AS state,
        CASE job.status WHEN 'skipped' THEN 'skip' ELSE 'normal' END AS suc_type,
        job.return_info AS ret_json
    FROM workflow_node_job AS job
    LEFT JOIN workflow_node_run AS run ON run.uuid = job.workflow_node_run_uuid
    WHERE job.deleted_at IS NULL
    """,
)

RUNTIME_MIGRATIONS = (_V2_RESOURCE_WAITER_ENQUEUED_AT, _V3_JOB_ESTIMATED_DURATION)

#: 已发布版本的结构指纹：库内是这些版本且指纹一致才走迁移；同号不同指纹的旧开发库按 refuse 处理。
RUNTIME_RELEASED_CHECKSUMS = (
    (1, "68bbaa4dbedafb58de6a87d5f3a0153ce27165920c3797a9206237605a7e46ed"),
    (2, "8542e457cc32ffe4334964b33cd57f58dc3a0adc89d06505edd8eb2a32b1c443"),
)
