from __future__ import annotations

from typing import Optional

from unilabos.queries.engine import QueryEngine
from unilabos.queries.models import Pose


def query_pose(engine: QueryEngine, target: str, frame: Optional[str] = None) -> Pose:
    return engine.query_pose(target, frame=frame)
