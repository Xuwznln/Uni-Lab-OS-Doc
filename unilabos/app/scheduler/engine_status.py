"""调度查询的时间投影；只呈现平台证据，不估时、不产生执行授权。"""

from __future__ import annotations

from typing import Any

from unilabos.workflow.execution_started import execution_start_times
from unilabos.workflow.store import WorkflowStore


def execution_timing(
    rows: list[dict[str, Any]], *, store: WorkflowStore | None, now: float,
) -> list[dict[str, Any]]:
    """将旧缓存中的派发时间显式分离；实际开始只取不可变设备回执。"""
    starts: dict[str, int] = {}
    if store is not None:
        with store.read() as connection:
            starts = execution_start_times(connection, [row["job_id"] for row in rows])
    result = []
    for row in rows:
        dispatched = row["started_at"]
        started_ms = starts.get(row["job_id"])
        started = started_ms / 1000 if started_ms is not None else None
        end = row.get("ended_at", now)
        # 跨设备时钟可能不一致；保留原始证据，不把负时差夹成零耗时。
        elapsed = round(end - started, 3) if started is not None and end >= started else None
        result.append({
            **row, "dispatched_at": dispatched, "started_at": started,
            "dispatch_elapsed_s": round(end - dispatched, 3) if end >= dispatched else None,
            "estimated_s": None, "estimate_source": "unknown",
            **({"actual_s": elapsed, "timing_boundary": "execution_started_to_observed_completion"}
               if "ended_at" in row else {"elapsed_s": elapsed, "timing_boundary": "execution_started_to_now"}),
        })
    return result
