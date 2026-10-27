"""The runtime boundary between the API and the agent engine.

Everything the API needs from the agent goes through ``AgentRuntime``: a turn runs in its own task
and streams the Vercel AI data-stream protocol (AI SDK 7); persistence happens in callbacks.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, cast

import structlog
from pydantic_ai import DeferredToolResults
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk
from starlette.requests import Request
from starlette.responses import Response

from echolot_app.agent.agent import WorkbenchAgent, build_agent
from echolot_app.agent.deps import AgentDeps

log = structlog.get_logger(__name__)


@dataclass
class RunSummary:
    """What the API persists after a turn."""

    messages_json: bytes
    usage: dict[str, Any]
    model_name: str
    new_ui_messages: list[dict[str, Any]]
    """The messages produced in this run (assistant side) in AI SDK UIMessage form."""


class AgentRuntime(Protocol):
    async def drain(self, timeout_s: float = 30.0) -> None: ...

    async def dispatch_chat(
        self,
        request: Request,
        *,
        deps: AgentDeps,
        model: Model,
        on_complete: Callable[[RunSummary], Any] | None = None,
        on_cancel: Callable[[str], Any] | None = None,
        message_history: list[ModelMessage] | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        body: dict[str, Any] | None = None,
    ) -> Response: ...


class PydanticAIRuntime:
    """Runs the workbench agent and streams the Vercel AI data-stream protocol (AI SDK 7)."""

    sdk_version: ClassVar[Literal[5, 6, 7]] = 7

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def build(self, deps: AgentDeps, model: Model) -> WorkbenchAgent:
        return build_agent(model, dev_tools=deps.settings.enable_test_model)

    async def dispatch_chat(
        self,
        request: Request,
        *,
        deps: AgentDeps,
        model: Model,
        on_complete: Callable[[RunSummary], Any] | None = None,
        on_cancel: Callable[[str], Any] | None = None,
        message_history: list[ModelMessage] | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        body: dict[str, Any] | None = None,
    ) -> Response:
        """Run a turn. ``body`` replaces the request's JSON when the caller has normalised it."""
        agent = self.build(deps, model)
        completed = False

        async def _on_cancel(reason: Any) -> None:
            if on_cancel is None:
                return
            outcome = on_cancel(str(reason))
            if hasattr(outcome, "__await__"):
                await outcome

        async def _on_complete(run_result: Any, *_: Any) -> Any:
            nonlocal completed
            completed = True
            if on_complete is None:
                return None
            usage = run_result.usage
            ui_messages = VercelAIAdapter.dump_messages(
                with_orphan_tool_calls(run_result.new_messages(), run_result.all_messages()),
                sdk_version=self.sdk_version,
            )
            summary = RunSummary(
                messages_json=run_result.all_messages_json(),
                usage={
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": getattr(usage, "cache_read_tokens", 0),
                    "cache_write_tokens": getattr(usage, "cache_write_tokens", 0),
                    "requests": usage.requests,
                    "tool_calls": usage.tool_calls,
                },
                model_name=model.model_name,
                new_ui_messages=[
                    m.model_dump(by_alias=True, exclude_none=True, mode="json")
                    for m in ui_messages
                    if m.role != "user"
                ],
            )
            outcome = on_complete(summary)
            if hasattr(outcome, "__await__"):
                await outcome
            return None

        adapter: VercelAIAdapter[AgentDeps, Any]
        if body is not None:
            adapter = VercelAIAdapter(
                agent=cast(Any, agent),
                run_input=VercelAIAdapter.build_run_input(json.dumps(body).encode("utf-8")),
                accept=request.headers.get("accept"),
                sdk_version=self.sdk_version,
            )
        else:
            adapter = await VercelAIAdapter.from_request(
                request, agent=cast(Any, agent), sdk_version=self.sdk_version
            )
        events = adapter.run_stream(
            deps=deps,
            message_history=message_history,
            deferred_tool_results=deferred_tool_results,
            on_complete=_on_complete,
        )

        # The agent runs in its own task and feeds a queue. If the browser disconnects, the
        # response consumer is cancelled but the run still finishes and on_complete persists it.
        queue: asyncio.Queue[BaseChunk | None] = asyncio.Queue()

        async def produce() -> None:
            failed: str | None = None
            try:
                async for event in events:
                    if getattr(event, "type", "") == "error":
                        failed = str(getattr(event, "error_text", "error"))
                    queue.put_nowait(event)
            except Exception as exc:
                failed = f"{type(exc).__name__}: {exc}"
                log.exception("agent.run_failed")
            finally:
                try:
                    if failed is not None:
                        await _on_cancel(f"error: {failed}")
                    elif not completed:
                        await _on_cancel("run ended before completion")
                finally:
                    queue.put_nowait(None)

        task = asyncio.create_task(produce(), name="agent-run")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        async def consume() -> AsyncIterator[BaseChunk]:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        return adapter.streaming_response(consume())

    async def drain(self, timeout_s: float = 30.0) -> None:
        """Wait for in-flight runs (call at shutdown)."""
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout_s)


__all__ = ["AgentRuntime", "BaseChunk", "PydanticAIRuntime", "RunSummary"]


def with_orphan_tool_calls(
    new_messages: list[ModelMessage], all_messages: list[ModelMessage]
) -> list[ModelMessage]:
    """A continuation after an approval starts with the tool's return; its call sits in the
    history. ``dump_messages`` drops such returns, so put the matching calls in front."""
    if not new_messages or not isinstance(new_messages[0], ModelRequest):
        return new_messages
    returns = [p for p in new_messages[0].parts if isinstance(p, ToolReturnPart)]
    if not returns:
        return new_messages
    wanted = {r.tool_call_id for r in returns}
    known_calls = {
        p.tool_call_id
        for m in new_messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    }
    missing = wanted - known_calls
    if not missing:
        return new_messages
    calls: list[ToolCallPart] = []
    for message in all_messages:
        if message in new_messages or not isinstance(message, ModelResponse):
            continue
        calls.extend(
            p for p in message.parts if isinstance(p, ToolCallPart) and p.tool_call_id in missing
        )
    if not calls:
        return new_messages
    return [ModelResponse(parts=list(calls)), *new_messages]
