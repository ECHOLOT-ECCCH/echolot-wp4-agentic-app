"""The runtime boundary between the API and the agent engine (docs/PLAN.md decision "Agent core").

Everything the API needs from an agent engine goes through ``AgentRuntime`` so the engine can be
swapped (another Pydantic AI configuration, DeepSeek Harness, ...) without touching routers or UI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol

from pydantic_ai import DeferredToolResults
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk
from starlette.requests import Request
from starlette.responses import Response

from elexifier_agent.agent import ConversionAgent, build_agent
from elexifier_agent.deps import AgentDeps


@dataclass
class RunSummary:
    """What the API persists after a turn."""

    messages_json: bytes
    usage: dict[str, Any]
    model_name: str


class AgentRuntime(Protocol):
    async def dispatch_chat(
        self,
        request: Request,
        *,
        deps: AgentDeps,
        model: Model,
        on_complete: Callable[[RunSummary], Any] | None = None,
        message_history: list[ModelMessage] | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
    ) -> Response: ...


class PydanticAIRuntime:
    """Runs the conversion agent and streams the Vercel AI data-stream protocol (AI SDK 7)."""

    sdk_version: ClassVar[Literal[5, 6, 7]] = 7

    def build(self, deps: AgentDeps, model: Model) -> ConversionAgent:
        return build_agent(deps.project.source_type, model)

    async def dispatch_chat(
        self,
        request: Request,
        *,
        deps: AgentDeps,
        model: Model,
        on_complete: Callable[[RunSummary], Any] | None = None,
        message_history: list[ModelMessage] | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
    ) -> Response:
        agent = self.build(deps, model)

        async def _on_complete(run_result: Any, *_: Any) -> Any:
            if on_complete is None:
                return None
            usage = run_result.usage
            summary = RunSummary(
                messages_json=run_result.all_messages_json(),
                usage={
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "requests": usage.requests,
                    "tool_calls": usage.tool_calls,
                },
                model_name=model.model_name,
            )
            outcome = on_complete(summary)
            if hasattr(outcome, "__await__"):
                await outcome
            return None

        return await VercelAIAdapter.dispatch_request(
            request,
            agent=agent,
            deps=deps,
            sdk_version=self.sdk_version,
            message_history=message_history,
            deferred_tool_results=deferred_tool_results,
            on_complete=_on_complete if on_complete else None,
        )


__all__ = ["AgentRuntime", "BaseChunk", "PydanticAIRuntime", "RunSummary"]
