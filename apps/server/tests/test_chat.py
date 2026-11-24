from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from echolot_app.db import AgentRun, Message
from echolot_app.main import create_app
from echolot_app.settings import Settings
from echolot_app.state import AppState

from .conftest import Workspace, add_user, api_client, sign_in, upload


def user_message(text: str, message_id: str = "m1") -> dict[str, Any]:
    return {"id": message_id, "role": "user", "parts": [{"type": "text", "text": text}]}


def chat_body(*messages: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"trigger": "submit-message", "id": "chat-1", "messages": list(messages), **extra}


def parse_stream(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


async def new_conversation(client: httpx.AsyncClient, workspace: Workspace) -> str:
    created = await client.post(f"/api/workspaces/{workspace.id}/conversations", json={})
    assert created.status_code == 201
    return created.json()["id"]


async def test_conversations_crud(client: httpx.AsyncClient, workspace: Workspace) -> None:
    base = f"/api/workspaces/{workspace.id}/conversations"
    cid = await new_conversation(client, workspace)
    assert (await client.get(f"{base}/{cid}")).json()["title"] == "New conversation"
    assert [c["id"] for c in (await client.get(base)).json()] == [cid]
    assert (await client.get(f"{base}/{cid}/messages")).json() == []
    put = await client.put(
        f"{base}/{cid}/messages", json={"messages": [user_message("  Link   my people  ")]}
    )
    assert put.status_code == 204
    assert (await client.get(f"{base}/{cid}")).json()["title"] == "Link my people"
    assert (await client.delete(f"{base}/{cid}")).status_code == 204
    assert (await client.get(f"{base}/{cid}")).status_code == 404


async def test_chat_streams_and_persists(
    client: httpx.AsyncClient, workspace: Workspace, state: AppState
) -> None:
    cid = await new_conversation(client, workspace)
    resp = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(user_message("What files do I have?")),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    chunks = parse_stream(resp.text)
    types = [c["type"] for c in chunks]
    assert types[0] == "start"
    assert types[-1] == "finish"
    # the test model calls every tool it sees: the file tools run, the demo tool asks approval
    assert "tool-output-available" in types
    assert "tool-approval-request" in types

    run: AgentRun | None = None
    for _ in range(50):  # persistence happens in on_complete after the stream ends
        async with state.db.session() as db:
            run = (await db.execute(select(AgentRun))).scalar_one()
            if run.status == "done":
                break
        await asyncio.sleep(0.05)
    assert run is not None and run.status == "done"
    assert run.model == {"provider": "test", "model_id": "test", "key": "server"}
    assert run.usage["requests"] >= 1
    assert run.messages

    async with state.db.session() as db:
        stored = (await db.execute(select(Message).order_by(Message.position))).scalars().all()
        roles = [m.ui_message["role"] for m in stored]
    assert roles[0] == "user" and "assistant" in roles
    listed = await client.get(f"/api/workspaces/{workspace.id}/conversations/{cid}/messages")
    assert [m["role"] for m in listed.json()] == roles


async def test_viewing_line_names_the_open_row(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    fid = await upload(client, workspace.id, "people.csv", b"name\nAna\nJon\n")
    cid = await new_conversation(client, workspace)
    resp = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(
            user_message("Is this one right?"), viewing={"kind": "file", "fileId": fid, "row": 1}
        ),
    )
    assert resp.status_code == 200
    stored = (
        await client.get(f"/api/workspaces/{workspace.id}/conversations/{cid}/messages")
    ).json()
    texts = [p.get("text") for p in stored[0]["parts"] if p["type"] == "text"]
    assert texts[1] == f"[Looking at: row 1 of the file people.csv (file_id {fid})]"


async def test_bad_requests(client: httpx.AsyncClient, workspace: Workspace) -> None:
    cid = await new_conversation(client, workspace)
    url = f"/api/workspaces/{workspace.id}/conversations/{cid}/chat"
    assert (await client.post(url, json={"messages": []})).status_code == 400
    assert (await client.post(url, content=b"not json")).status_code == 400
    other = await client.post("/api/workspaces", json={"name": "Other"})
    foreign = f"/api/workspaces/{other.json()['id']}/conversations/{cid}/chat"
    assert (await client.post(foreign, json=chat_body(user_message("hi")))).status_code == 404


async def test_without_a_usable_model_the_chat_says_what_to_do(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        model_provider="anthropic",  # no server key, and the user has none either
        model_id="x",
    )
    app = create_app(settings)
    async with LifespanManager(app):
        await add_user(app.state.app, "ana@example.org")
        async with api_client(app) as client:
            await sign_in(client, "ana@example.org")
            wid = (await client.post("/api/workspaces", json={"name": "w"})).json()["id"]
            cid = (await client.post(f"/api/workspaces/{wid}/conversations", json={})).json()["id"]
            resp = await client.post(
                f"/api/workspaces/{wid}/conversations/{cid}/chat",
                json=chat_body(user_message("hi")),
            )
    assert resp.status_code == 409
    assert "add a key" in resp.json()["detail"]
