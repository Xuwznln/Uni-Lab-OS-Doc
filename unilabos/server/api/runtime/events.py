"""复用 /events：持久工作流通知 + 瞬时日志失效通知，不混用游标。"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response, StreamingResponse

from unilabos.protocol.runtime.logs import RuntimeLogNotice
from unilabos.server.lifecycle import shutting_down
from unilabos.utils.log_notices import LOG_NOTICE_EVENT, log_notices, merge_notices


async def runtime_event_stream(request: Request, service=None, cursor: int = 0):
    from unilabos.server.api.runtime.workflow import format_sse_event

    loop = asyncio.get_running_loop()
    pending: asyncio.Queue[RuntimeLogNotice] = asyncio.Queue(maxsize=1)
    closed = False

    def put(notice: RuntimeLogNotice):
        if closed:
            return
        if pending.full():
            notice = merge_notices(pending.get_nowait(), notice)
        pending.put_nowait(notice)

    def receive(notice: RuntimeLogNotice):
        if not closed:
            loop.call_soon_threadsafe(put, notice)

    release = log_notices.subscribe(receive)
    next_workflow = 0.0
    heartbeat = time.monotonic() + 15
    try:
        yield "retry: 3000\n: connected\n\n"
        while not shutting_down() and not await request.is_disconnected():
            now = time.monotonic()
            if now >= next_workflow:
                next_workflow = now + 1
                if service is not None:
                    events = service.list_events(after_id=cursor, limit=100)["items"]
                    for event in events:
                        cursor = event["id"]
                        yield format_sse_event(event)
                    if len(events) == 100:
                        next_workflow = now
            try:
                notice = await asyncio.wait_for(pending.get(), timeout=max(.01, next_workflow - time.monotonic()))
            except TimeoutError:
                if time.monotonic() >= heartbeat:
                    yield ": keepalive\n\n"
                    heartbeat = time.monotonic() + 15
                continue
            # 不写 SSE id：Last-Event-ID 继续只表示工作流持久事件的序号。
            yield f"event: {LOG_NOTICE_EVENT}\ndata: {notice.model_dump_json()}\n\n"
    finally:
        closed = True
        release()


def create_runtime_events_router(service=None) -> APIRouter:
    router = APIRouter(tags=["runtime-events"])

    @router.get("/events", name="runtime_events")
    async def events(request: Request, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> Response:
        from unilabos.server.api.runtime.workflow import _GO_WHITE_SPACE, _error, _parse_non_negative_int64_decimal
        from unilabos.server.services.runtime.workflow.service import WorkflowError

        try:
            raw = next((value for name, value in request.scope["headers"] if name.lower() == b"last-event-id"), None)
            value = (raw.decode("utf-8") if raw is not None else last_event_id or "").strip(_GO_WHITE_SPACE)
            cursor = _parse_non_negative_int64_decimal(value) if value else 0
        except (ValueError, UnicodeError):
            return _error(WorkflowError("invalid_input"))
        return StreamingResponse(
            runtime_event_stream(request, service, cursor), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
