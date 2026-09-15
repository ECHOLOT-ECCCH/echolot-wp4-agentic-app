"""Server-sent events formatting and the per-project event stream generator."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from elexifier_core.events import EventBus, ProjectEvent

HEARTBEAT_S = 15


def format_event(event: ProjectEvent) -> str:
    payload = json.dumps(
        {"project_id": event.project_id, "type": event.type, "data": event.data, "ts": event.ts},
        default=str,
    )
    return f"event: {event.type}\ndata: {payload}\n\n"


async def project_event_stream(
    bus: EventBus,
    project_id: uuid.UUID | str,
    *,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    heartbeat_s: float = HEARTBEAT_S,
    max_events: int | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for one project until the client disconnects.

    ``max_events`` bounds the stream (tests). Heartbeat comments keep proxies from closing idle
    connections and give the loop a chance to notice disconnects.
    """
    yield ": connected\n\n"
    delivered = 0
    async with bus.subscription(project_id) as queue:
        while max_events is None or delivered < max_events:
            if is_disconnected is not None and await is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), heartbeat_s)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            delivered += 1
            yield format_event(event)
