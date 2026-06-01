from __future__ import annotations

from typing import List, Optional

from unilabos.queries.engine import QueryEngine
from unilabos.queries.models import QueryAffordance


def query_affordance(engine: QueryEngine, target: str, kind: Optional[str] = None) -> List[QueryAffordance]:
    return engine.query_affordance(target, kind=kind)
