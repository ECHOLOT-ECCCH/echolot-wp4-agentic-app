"""Server test fixtures: settings on a temporary data directory, the app, an ASGI client."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
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
