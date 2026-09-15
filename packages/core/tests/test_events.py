from __future__ import annotations

import asyncio
from contextlib import aclosing

from elexifier_core.db.session import Database
from elexifier_core.events import EventBus, ProjectEvent, publish


async def test_dispatch_to_subscribers_without_database() -> None:
    bus = EventBus(dsn="postgresql://unused")
    received: list[ProjectEvent] = []

    async def consume() -> None:
        async with aclosing(bus.subscribe("p1")) as events:
            async for event in events:
                received.append(event)
                if len(received) == 2:
                    break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    bus.dispatch(ProjectEvent(project_id="p1", type="job.progress", data={"pct": 10}))
    bus.dispatch(ProjectEvent(project_id="other", type="job.progress", data={"pct": 99}))
    bus.dispatch(ProjectEvent(project_id="p1", type="job.finished"))
    await asyncio.wait_for(task, 2)
    assert [e.type for e in received] == ["job.progress", "job.finished"]
    assert received[0].data == {"pct": 10}
    assert bus.subscriber_count == 0


def test_event_json_round_trip() -> None:
    event = ProjectEvent(project_id="p", type="run.finished", data={"run_id": "r1"})
    parsed = ProjectEvent.from_json(event.to_json())
    assert parsed == event


async def test_publish_and_receive_through_postgres(migrated_db: str) -> None:
    dsn = migrated_db.replace("postgresql+asyncpg://", "postgresql://", 1)
    bus = EventBus(dsn=dsn)
    await bus.start()
    assert await bus.wait_ready(10)
    db = Database(url=migrated_db)
    try:
        subscription = bus.subscribe("proj-1")
        first = asyncio.ensure_future(anext(subscription))
        await asyncio.sleep(0)
        async with db.session() as session:
            await publish(session, "proj-1", "document.ready", {"document_id": "d1"})
            await session.commit()
        event = await asyncio.wait_for(first, 10)
        assert event.type == "document.ready"
        assert event.data == {"document_id": "d1"}
        await subscription.aclose()
    finally:
        await bus.stop()
        await db.dispose()
