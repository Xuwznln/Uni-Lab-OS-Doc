"""微后端领域服务（目录严格按四库划分）。

- ``services/runtime/``：runtime.db（RuntimeService / workflow 子包 / RegistryService）
- ``services/materials/``：materials.db（MaterialsService / GraphService / 快照对比）
- ``services/telemetry.py``：telemetry.db
- ``services/history.py``：history.db
"""

from unilabos.server.services.history import HistoryService
from unilabos.server.services.materials import (
    MaterialConflictError,
    MaterialNoChangeError,
    MaterialNotFoundError,
    MaterialValidationError,
    MaterialsService,
    MaterialsServiceError,
    RejectedMutationError,
    compare_material_snapshot,
    snapshot_state_hash,
)
from unilabos.server.services.runtime import RuntimeService
from unilabos.server.services.telemetry import TelemetryService

__all__ = [
    "MaterialConflictError",
    "MaterialNoChangeError",
    "MaterialNotFoundError",
    "MaterialValidationError",
    "MaterialsService",
    "MaterialsServiceError",
    "HistoryService",
    "RejectedMutationError",
    "RuntimeService",
    "TelemetryService",
    "compare_material_snapshot",
    "snapshot_state_hash",
]
