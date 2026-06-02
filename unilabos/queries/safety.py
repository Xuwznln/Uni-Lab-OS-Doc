from __future__ import annotations

from typing import List

from unilabos.queries.engine import QueryEngine
from unilabos.queries.models import SafetyZone


def query_safety_zones(engine: QueryEngine) -> List[SafetyZone]:
    return engine.query_safety_zones()
