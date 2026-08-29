"""``workflow.db`` 的完整 DDL（Workflow Authority 专用 SQLite 文件）。"""

from __future__ import annotations

WORKFLOW_STORE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workflow (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    name TEXT NOT NULL,
    tags TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workflow_node_template (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    authority_id TEXT NOT NULL,
    resource_template_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    class TEXT,
    goal TEXT NOT NULL,
    goal_default TEXT NOT NULL,
    feedback TEXT NOT NULL,
    result TEXT NOT NULL,
    schema TEXT,
    type TEXT NOT NULL,
    icon TEXT,
    header TEXT,
    footer TEXT,
    node_type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_workflow_node_template_authority
    ON workflow_node_template(authority_id);

CREATE TABLE IF NOT EXISTS workflow_handle_template (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    authority_id TEXT NOT NULL,
    workflow_node_template_uuid TEXT NOT NULL,
    handle_key TEXT NOT NULL,
    io_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    type TEXT NOT NULL,
    required INTEGER NOT NULL,
    data_source TEXT,
    data_key TEXT
);
CREATE INDEX IF NOT EXISTS ix_workflow_handle_template_node
    ON workflow_handle_template(workflow_node_template_uuid);
CREATE INDEX IF NOT EXISTS ix_workflow_handle_template_authority
    ON workflow_handle_template(authority_id);

CREATE TABLE IF NOT EXISTS workflow_node (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_uuid TEXT NOT NULL,
    workflow_node_template_uuid TEXT,
    parent_uuid TEXT,
    material_uuid TEXT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    type TEXT NOT NULL,
    icon TEXT,
    pose TEXT NOT NULL,
    param TEXT NOT NULL,
    footer TEXT,
    action_name TEXT,
    action_type TEXT,
    execution_policy TEXT NOT NULL,
    disabled INTEGER NOT NULL,
    minimized INTEGER NOT NULL,
    script TEXT,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);
CREATE INDEX IF NOT EXISTS ix_workflow_node_workflow
    ON workflow_node(workflow_uuid);

CREATE TABLE IF NOT EXISTS workflow_edge (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_uuid TEXT NOT NULL,
    source_node_uuid TEXT NOT NULL,
    target_node_uuid TEXT NOT NULL,
    source_handle_uuid TEXT NOT NULL,
    target_handle_uuid TEXT NOT NULL,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);
CREATE INDEX IF NOT EXISTS ix_workflow_edge_workflow
    ON workflow_edge(workflow_uuid);

CREATE TABLE IF NOT EXISTS workflow_task (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_uuid TEXT NOT NULL,
    status TEXT NOT NULL,
    workflow_snapshot TEXT NOT NULL,
    execution_plan TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    target_node_uuid TEXT,
    control_status TEXT NOT NULL,
    cleanup_status TEXT NOT NULL,
    trace_context TEXT NOT NULL,
    input TEXT NOT NULL,
    output TEXT NOT NULL,
    error_info TEXT NOT NULL,
    timeout_at TEXT,
    attention_reason TEXT,
    terminal_ghost_detected_at TEXT,
    reconciliation_resume_control_status TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);
CREATE INDEX IF NOT EXISTS ix_workflow_task_workflow
    ON workflow_task(workflow_uuid);
CREATE INDEX IF NOT EXISTS ix_workflow_task_status
    ON workflow_task(status);

CREATE TABLE IF NOT EXISTS workflow_node_job (
    uuid TEXT PRIMARY KEY,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    deleted_at TEXT,
    description TEXT,
    meta_data TEXT NOT NULL,
    workflow_task_uuid TEXT NOT NULL,
    workflow_node_uuid TEXT NOT NULL,
    material_uuid TEXT,
    edge_agent_uuid TEXT,
    edge_command_uuid TEXT,
    job_access_token_hash TEXT NOT NULL DEFAULT '',
    feedback_sequence INTEGER NOT NULL,
    topological_index INTEGER NOT NULL,
    executor_kind TEXT NOT NULL,
    execution_policy TEXT NOT NULL,
    execution_timeout_seconds INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    param TEXT NOT NULL,
    feedback_data TEXT NOT NULL,
    return_info TEXT NOT NULL,
    control_data TEXT NOT NULL,
    error_info TEXT NOT NULL,
    dispatch_deadline_at TEXT,
    execution_deadline_at TEXT,
    cancel_command_uuid TEXT,
    cancel_ack_deadline_at TEXT,
    cancel_complete_deadline_at TEXT,
    uncertainty_reason TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(workflow_task_uuid) REFERENCES workflow_task(uuid)
);
CREATE INDEX IF NOT EXISTS ix_workflow_node_job_task
    ON workflow_node_job(workflow_task_uuid);
CREATE INDEX IF NOT EXISTS ix_workflow_node_job_node
    ON workflow_node_job(workflow_node_uuid);

CREATE TABLE IF NOT EXISTS workflow_source_registration (
    workflow_uuid TEXT PRIMARY KEY,
    package_id TEXT NOT NULL,
    package_root TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    create_time TEXT NOT NULL,
    update_time TEXT NOT NULL,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_source_registration_path
    ON workflow_source_registration(package_root, relative_path);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_source_registration_uri
    ON workflow_source_registration(source_uri);

CREATE TABLE IF NOT EXISTS workflow_authoring (
    workflow_uuid TEXT PRIMARY KEY,
    observed_draft_hash TEXT,
    draft_update_time TEXT,
    diagnostics TEXT NOT NULL,
    candidate_hash TEXT,
    candidate TEXT,
    applied_source TEXT,
    writeback_status TEXT NOT NULL DEFAULT 'settled',
    writeback_source TEXT,
    writeback_expected_hash TEXT,
    writeback_generation TEXT,
    update_time TEXT NOT NULL,
    FOREIGN KEY(workflow_uuid) REFERENCES workflow(uuid)
);

CREATE TABLE IF NOT EXISTS frontend_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    data TEXT NOT NULL,
    create_time TEXT NOT NULL
);
"""


__all__ = ["WORKFLOW_STORE_SCHEMA"]
