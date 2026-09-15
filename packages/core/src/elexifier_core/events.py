"""Project events over PostgreSQL LISTEN/NOTIFY (docs/PLAN.md 7.6).

Workers and the API publish with ``pg_notify('project_events', payload)``. The API process runs one
``EventBus`` listener connection and fans events out to per-project subscribers (SSE handlers).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

CHANNEL = "project_events"
MAX_PAYLOAD_BYTES = 7900  # pg_notify payload limit is 8000 bytes

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ProjectEvent:
    project_id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json(self) -> str:
        return json.dumps(
            {"project_id": self.project_id, "type": self.type, "data": self.data, "ts": self.ts},
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def from_json(cls, payload: str) -> ProjectEvent:
        raw = json.loads(payload)
        return cls(
            project_id=str(raw["project_id"]),
            type=str(raw["type"]),
            data=dict(raw.get("data") or {}),
            ts=str(raw.get("ts") or datetime.now(UTC).isoformat()),
        )


async def publish(
    session: AsyncSession | AsyncConnection,
    project_id: uuid.UUID | str,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Publish an event inside the caller's transaction (delivered on commit)."""
    event = ProjectEvent(project_id=str(project_id), type=event_type, data=data or {})
    payload = event.to_json()
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        event = ProjectEvent(
            project_id=event.project_id,
            type=event.type,
            data={
                "truncated": True,
                **{
                    k: v for k, v in event.data.items() if k in ("id", "job_id", "run_id", "status")
                },
            },
        )
        payload = event.to_json()
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload}
    )


class EventBus:
    """One LISTEN connection, many in-process subscribers."""

    def __init__(self, dsn: str, reconnect_delay_s: float = 2.0) -> None:
        self._dsn = dsn
        self._reconnect_delay_s = reconnect_delay_s
        self._subscribers: dict[str, set[asyncio.Queue[ProjectEvent]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._conn: asyncpg.Connection | None = None
        self._stopping = False
        self._ready = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="event-bus-listener")

    async def wait_ready(self, timeout_s: float = 10.0) -> bool:
        """Wait until the LISTEN connection is established. Returns False on timeout."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout_s)
        except TimeoutError:
            return False
        return True

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    async def stop(self) -> None:
        self._stopping = True
        self._ready.clear()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self._close_conn()

    async def _close_conn(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                conn = await asyncpg.connect(self._dsn)
                self._conn = conn
                await conn.add_listener(CHANNEL, self._on_notify)
                self._ready.set()
                log.info("event_bus.listening", channel=CHANNEL)
                while not self._stopping:
                    await asyncio.sleep(30)
                    await conn.execute("SELECT 1")  # keepalive; raises if the connection dropped
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("event_bus.disconnected", error=str(exc))
                self._ready.clear()
                await self._close_conn()
                await asyncio.sleep(self._reconnect_delay_s)

    def _on_notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            event = ProjectEvent.from_json(payload)
        except (ValueError, KeyError) as exc:
            log.warning("event_bus.bad_payload", error=str(exc))
            return
        self.dispatch(event)

    def dispatch(self, event: ProjectEvent) -> None:
        """Deliver an event to in-process subscribers (also used directly in tests)."""
        for queue in list(self._subscribers.get(event.project_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("event_bus.subscriber_slow", project_id=event.project_id)

    async def subscribe(self, project_id: uuid.UUID | str) -> AsyncGenerator[ProjectEvent]:
        """Async generator of events for one project. Consume with ``contextlib.aclosing`` so the
        subscription is removed as soon as the consumer stops."""
        key = str(project_id)
        queue: asyncio.Queue[ProjectEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(key, set()).add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subs = self._subscribers.get(key)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(key, None)

    @property
    def subscriber_count(self) -> int:
        return sum(len(s) for s in self._subscribers.values())
