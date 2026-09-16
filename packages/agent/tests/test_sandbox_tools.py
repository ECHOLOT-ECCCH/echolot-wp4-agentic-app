"""run_code through a scripted model and a fake runner: approval, inputs, artifacts, errors."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import select

from elexifier_agent.agent import build_agent, render_system_prompt
from elexifier_agent.sandbox import RunnerClient
from elexifier_core.db.models import AuditLog, File

from .conftest import Fixture

Behaviour = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | httpx.Response]]


def fake_runner(behaviour: Behaviour) -> RunnerClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-runner-token") != "tok":
            return httpx.Response(401, json={"detail": "invalid runner token"})
        result = await behaviour(json.loads(request.content))
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)

    return RunnerClient("http://runner", "tok", transport=httpx.MockTransport(handler))


async def approved_call(fixture: Fixture, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a gated tool: the first run stops for approval, the second runs it."""
    returns: list[Any] = []

    def script(messages, info: AgentInfo) -> ModelResponse:  # type: ignore[no-untyped-def]
        last = [
            part
            for part in messages[-1].parts
            if isinstance(part, ToolReturnPart) and part.tool_name == name
        ]
        if last:
            returns.append(last[0].content)
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])

    agent = build_agent(fixture.deps.project.source_type, FunctionModel(script))
    first = await agent.run("go", deps=fixture.deps)
    assert isinstance(first.output, DeferredToolRequests)
    assert [c.tool_name for c in first.output.approvals] == [name]
    second = await agent.run(
        deps=fixture.deps,
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={first.output.approvals[0].tool_call_id: True}
        ),
    )
    assert second.output == "done"
    return returns[0]


async def test_run_code_needs_approval_then_runs_and_keeps_artifacts(fixture: Fixture) -> None:
    assert "run_code" in render_system_prompt(fixture.deps)
    seen: list[dict[str, Any]] = []

    async def behaviour(body: dict[str, Any]) -> dict[str, Any]:
        seen.append(body)
        key = f"projects/{body['project_id']}/artifacts/{body['run_id']}/upper.txt"
        stat = await fixture.storage.put(key, b"ABACUS", content_type="text/plain")
        return {
            "exit_code": 0,
            "stdout": "6 letters\n",
            "stderr": "",
            "truncated": False,
            "duration_s": 0.3,
            "outputs": [{"s3_key": key, "name": "upper.txt", "size": 6, "sha256": stat.sha256}],
            "killed": False,
        }

    fixture.deps.runner = fake_runner(behaviour)
    result = await approved_call(
        fixture,
        "run_code",
        {
            "code": "print('6 letters')\nopen('out/upper.txt', 'w').write('ABACUS')\n",
            "purpose": "Upper-case the first headword",
            "inputs": ["source"],
            "timeout_s": 30,
        },
    )
    assert result["summary"].startswith(
        "Snippet finished with exit code 0 in 0.3 s; 1 output file (upper.txt, 6 B). "
        "stdout: 6 letters"
    )
    data = result["data"]
    assert data["exit_code"] == 0 and data["outputs"][0]["name"] == "upper.txt"
    assert data["inputs"] == [
        {
            "file_id": data["inputs"][0]["file_id"],
            "name": "oxford.xml",
            "mount_path": "in/oxford.xml",
        }
    ]
    assert [a["kind"] for a in result["artifacts"]] == ["code", "report", "log"]
    request = seen[0]
    assert request["limits"]["timeout_s"] == 30 and request["language"] == "python"
    assert request["inputs"][0]["mount_path"] == "in/oxford.xml"
    assert request["inputs"][0]["s3_key"].endswith("/oxford.xml")

    async with fixture.db.session() as db:
        rows = (
            (await db.execute(select(File).where(File.project_id == fixture.project_id)))
            .scalars()
            .all()
        )
        mine = {r.name: r for r in rows if r.meta.get("code_run") == data["code_run_id"]}
        short = data["code_run_id"][:8]
        assert set(mine) == {
            f"code-{short}.py",
            "upper.txt",
            f"log-{short}.txt",
            f"step-{short}.json",
        }
        assert all(r.kind == "artifact" for r in mine.values())
        assert mine["upper.txt"].size == 6 and mine["upper.txt"].mime == "text/plain"
        assert mine["upper.txt"].sha256 and mine["upper.txt"].meta["role"] == "output"
        assert str(mine[f"code-{short}.py"].id) == data["code_file_id"]
        code = b"".join([c async for c in fixture.storage.open(mine[f"code-{short}.py"].s3_key)])
        assert code.startswith(b"print('6 letters')")
        step = json.loads(
            b"".join([c async for c in fixture.storage.open(mine[f"step-{short}.json"].s3_key)])
        )
        assert step["purpose"] == "Upper-case the first headword"
        assert step["inputs"][0]["mount_path"] == "in/oxford.xml" and step["exit_code"] == 0
        audit = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "sandbox.run")))
            .scalars()
            .all()
        )
        assert len(audit) == 1 and audit[0].meta["purpose"] == "Upper-case the first headword"
        assert audit[0].target == data["code_run_id"]


async def test_run_code_resolves_inputs_and_reports_runner_trouble(fixture: Fixture) -> None:
    calls: list[dict[str, Any]] = []

    async def refusing(body: dict[str, Any]) -> httpx.Response:
        calls.append(body)
        return httpx.Response(503, json={"detail": "sandbox image 'x' is missing"})

    fixture.deps.runner = fake_runner(refusing)
    unknown = await approved_call(
        fixture, "run_code", {"code": "print(1)", "purpose": "p", "inputs": ["not-an-id"]}
    )
    assert unknown["summary"].startswith("Error: input 'not-an-id' is neither a file id")
    missing = await approved_call(
        fixture, "run_code", {"code": "print(1)", "purpose": "p", "inputs": ["latest_export"]}
    )
    assert missing["summary"].startswith("Error: there is no export yet")
    empty = await approved_call(fixture, "run_code", {"code": "   \n", "purpose": "p"})
    assert empty["summary"] == "Error: code is empty"
    assert calls == []  # nothing reached the runner so far

    refused = await approved_call(
        fixture,
        "run_code",
        {"code": "print(1)", "purpose": "p", "inputs": [str(fixture.file_id), "source"]},
    )
    assert refused["summary"].startswith(
        "Error: the sandbox runner refused the run (503): sandbox image 'x' is missing"
    )
    assert refused["data"]["code_file_id"]  # the snippet was kept even though the run failed
    assert [i["mount_path"] for i in calls[0]["inputs"]] == ["in/dict.xml", "in/oxford.xml"]

    async def unreachable(body: dict[str, Any]) -> dict[str, Any]:
        raise httpx.ConnectError("refused")

    fixture.deps.runner = fake_runner(unreachable)
    down = await approved_call(fixture, "run_code", {"code": "print(1)", "purpose": "p"})
    assert down["summary"].startswith("Error: cannot reach the sandbox runner at http://runner")

    fixture.deps.runner = None
    off = await approved_call(fixture, "run_code", {"code": "print(1)", "purpose": "p"})
    assert off["summary"].startswith("Error: the sandbox is not configured")


async def test_killed_and_failed_runs_are_reported(fixture: Fixture) -> None:
    async def killed(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "exit_code": 137,
            "stdout": "start\n",
            "stderr": "[runner] killed after 1 s (timeout)",
            "truncated": False,
            "duration_s": 1.02,
            "outputs": [],
            "killed": True,
        }

    fixture.deps.runner = fake_runner(killed)
    result = await approved_call(
        fixture, "run_code", {"code": "while True: pass", "purpose": "loop", "timeout_s": 1}
    )
    assert result["summary"].startswith(
        "Snippet was killed at the timeout after 1.0 s; 0 output files. stdout: start stderr:"
    )
    assert result["data"]["killed"] is True and result["data"]["exit_code"] == 137
    assert [a["kind"] for a in result["artifacts"]] == ["code", "log"]
    assert "ui_actions" not in result  # nothing to open: no outputs
