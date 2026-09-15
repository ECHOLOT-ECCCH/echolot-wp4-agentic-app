"""Server-sent events per project (docs/PLAN.md 7.6)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from elexifier_api.authz import get_project_or_404
from elexifier_api.deps import CurrentEventBus, CurrentUser, DbSession
from elexifier_api.sse import project_event_stream

router = APIRouter(prefix="/api/projects/{project_id}/events", tags=["events"])


@router.get("")
async def project_events(
    project_id: uuid.UUID,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    bus: CurrentEventBus,
) -> StreamingResponse:
    await get_project_or_404(db, project_id, user)
    return StreamingResponse(
        project_event_stream(bus, project_id, is_disconnected=request.is_disconnected),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
