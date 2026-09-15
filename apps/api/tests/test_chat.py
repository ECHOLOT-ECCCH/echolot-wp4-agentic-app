from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
from sqlalchemy import select

from elexifier_api.state import AppState
from elexifier_core.db.models import AgentRun, Message, UsageRecord
from elexifier_core.events import publish

from .conftest import LoggedIn


def _user_message(text: str, message_id: str = "m1") -> dict[str, Any]:
    return {"id": message_id, "role": "user", "parts": [{"type": "text", "text": text}]}


def _chat_body(*messages: dict[str, Any], model: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"trigger": "submit-message", "id": "chat-1", "messages": list(messages)}
    if model:
        body["model"] = model
    return body


def _parse_stream(text: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            chunks.append(json.loads(line[6:]))
    return chunks


async def _project_with_test_model(client: httpx.AsyncClient) -> str:
    created = await client.post("/api/projects", json={"name": "Chat", "source_type": "xml"})
    pid = created.json()["id"]
    await client.patch(
        f"/api/projects/{pid}", json={"default_model": {"provider": "test", "model_id": "test"}}
    )
    return pid


async def test_conversations_crud(client: httpx.AsyncClient, logged_in: LoggedIn) -> None:
    logged_in.apply(client)
    pid = await _project_with_test_model(client)
    created = await client.post(f"/api/projects/{pid}/conversations", json={})
    assert created.status_code == 201
    cid = created.json()["id"]
    assert created.json()["title"] == "New conversation"
    assert [c["id"] for c in (await client.get(f"/api/projects/{pid}/conversations")).json()] == [
        cid
    ]
    assert (await client.get(f"/api/projects/{pid}/conversations/{cid}/messages")).json() == []

    put = await client.put(
        f"/api/projects/{pid}/conversations/{cid}/messages",
        json={"messages": [_user_message("  Convert   my dictionary  ")]},
    )
    assert put.status_code == 204
    assert (await client.get(f"/api/projects/{pid}/conversations/{cid}")).json()["title"] == (
        "Convert my dictionary"
    )
    deleted = await client.delete(f"/api/projects/{pid}/conversations/{cid}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/projects/{pid}/conversations/{cid}")).status_code == 404


async def test_chat_streams_and_persists(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid = await _project_with_test_model(client)
    cid = (await client.post(f"/api/projects/{pid}/conversations", json={})).json()["id"]

    resp = await client.post(
        f"/api/projects/{pid}/conversations/{cid}/chat",
        json=_chat_body(_user_message("What files do I have?")),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    chunks = _parse_stream(resp.text)
    types = [c["type"] for c in chunks]
    assert types[0] == "start"
    assert types[-1] == "finish"
    # the test model calls every tool it sees: list_files runs, the dev demo tool asks for approval
    assert any(t.startswith("tool-input") for t in types)
    assert any(t == "tool-output-available" for t in types)
    assert "tool-approval-request" in types

    # persistence happens in on_complete after the stream ends
    run: AgentRun | None = None
    for _ in range(50):
        async with app_state.db.session() as db:
            run = (await db.execute(select(AgentRun))).scalar_one()
            if run.status == "done":
                break
        await asyncio.sleep(0.05)
    assert run is not None and run.status == "done"
    assert run.model == {"provider": "test", "model_id": "test", "credential_id": None}
    assert run.usage and run.usage["requests"] >= 1
    assert isinstance(run.messages, list) and run.messages

    async with app_state.db.session() as db:
        stored = (await db.execute(select(Message).order_by(Message.position))).scalars().all()
        roles = [m.ui_message["role"] for m in stored]
        assert roles[0] == "user"
        assert "assistant" in roles
        usage = (await db.execute(select(UsageRecord))).scalar_one()
        assert usage.purpose == "chat" and usage.model == "test"

    listed = await client.get(f"/api/projects/{pid}/conversations/{cid}/messages")
    assert [m["role"] for m in listed.json()] == roles


async def test_chat_requires_model_and_valid_body(
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    logged_in.apply(client)
    created = await client.post("/api/projects", json={"name": "NoModel", "source_type": "xml"})
    pid = created.json()["id"]
    cid = (await client.post(f"/api/projects/{pid}/conversations", json={})).json()["id"]
    no_model = await client.post(
        f"/api/projects/{pid}/conversations/{cid}/chat", json=_chat_body(_user_message("hi"))
    )
    assert no_model.status_code == 400
    assert "Choose a model" in no_model.json()["detail"]

    needs_key = await client.post(
        f"/api/projects/{pid}/conversations/{cid}/chat",
        json=_chat_body(_user_message("hi"), model={"provider": "openrouter", "model_id": "x/y"}),
    )
    assert needs_key.status_code == 400
    assert "credential" in needs_key.json()["detail"].lower()

    empty = await client.post(
        f"/api/projects/{pid}/conversations/{cid}/chat", json={"messages": []}
    )
    assert empty.status_code == 400


async def test_events_stream_delivers_project_events(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    """The endpoint authorizes; the generator is exercised directly because httpx's in-process
    ASGI transport buffers streaming responses."""
    from elexifier_api.sse import project_event_stream

    logged_in.apply(client)
    pid = await _project_with_test_model(client)
    assert await app_state.event_bus.wait_ready(10)

    async def consume() -> list[str]:
        frames: list[str] = []
        async for frame in project_event_stream(
            app_state.event_bus, pid, heartbeat_s=0.2, max_events=1
        ):
            frames.append(frame)
        return frames

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.5)  # lets at least one heartbeat go out before the event
    async with app_state.db.session() as db:
        await publish(db, pid, "document.ready", {"document_id": "d1"})
        await db.commit()
    frames = await asyncio.wait_for(task, 10)
    assert frames[0] == ": connected\n\n"
    assert ": ping\n\n" in frames
    event_frame = frames[-1]
    assert event_frame.startswith("event: document.ready\ndata: ")
    payload = json.loads(event_frame.split("data: ", 1)[1].strip())
    assert payload["data"] == {"document_id": "d1"}
    assert app_state.event_bus.subscriber_count == 0

    # endpoint-level authorization: unknown project is 404, no session is 401
    assert (await client.get(f"/api/projects/{uuid.uuid4()}/events")).status_code == 404
    client.cookies.clear()
    assert (await client.get(f"/api/projects/{pid}/events")).status_code == 401
