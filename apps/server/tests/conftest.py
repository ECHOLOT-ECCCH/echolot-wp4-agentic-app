"""Server test fixtures: settings on a temporary data directory, the app, an ASGI client."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from echolot_app.agent.agent import build_agent
from echolot_app.agent.deps import AgentDeps, WorkspaceContext
from echolot_app.main import create_app
from echolot_app.settings import Settings
from echolot_app.state import AppState


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        model_provider="test",
        model_id="test",
        enable_test_model=True,
        public_url="http://testserver",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
def state(app: FastAPI) -> AppState:
    return app.state.app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@dataclass
class Workspace:
    id: uuid.UUID
    deps: AgentDeps


@pytest.fixture
async def workspace(client: httpx.AsyncClient, state: AppState) -> Workspace:
    """A workspace through the API, with the agent's dependencies for it."""
    created = (await client.post("/api/workspaces", json={"name": "Heritage"})).json()
    wid = uuid.UUID(created["id"])
    deps = AgentDeps(
        workspace=WorkspaceContext(id=wid, name="Heritage"),
        session_factory=state.db.session_factory,
        storage=state.storage,
        settings=state.settings,
        executor=state.executor,
    )
    return Workspace(id=wid, deps=deps)


async def upload(client: httpx.AsyncClient, wid: uuid.UUID, name: str, data: bytes) -> str:
    response = await client.post(
        f"/api/workspaces/{wid}/files", params={"name": name}, content=data
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def call_tool(deps: AgentDeps, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call through a scripted model and return its result dict."""
    captured: list[dict[str, Any]] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert any(t.name == name for t in info.function_tools), f"tool {name} not registered"
        for message in messages:
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == name:
                    captured.append(cast(dict[str, Any], part.content))
        if not captured:
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])
        return ModelResponse(parts=[TextPart(content="done")])

    agent = build_agent(FunctionModel(script))
    result = await agent.run(f"call {name}", deps=deps)
    assert result.output == "done"
    assert len(captured) == 1
    return captured[0]


async def call_gated_tool(
    deps: AgentDeps, name: str, args: dict[str, Any], *, approve: bool = True
) -> dict[str, Any] | None:
    """A tool that needs approval: the first run stops with the request, the second resumes it
    with the decision. Returns the tool's result, or None when denied."""

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returned = [
            p
            for m in messages
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_name == name
        ]
        if not returned:
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])
        return ModelResponse(parts=[TextPart(content="done")])

    agent = build_agent(FunctionModel(script))
    first = await agent.run(f"call {name}", deps=deps)
    assert isinstance(first.output, DeferredToolRequests), first.output
    [request] = first.output.approvals
    assert request.tool_name == name
    second = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(approvals={request.tool_call_id: approve}),
        deps=deps,
    )
    assert second.output == "done"
    if not approve:
        return None  # the model sees "The tool call was denied."; the tool never ran
    for message in second.new_messages():
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == name:
                return cast(dict[str, Any], part.content)
    return None


UPPER = """
def process(record, ctx):
    reply = ctx.service("upper").post("/", json={"name": record["name"]})
    return {"upper": reply["name"], "length": len(reply["name"])}
"""


class FakeService(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        name = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["name"]
        if name == "boom":
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b"upstream failed")
            return
        body = json.dumps({"name": name.upper()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_service() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeService)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


@dataclass
class Live:
    """The app on a real port (the runner subprocess calls it back) with a workspace holding a
    50-row CSV whose row 17 makes the fake service answer 502."""

    client: httpx.AsyncClient
    settings: Settings
    state: AppState
    workspace_id: str
    file_id: str

    def deps(self) -> AgentDeps:
        return AgentDeps(
            workspace=WorkspaceContext(id=uuid.UUID(self.workspace_id), name="Runs"),
            session_factory=self.state.db.session_factory,
            storage=self.state.storage,
            settings=self.settings,
            executor=self.state.executor,
        )

    async def run(self, **body: Any) -> dict[str, Any]:
        response = await self.client.post(
            f"/api/workspaces/{self.workspace_id}/runs", json={"file_id": self.file_id, **body}
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def wait(self, run_id: str, timeout_s: float = 60) -> dict[str, Any]:
        run: dict[str, Any] = {}
        for _ in range(int(timeout_s / 0.1)):
            run = (
                await self.client.get(f"/api/workspaces/{self.workspace_id}/runs/{run_id}")
            ).json()
            if run["status"] in ("done", "failed", "cancelled"):
                return run
            await asyncio.sleep(0.1)
        raise AssertionError(f"run {run_id} did not finish: {run}")

    async def script(self, code: str) -> None:
        response = await self.client.post(
            f"/api/workspaces/{self.workspace_id}/scripts", json={"code": code}
        )
        assert response.status_code == 201, response.text


@pytest.fixture
async def live(tmp_path: Path, fake_service: str) -> AsyncIterator[Live]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    services = tmp_path / "services.toml"
    services.write_text(
        f'[upper]\nurl = "{fake_service}"\nretries = 2\nbackoff_s = 0\nretry_statuses = [502]\n',
        encoding="utf-8",
    )
    docs = tmp_path / "services"
    docs.mkdir()
    (docs / "upper.md").write_text(
        "---\nname: upper\ndescription: Upper-cases a name\n---\n"
        '# upper\nPOST / with {"name": "..."}; answers {"name": "NAME"}. 502 on "boom".\n',
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        model_provider="test",
        model_id="test",
        enable_test_model=True,
        services_dir=docs,
        public_url=f"http://127.0.0.1:{port}",
        local_services_file=services,
        run_poll_interval_s=0.1,
        log_level="WARNING",
    )
    application = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(application, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(500):  # uvicorn exposes no event for "started"
        if server.started:
            break
        await asyncio.sleep(0.02)
    async with httpx.AsyncClient(base_url=settings.public_url, timeout=30) as client:
        wid = (await client.post("/api/workspaces", json={"name": "Runs"})).json()["id"]
        names = [f"person {i}" for i in range(50)]
        names[17] = "boom"
        data = ("name,born\n" + "".join(f"{n},{1900 + i}\n" for i, n in enumerate(names))).encode()
        fid = (
            await client.post(
                f"/api/workspaces/{wid}/files", params={"name": "p.csv"}, content=data
            )
        ).json()["id"]
        yield Live(
            client=client,
            settings=settings,
            state=application.state.app,
            workspace_id=wid,
            file_id=fid,
        )
    server.should_exit = True
    await task
