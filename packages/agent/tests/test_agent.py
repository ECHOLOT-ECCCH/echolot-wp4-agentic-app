from __future__ import annotations

import json
from typing import Any, cast

from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from elexifier_agent.agent import build_agent, render_system_prompt, toolsets_for
from elexifier_agent.runtime import PydanticAIRuntime

from .conftest import Fixture


def test_system_prompt_mentions_project_and_dmlex(fixture: Fixture) -> None:
    prompt = render_system_prompt(fixture.deps)
    assert "Test dictionary" in prompt
    assert "legacy XML export" in prompt
    assert "DMLex in brief" in prompt
    assert "Ada" in prompt


def test_toolsets_by_project_type() -> None:
    assert [t for t in toolsets_for("xml")]
    assert [t for t in toolsets_for("pdf")]


def _tool_returns(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


async def test_agent_lists_files_with_scripted_model(fixture: Fixture) -> None:
    seen_tool_returns: list[dict[str, Any]] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        names = {t.name for t in info.function_tools}
        assert {"list_files", "read_file_head"} <= names
        seen_tool_returns[:] = _tool_returns(messages)
        if not seen_tool_returns:
            return ModelResponse(parts=[ToolCallPart(tool_name="list_files", args={})])
        if len(seen_tool_returns) == 1:
            files = seen_tool_returns[0]["data"]["files"]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read_file_head",
                        args={"file_id": files[0]["file_id"], "max_bytes": 64},
                    )
                ]
            )
        head = seen_tool_returns[1]["data"]
        return ModelResponse(
            parts=[TextPart(content=f"Your file starts with: {head['text'][:12]}")]
        )

    agent = build_agent("xml", FunctionModel(script))
    result = await agent.run("What files do I have?", deps=fixture.deps)
    assert result.output == "Your file starts with: <dictionary>"
    first, second = seen_tool_returns
    assert first["summary"].startswith("2 file(s) in project 'Test dictionary'")
    assert first["data"]["files"][0]["name"] == "dict.xml"
    assert second["data"]["truncated"] is True
    assert second["data"]["size_bytes"] > 64


async def test_read_file_head_rejects_foreign_and_bad_ids(fixture: Fixture) -> None:
    calls: list[dict[str, Any]] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls[:] = _tool_returns(messages)
        if len(calls) == 0:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="read_file_head", args={"file_id": "nope"})]
            )
        if len(calls) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read_file_head",
                        args={"file_id": "00000000-0000-0000-0000-000000000000"},
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="done")])

    agent = build_agent("xml", FunctionModel(script))
    result = await agent.run("peek", deps=fixture.deps)
    assert result.output == "done"
    assert "not a valid id" in calls[0]["summary"]
    assert "no such file" in calls[1]["summary"]


async def test_test_model_runs_end_to_end(fixture: Fixture) -> None:
    agent = build_agent("xml", TestModel())
    result = await agent.run("hello", deps=fixture.deps)
    assert isinstance(result.output, str | DeferredToolRequests)
    assert result.usage.requests >= 1


def test_runtime_builds_agent(fixture: Fixture) -> None:
    runtime = PydanticAIRuntime()
    agent = runtime.build(fixture.deps, TestModel())
    assert agent.name == "elexifier"
    assert json.dumps({"ok": True})  # runtime module imports cleanly with the Vercel adapter
