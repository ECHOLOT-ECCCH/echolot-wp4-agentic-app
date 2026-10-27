"""Human-in-the-loop approval through the AI SDK 7 data-stream protocol (PLAN 7.3)."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import httpx

from .conftest import Workspace
from .test_chat import chat_body, new_conversation, parse_stream, user_message


def _assistant_parts_from_stream(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild the assistant message parts the browser would hold after the first stream."""
    parts: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        kind = chunk["type"]
        if kind == "tool-input-available":
            part = {
                "type": f"tool-{chunk['toolName']}",
                "toolCallId": chunk["toolCallId"],
                "state": "input-available",
                "input": chunk["input"],
            }
            by_id[chunk["toolCallId"]] = part
            parts.append(part)
        elif kind == "tool-output-available":
            by_id[chunk["toolCallId"]].update(
                {"state": "output-available", "output": chunk["output"]}
            )
        elif kind == "tool-approval-request":
            by_id[chunk["toolCallId"]].update(
                {"state": "approval-requested", "approval": {"id": chunk["approvalId"]}}
            )
    return parts


def _respond(parts: list[dict[str, Any]], approved: bool) -> list[dict[str, Any]]:
    out = copy.deepcopy(parts)
    for part in out:
        if part.get("state") == "approval-requested":
            part["state"] = "approval-responded"
            part["approval"] = {"id": part["approval"]["id"], "approved": approved}
            if not approved:
                part["approval"]["reason"] = "not now"
    return out


async def _first_turn(
    client: httpx.AsyncClient, workspace: Workspace
) -> tuple[str, list[dict[str, Any]]]:
    cid = await new_conversation(client, workspace)
    resp = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(user_message("Please run the demo action")),
    )
    assert resp.status_code == 200, resp.text
    chunks = parse_stream(resp.text)
    approval_requests = [c for c in chunks if c["type"] == "tool-approval-request"]
    assert len(approval_requests) == 1, [c["type"] for c in chunks]
    assert approval_requests[0]["toolCallId"].endswith("demo_requires_approval")
    # the run ends without executing the gated tool
    outputs = {c["toolCallId"] for c in chunks if c["type"] == "tool-output-available"}
    assert approval_requests[0]["toolCallId"] not in outputs
    return cid, _assistant_parts_from_stream(chunks)


async def test_approve_runs_the_gated_tool(client: httpx.AsyncClient, workspace: Workspace) -> None:
    cid, parts = await _first_turn(client, workspace)
    resp = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(
            user_message("Please run the demo action"),
            {"id": "m2", "role": "assistant", "parts": _respond(parts, approved=True)},
        ),
    )
    assert resp.status_code == 200, resp.text
    chunks = parse_stream(resp.text)
    output = next(
        c
        for c in chunks
        if c["type"] == "tool-output-available"
        and c["toolCallId"].endswith("demo_requires_approval")
    )
    assert output["output"]["summary"].startswith("Approved and performed")
    assert output["output"]["data"]["workspace"] == "Heritage"
    assert "tool-output-denied" not in {c["type"] for c in chunks}
    assert chunks[-1]["type"] == "finish"


async def test_deny_skips_the_gated_tool(client: httpx.AsyncClient, workspace: Workspace) -> None:
    cid, parts = await _first_turn(client, workspace)
    resp = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(
            user_message("Please run the demo action"),
            {"id": "m2", "role": "assistant", "parts": _respond(parts, approved=False)},
        ),
    )
    assert resp.status_code == 200, resp.text
    chunks = parse_stream(resp.text)
    types = [c["type"] for c in chunks]
    assert "tool-output-denied" in types
    denied_ids = {c["toolCallId"] for c in chunks if c["type"] == "tool-output-denied"}
    assert all(i.endswith("demo_requires_approval") for i in denied_ids)
    executed = {
        c["toolCallId"]
        for c in chunks
        if c["type"] == "tool-output-available"
        and c["toolCallId"].endswith("demo_requires_approval")
    }
    assert not executed
    # the model saw the denial reason (the test model echoes tool returns as text)
    text = "".join(c.get("delta", "") for c in chunks if c["type"] == "text-delta")
    assert "not now" in text
    assert chunks[-1]["type"] == "finish"


async def test_non_boolean_approval_is_rejected(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    cid, parts = await _first_turn(client, workspace)
    tampered = _respond(parts, approved=True)
    for part in tampered:
        if part.get("state") == "approval-responded":
            part["approval"]["approved"] = "true"  # strings must not open the gate
    resp = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(
            user_message("Please run the demo action"),
            {"id": "m2", "role": "assistant", "parts": tampered},
        ),
    )
    assert resp.status_code == 422
    assert "Approved and performed" not in resp.text


async def test_conversation_continues_after_an_approval(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    """The browser keeps the approved part in state approval-responded; the next turn must not
    treat it as a pending decision (regression: 'no unprocessed tool calls')."""
    cid, parts = await _first_turn(client, workspace)
    responded = _respond(parts, approved=True)
    approve = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(
            user_message("Please run the demo action"),
            {"id": "m2", "role": "assistant", "parts": responded},
        ),
    )
    assert approve.status_code == 200
    assert any(c["type"] == "tool-output-available" for c in parse_stream(approve.text))

    # persistence is asynchronous; wait until the server appended the continuation
    stored: list[dict[str, Any]] = []
    for _ in range(100):
        stored = (
            await client.get(f"/api/workspaces/{workspace.id}/conversations/{cid}/messages")
        ).json()
        if any(
            p.get("state") == "output-available"
            for m in stored
            for p in m["parts"]
            if p.get("type") == "tool-demo_requires_approval"
        ):
            break
        await asyncio.sleep(0.05)
    demo_parts = [
        p for m in stored for p in m["parts"] if p.get("type") == "tool-demo_requires_approval"
    ]
    assert [p["state"] for p in demo_parts] == ["output-available"]  # merged, not duplicated
    assert demo_parts[0]["approval"]["approved"] is True

    # a third turn as the browser would send it: its own stale copy of m2 plus the new question
    follow_up = await client.post(
        f"/api/workspaces/{workspace.id}/conversations/{cid}/chat",
        json=chat_body(
            user_message("Please run the demo action"),
            {"id": "m2", "role": "assistant", "parts": responded},
            user_message("And what files do I have?", "m3"),
        ),
    )
    assert follow_up.status_code == 200, follow_up.text
    chunks = parse_stream(follow_up.text)
    assert chunks[-1]["type"] == "finish"
    assert not any(c["type"] == "error" for c in chunks), [
        c for c in chunks if c["type"] == "error"
    ]
    # the test model answers from the history (it already called every tool)
    assert any(c["type"] == "text-delta" for c in chunks)
