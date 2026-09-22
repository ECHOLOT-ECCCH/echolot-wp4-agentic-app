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


async def test_chat_attaches_selection_context(
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    logged_in.apply(client)
    pid = await _project_with_test_model(client)
    cid = (await client.post(f"/api/projects/{pid}/conversations", json={})).json()["id"]
    body = _chat_body(_user_message("What does this say?"))
    body["context"] = [
        {
            "document_id": str(uuid.uuid4()),
            "page": 2,
            "bbox": [50, 100, 280, 130],
            "text": "abbey adv. a monastery or convent",
            "words": 6,
        }
    ]
    resp = await client.post(f"/api/projects/{pid}/conversations/{cid}/chat", json=body)
    assert resp.status_code == 200, resp.text
    stored = (await client.get(f"/api/projects/{pid}/conversations/{cid}/messages")).json()
    user = stored[0]
    kinds = [p["type"] for p in user["parts"]]
    assert kinds == ["text", "text", "data-context"]
    assert user["parts"][1]["text"].startswith("[Selected on page 2, box (50, 100, 280, 130)")
    assert "abbey adv." in user["parts"][1]["text"]
    assert user["parts"][2]["data"]["page"] == 2

    bad = _chat_body(_user_message("again"))
    bad["context"] = [{"page": "x"}]
    assert (
        await client.post(f"/api/projects/{pid}/conversations/{cid}/chat", json=bad)
    ).status_code == 422


async def _parsed_project_with_test_model(
    client: httpx.AsyncClient,
) -> tuple[str, dict[str, Any]]:
    from elexifier_api.jobs import documents as jobs_documents
    from elexifier_pipeline.sample import OXFORD_XML

    pid = await _project_with_test_model(client)
    resp = await client.post(
        f"/api/projects/{pid}/files", params={"name": "oxford.xml"}, content=OXFORD_XML.read_bytes()
    )
    assert resp.status_code == 201, resp.text
    doc = (await client.get(f"/api/projects/{pid}/documents")).json()[0]
    await jobs_documents.parse_document(doc["id"])
    entries = (
        await client.get(f"/api/projects/{pid}/documents/{doc['id']}/entries", params={"limit": 2})
    ).json()["items"]
    return pid, entries[1]


async def test_chat_tells_the_model_what_is_open_and_keeps_it_across_turns(
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    logged_in.apply(client)
    pid, entry = await _parsed_project_with_test_model(client)
    cid = (await client.post(f"/api/projects/{pid}/conversations", json={})).json()["id"]
    chat = f"/api/projects/{pid}/conversations/{cid}/chat"

    body = _chat_body(_user_message("is this entry right?"))
    body["viewing"] = {"kind": "entry", "entryId": entry["id"], "runId": None}
    assert (await client.post(chat, json=body)).status_code == 200
    stored = (await client.get(f"/api/projects/{pid}/conversations/{cid}/messages")).json()
    parts = stored[0]["parts"]
    assert [p["type"] for p in parts] == ["text", "text", "data-viewing"]
    assert parts[1]["text"] == (
        "[Looking at: entry 2 “b” of oxford.xml, converted live with the latest mapping]"
    )
    assert parts[2]["data"]["ordinal"] == 1 and parts[2]["data"]["kind"] == "entry"

    # the next turn: the browser resends its own copy of the first message (no server parts)
    # plus the new one; the note survives on the first and the new one gets its own
    second = _chat_body(
        _user_message("is this entry right?"), *stored[1:], _user_message("and the mapping?", "m2")
    )
    second["viewing"] = {"kind": "mapping", "version": 2}
    assert (await client.post(chat, json=second)).status_code == 200
    stored = (await client.get(f"/api/projects/{pid}/conversations/{cid}/messages")).json()
    users = [m for m in stored if m["role"] == "user"]
    assert users[0]["parts"] == parts
    assert users[1]["parts"][1]["text"] == "[Looking at: the mapping editor, version 2]"

    # nothing open, or something of another project: no note; a malformed target is ignored
    third = _chat_body(*stored, _user_message("thanks", "m3"))
    third["viewing"] = {"kind": "entry", "entryId": str(uuid.uuid4())}
    assert (await client.post(chat, json=third)).status_code == 200
    stored = (await client.get(f"/api/projects/{pid}/conversations/{cid}/messages")).json()
    assert [p["type"] for p in stored[-2]["parts"] if stored[-2]["role"] == "user"] in (
        ["text"],
        [],
    )
    fourth = _chat_body(*stored, _user_message("bye", "m4"))
    fourth["viewing"] = {"kind": "entry", "entryId": "not-a-uuid"}
    assert (await client.post(chat, json=fourth)).status_code == 200


async def test_chat_attaches_an_entry_as_shown(
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    logged_in.apply(client)
    pid = await _project_with_test_model(client)
    cid = (await client.post(f"/api/projects/{pid}/conversations", json={})).json()["id"]
    body = _chat_body(_user_message("the sense numbering here is wrong"))
    body["context"] = [
        {
            "kind": "entry",
            "id": "entry:e1",
            "label": "entry 8 babble",
            "entry_id": str(uuid.uuid4()),
            "document_id": str(uuid.uuid4()),
            "ordinal": 7,
            "headword": "babble",
            "document_name": "oxford.xml",
            "origin": "converted live with mapping v3",
            "source_text": "<se><hw>babble</hw><s1>...</s1></se>",
            "output_xml": '<entry id="e8"><headword>babble</headword></entry>',
            "issues": ["sense[1]: sense has no content"],
        }
    ]
    resp = await client.post(f"/api/projects/{pid}/conversations/{cid}/chat", json=body)
    assert resp.status_code == 200, resp.text
    stored = (await client.get(f"/api/projects/{pid}/conversations/{cid}/messages")).json()
    parts = stored[0]["parts"]
    assert [p["type"] for p in parts] == ["text", "text", "data-context"]
    assert parts[1]["text"].startswith("[Attached: entry 8 “babble” of oxford.xml; converted live")
    assert "<hw>babble</hw>" in parts[1]["text"] and "Issues: sense[1]" in parts[1]["text"]
    assert parts[2]["data"]["kind"] == "entry" and "id" not in parts[2]["data"]
