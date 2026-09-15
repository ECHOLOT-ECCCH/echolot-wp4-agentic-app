"""Human-in-the-loop approval through the AI SDK 7 data-stream protocol (PLAN 7.3)."""

from __future__ import annotations

import copy
from typing import Any

import httpx

from .conftest import LoggedIn
from .test_chat import _chat_body, _parse_stream, _project_with_test_model, _user_message


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


async def _first_turn(client: httpx.AsyncClient) -> tuple[str, str, list[dict[str, Any]]]:
    pid = await _project_with_test_model(client)
    cid = (await client.post(f"/api/projects/{pid}/conversations", json={})).json()["id"]
    resp = await client.post(
        f"/api/projects/{pid}/conversations/{cid}/chat",
        json=_chat_body(_user_message("Please run the demo action")),
    )
    assert resp.status_code == 200, resp.text
    chunks = _parse_stream(resp.text)
    approval_requests = [c for c in chunks if c["type"] == "tool-approval-request"]
    assert len(approval_requests) == 1, [c["type"] for c in chunks]
    assert approval_requests[0]["toolCallId"].endswith("demo_requires_approval")
    # the run ends without executing the gated tool
    outputs = {c["toolCallId"] for c in chunks if c["type"] == "tool-output-available"}
    assert approval_requests[0]["toolCallId"] not in outputs
    return pid, cid, _assistant_parts_from_stream(chunks)


async def test_approve_runs_the_gated_tool(client: httpx.AsyncClient, logged_in: LoggedIn) -> None:
    logged_in.apply(client)
    pid, cid, parts = await _first_turn(client)
    resp = await client.post(
        f"/api/projects/{pid}/conversations/{cid}/chat",
        json=_chat_body(
            _user_message("Please run the demo action"),
            {"id": "m2", "role": "assistant", "parts": _respond(parts, approved=True)},
        ),
    )
    assert resp.status_code == 200, resp.text
    chunks = _parse_stream(resp.text)
    output = next(
        c
        for c in chunks
        if c["type"] == "tool-output-available"
        and c["toolCallId"].endswith("demo_requires_approval")
    )
    assert output["output"]["summary"].startswith("Approved and performed")
    assert output["output"]["data"]["approved_by"] == logged_in.user.email
    assert "tool-output-denied" not in {c["type"] for c in chunks}
    assert chunks[-1]["type"] == "finish"


async def test_deny_skips_the_gated_tool(client: httpx.AsyncClient, logged_in: LoggedIn) -> None:
    logged_in.apply(client)
    pid, cid, parts = await _first_turn(client)
    resp = await client.post(
        f"/api/projects/{pid}/conversations/{cid}/chat",
        json=_chat_body(
            _user_message("Please run the demo action"),
            {"id": "m2", "role": "assistant", "parts": _respond(parts, approved=False)},
        ),
    )
    assert resp.status_code == 200, resp.text
    chunks = _parse_stream(resp.text)
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
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    logged_in.apply(client)
    pid, cid, parts = await _first_turn(client)
    tampered = _respond(parts, approved=True)
    for part in tampered:
        if part.get("state") == "approval-responded":
            part["approval"]["approved"] = "true"  # strings must not open the gate
    resp = await client.post(
        f"/api/projects/{pid}/conversations/{cid}/chat",
        json=_chat_body(
            _user_message("Please run the demo action"),
            {"id": "m2", "role": "assistant", "parts": tampered},
        ),
    )
    assert resp.status_code == 422
    assert "Approved and performed" not in resp.text
