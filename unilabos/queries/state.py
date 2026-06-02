from __future__ import annotations

from unilabos.queries.engine import QueryEngine
from unilabos.queries.models import State


def query_state(engine: QueryEngine, target: str) -> State:
    return engine.query_state(target)
