"""service_call: one request to a service through the runner, approved on a card, with the
reply returned to the agent and nothing left in the workspace's runs, scripts or files."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from echolot_app.db import File, Run, RunStatus, Script, ServiceCall
from echolot_app.runs.calls import call_input
from echolot_app.runs.tokens import new_token

from .conftest import Live, call_gated_tool


async def _count(live: Live, model: type) -> int:
    async with live.state.db.session() as db:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_an_approved_call_returns_the_reply_and_leaves_no_run(live: Live) -> None:
    files_before = await _count(live, File)

    result = await call_gated_tool(
        live.deps(),
        "service_call",
        {"service": "upper", "path": "/", "body": {"name": "ana"}, "purpose": "try one name"},
    )

    assert result is not None
    assert result["summary"] == "upper POST / answered"
    assert result["data"]["reply"] == {"name": "ANA"}
    assert await _count(live, Run) == 0
    assert await _count(live, Script) == 0
    assert await _count(live, File) == files_before
    async with live.state.db.session() as db:
        call = (await db.execute(select(ServiceCall))).scalar_one()
    assert call.status == RunStatus.DONE.value
    assert call.purpose == "try one name"
    assert call.request == {"method": "POST", "path": "/", "params": {}, "body": {"name": "ana"}}
    assert call.finished_at is not None


async def test_a_get_with_query_values(live: Live) -> None:
    result = await call_gated_tool(
        live.deps(),
        "service_call",
        {
            "service": "upper",
            "path": "/look",
            "method": "GET",
            "params": {"algorithm": "rule_based", "n": 3},
            "purpose": "a lookup",
        },
    )

    assert result is not None
    query = {"algorithm": "rule_based", "n": "3"}  # query values travel as text
    assert result["data"]["reply"] == {"path": "/look", "query": query}


async def test_a_service_error_comes_back_as_a_message(live: Live) -> None:
    result = await call_gated_tool(
        live.deps(),
        "service_call",
        {"service": "upper", "path": "/", "body": {"name": "boom"}, "purpose": "the bad one"},
    )

    assert result is not None
    assert result["summary"] == (
        "Error: upper POST / failed: "
        "ServiceError: 502 from upper: upstream failed (after 2 attempts)"
    )
    async with live.state.db.session() as db:
        call = (await db.execute(select(ServiceCall))).scalar_one()
    assert call.status == RunStatus.DONE.value  # the runner finished; the request failed


async def test_a_denied_call_is_not_sent(live: Live) -> None:
    result = await call_gated_tool(
        live.deps(),
        "service_call",
        {"service": "upper", "path": "/", "body": {"name": "ana"}, "purpose": "x"},
        approve=False,
    )

    assert result is None
    assert await _count(live, ServiceCall) == 0


async def test_bad_arguments_are_refused_before_anything_is_sent(live: Live) -> None:
    cases = [
        ({"service": "nope", "path": "/"}, "Error: no service 'nope'; known: upper"),
        (
            {"service": "upper", "path": "http://elsewhere.example/x"},
            "Error: the path must be an endpoint such as /reconcile, not 'http://elsewhere.example/x'",
        ),
        (
            {"service": "upper", "path": "/", "method": "GET", "body": {"a": 1}},
            "Error: a GET request has no body; send it as POST or put values in params",
        ),
    ]
    for args, summary in cases:
        result = await call_gated_tool(live.deps(), "service_call", {**args, "purpose": "x"})
        assert result is not None and result["summary"] == summary
    assert await _count(live, ServiceCall) == 0


async def test_the_call_endpoints_need_the_call_token(live: Live) -> None:
    token, hashed = new_token()
    async with live.state.db.session() as db:
        call = ServiceCall(
            workspace_id=uuid.UUID(live.workspace_id),
            service="upper",
            request={"method": "POST", "path": "/", "params": {}, "body": {"name": "ana"}},
            status=RunStatus.QUEUED.value,
            executor="local",
            token_hash=hashed,
        )
        db.add(call)
        await db.commit()
        call_id = call.id
        expected = call_input(call)
    url = f"/runner/calls/{call_id}/input"

    assert (await live.client.get(url)).status_code == 404
    wrong = {"Authorization": "Bearer wrong"}
    assert (await live.client.get(url, headers=wrong)).status_code == 404
    other = f"/runner/calls/{uuid.uuid4()}/input"
    right = {"Authorization": f"Bearer {token}"}
    assert (await live.client.get(other, headers=right)).status_code == 404
    response = await live.client.get(url, headers=right)
    assert response.status_code == 200
    assert response.content == expected
    assert b'"record": {"service": "upper", "method": "POST"' in expected

    async with live.state.db.session() as db:
        done = await db.get(ServiceCall, call_id)
        assert done is not None
        done.status = RunStatus.DONE.value
        await db.commit()
    assert (await live.client.get(url, headers=right)).status_code == 404  # over: closed
