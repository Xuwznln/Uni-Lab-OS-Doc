"""``runtime.db`` 域的表映射与库规格（运行控制 + workflow + registry）。

runtime.db 是"运行过程产生/服务运行"事实的单库单写者：

- ``control``：微后端命令与执行控制（backend_session / command_inbox /
  execution_job / adapter outbox-inbox 等）；
- ``workflow``：Workflow Authority 的定义 / 任务 / 作业 / 前端事件；
- ``registry``：Edge 注册表快照（条目级版本模型三表）。

对外导入只看表：一律 ``from unilabos.server.database.tables.runtime
import X``，不感知域内文件拆分；库级规格 ``RUNTIME_DATABASE`` 在此聚合。
"""

from unilabos.server.database.schema import SCHEMA_IDENTITY_TABLE, DatabaseSpec
from unilabos.server.database.tables.runtime.data import (
    DATA_TABLES,
    DATA_TABLE_MODELS,
    AdapterCommandOutboxRecord,
    AdapterEventInboxRecord,
    BackendEventOutboxRecord,
    BackendSessionRecord,
    CommandInboxRecord,
    DeviceActionCapability,
    DeviceRoute,
    ExecutionJobRecord,
    ExecutorEndpointRecord,
    MaterialBinding,
    Transport,
)
from unilabos.server.database.tables.runtime.registry import (
    REGISTRY_TABLES,
    REGISTRY_TABLE_MODELS,
    RegistryEntryRecord,
    RegistryEntryStateRecord,
    RegistryReportRecord,
)
from unilabos.server.database.tables.runtime.workflow import (
    WORKFLOW_TABLES,
    WORKFLOW_TABLE_MODELS,
    ExecutionLockLeaseRecord,
    FrontendEventRecord,
    WorkflowAuthoringRecord,
    WorkflowEdgeRecord,
    WorkflowHandleTemplateRecord,
    WorkflowInterventionRecord,
    WorkflowManualConfirmationRecord,
    WorkflowNodeJobFeedbackHistoryRecord,
    WorkflowNodeJobRecord,
    WorkflowNodeJobResultRecord,
    WorkflowNodeRecord,
    WorkflowNodeTemplateRecord,
    WorkflowRecord,
    WorkflowSourceRegistrationRecord,
    WorkflowTaskCommandRecord,
    WorkflowTaskRecord,
)


RUNTIME_TABLE_MODELS = (
    *DATA_TABLE_MODELS,
    *WORKFLOW_TABLE_MODELS,
    *REGISTRY_TABLE_MODELS,
)


# control / workflow / registry 同域同库：都是"运行过程产生/服务运行"的
# 事实（调度任务、frontend 事件、edge 注册表快照），合并为单库单写者，
# 进程内 join 查询直达。表定义留在各自模块，这里做库级规格聚合。
RUNTIME_DATABASE = DatabaseSpec(
    key="runtime",
    filename="runtime.db",
    role=(
        "critical microbackend command and execution control, "
        "workflow authority and registry snapshots"
    ),
    synchronous="FULL",
    tables=(
        *DATA_TABLES,
        *(spec for spec in WORKFLOW_TABLES if spec is not SCHEMA_IDENTITY_TABLE),
        *(spec for spec in REGISTRY_TABLES if spec is not SCHEMA_IDENTITY_TABLE),
    ),
)


__all__ = [
    # 库级聚合
    "RUNTIME_DATABASE",
    "RUNTIME_TABLE_MODELS",
    # control（运行控制）
    "AdapterCommandOutboxRecord",
    "AdapterEventInboxRecord",
    "BackendEventOutboxRecord",
    "BackendSessionRecord",
    "CommandInboxRecord",
    "DeviceActionCapability",
    "DeviceRoute",
    "ExecutionJobRecord",
    "ExecutorEndpointRecord",
    "MaterialBinding",
    "Transport",
    # workflow（Workflow Authority）
    "ExecutionLockLeaseRecord",
    "FrontendEventRecord",
    "WorkflowAuthoringRecord",
    "WorkflowEdgeRecord",
    "WorkflowHandleTemplateRecord",
    "WorkflowInterventionRecord",
    "WorkflowManualConfirmationRecord",
    "WorkflowNodeJobFeedbackHistoryRecord",
    "WorkflowNodeJobRecord",
    "WorkflowNodeJobResultRecord",
    "WorkflowNodeRecord",
    "WorkflowNodeTemplateRecord",
    "WorkflowRecord",
    "WorkflowSourceRegistrationRecord",
    "WorkflowTaskCommandRecord",
    "WorkflowTaskRecord",
    # registry（注册表快照）
    "RegistryEntryRecord",
    "RegistryEntryStateRecord",
    "RegistryReportRecord",
]
