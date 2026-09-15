"""The runtime boundary between the API and the agent engine (docs/PLAN.md decision "Agent core").

Everything the API needs from an agent engine goes through ``AgentRuntime`` so the engine can be
swapped (another Pydantic AI configuration, DeepSeek Harness, ...) without touching routers or UI.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, cast

import structlog
from pydantic_ai import DeferredToolResults
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk
from starlette.requests import Request
from starlette.responses import Response

from elexifier_agent.agent import ConversionAgent, build_agent
from elexifier_agent.deps import AgentDeps

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
    ) -> Response: ...


class PydanticAIRuntime:
    """Runs the conversion agent and streams the Vercel AI data-stream protocol (AI SDK 7)."""

    sdk_version: ClassVar[Literal[5, 6, 7]] = 7

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def build(self, deps: AgentDeps, model: Model) -> ConversionAgent:
        return build_agent(
            deps.project.source_type, model, dev_tools=deps.settings.elx_enable_test_model
        )

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
    ) -> Response:
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
                run_result.new_messages(), sdk_version=self.sdk_version
            )
            summary = RunSummary(
                messages_json=run_result.all_messages_json(),
                usage={
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
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

        adapter: VercelAIAdapter[AgentDeps, Any] = await VercelAIAdapter.from_request(
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
